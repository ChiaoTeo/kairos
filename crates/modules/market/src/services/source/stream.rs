//! Wake-driven driver for Integration live market capabilities.

use kairos_integration::application::{
    AsyncMarketEventSource, IntegrationError, MarketEventKind, MarketSubscription,
    SubscriptionId as IntegrationSubscriptionId,
};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use tokio::sync::mpsc;

use super::normalization::{normalize, with_epoch, Normalized};
use super::recovery::{reconnectable, recover_connection};

use super::messages::{ProviderSubscriptionId, SourceCommand, SourceInput, SourceRequestId};
use super::SourceHandle;
use crate::domain::market::ResolvedMarket;
use crate::domain::source::{
    SourceDescriptor, SourceEpoch, SourceFailureKind, SourceId, SourceStatus,
};

const COMMAND_CAPACITY: usize = 1_024;

#[derive(Clone, Copy)]
pub(crate) enum StreamFailurePolicy {
    StopSource,
    MarketScopedResync,
}

pub(crate) fn spawn_stream<C>(
    descriptor: SourceDescriptor,
    connection: C,
    input_capacity: usize,
) -> SourceHandle
where
    C: AsyncMarketEventSource + 'static,
{
    spawn_stream_with_policy(
        descriptor,
        connection,
        input_capacity,
        StreamFailurePolicy::StopSource,
    )
}

pub(crate) fn spawn_stream_with_policy<C>(
    descriptor: SourceDescriptor,
    connection: C,
    input_capacity: usize,
    failure_policy: StreamFailurePolicy,
) -> SourceHandle
where
    C: AsyncMarketEventSource + 'static,
{
    let (commands, command_receiver) = mpsc::channel(COMMAND_CAPACITY);
    let (input_sender, inputs) = mpsc::channel(input_capacity);
    let task_descriptor = descriptor.clone();
    let task = tokio::spawn(run(
        task_descriptor,
        connection,
        command_receiver,
        input_sender,
        failure_policy,
    ));
    SourceHandle {
        descriptor,
        commands,
        inputs,
        task,
    }
}

async fn run<C>(
    descriptor: SourceDescriptor,
    mut connection: C,
    mut commands: mpsc::Receiver<SourceCommand>,
    inputs: mpsc::Sender<SourceInput>,
    failure_policy: StreamFailurePolicy,
) where
    C: AsyncMarketEventSource,
{
    let source_id = descriptor.id;
    let mut epoch = SourceEpoch::new(1);
    let mut deferred_commands = VecDeque::new();
    match connection.connect_channel().await {
        Ok(()) => {
            if status(&inputs, &source_id, epoch, SourceStatus::Ready)
                .await
                .is_err()
            {
                return;
            }
        }
        Err(error)
            if reconnectable(&error)
                && recover_connection(
                    &mut connection,
                    &mut commands,
                    &mut deferred_commands,
                    &inputs,
                    &source_id,
                    &mut epoch,
                    failure_kind(&error),
                    error.to_string(),
                )
                .await => {}
        Err(error) => {
            fail(
                &inputs,
                &source_id,
                epoch,
                failure_kind(&error),
                error.to_string(),
            )
            .await;
            return;
        }
    }
    let mut markets = BTreeMap::<IntegrationSubscriptionId, ResolvedMarket>::new();
    let mut resyncing = BTreeMap::<kairos_primitives::MarketId, SourceRequestId>::new();
    let mut blocked_markets = BTreeSet::<kairos_primitives::MarketId>::new();
    loop {
        tokio::select! {
            command = async {
                match deferred_commands.pop_front() {
                    Some(command) => Some(command),
                    None => commands.recv().await,
                }
            } => {
                let Some(command) = command else { return };
                match command {
                    SourceCommand::Subscribe { request_id, market } => {
                        subscribe(&mut connection, &mut markets, &inputs, &source_id, epoch, request_id, *market).await;
                    }
                    SourceCommand::Unsubscribe { request_id, handle } => {
                        unsubscribe(&mut connection, &mut markets, &inputs, &source_id, epoch, request_id, handle).await;
                    }
                    SourceCommand::ResyncOrderBook { request_id, market } => {
                        resync(
                            &mut connection,
                            &mut markets,
                            &mut resyncing,
                            &inputs,
                            &source_id,
                            epoch,
                            request_id,
                            *market,
                        ).await;
                    }
                    SourceCommand::Pause | SourceCommand::Resume => {}
                    SourceCommand::Reconnect => {
                        let _ = status(&inputs, &source_id, epoch, SourceStatus::Reconnecting).await;
                        match connection.reconnect_channel().await {
                            Ok(()) => {
                                epoch.advance();
                                let _ = status(&inputs, &source_id, epoch, SourceStatus::Ready).await;
                            }
                            Err(error) => {
                                fail(&inputs, &source_id, epoch, failure_kind(&error), error.to_string()).await;
                                return;
                            }
                        }
                    }
                    SourceCommand::Shutdown => {
                        let _ = connection.disconnect_channel().await;
                        let _ = status(&inputs, &source_id, epoch, SourceStatus::Stopped).await;
                        return;
                    }
                }
            }
            result = connection.next_market_event(), if !markets.is_empty() => {
                let event = match result {
                    Ok(event) => event,
                    Err(IntegrationError::ResyncRequired(reason))
                        if matches!(failure_policy, StreamFailurePolicy::MarketScopedResync) => {
                        let affected = markets
                            .values()
                            .filter(|market| {
                                reason.contains(market.route.provider_symbol.as_str())
                            })
                            .cloned()
                            .collect::<Vec<_>>();
                        if affected.is_empty() {
                            fail(&inputs, &source_id, epoch, SourceFailureKind::ResyncRequired, reason).await;
                            return;
                        }
                        for market in affected {
                            let Some(market_id) = market.market_id().cloned() else {
                                fail(&inputs, &source_id, epoch, SourceFailureKind::InvalidPayload,
                                    "consolidated source cannot perform market-scoped order-book resync".into()).await;
                                return;
                            };
                            blocked_markets.insert(market_id);
                            if inputs.send(SourceInput::ResyncRequired {
                                source_id: source_id.clone(),
                                epoch,
                                market: Box::new(market),
                                reason: reason.clone(),
                            }).await.is_err() { return; }
                        }
                        continue;
                    }
                    Err(error) => {
                        if !reconnectable(&error)
                            || !recover_connection(
                                &mut connection,
                                &mut commands,
                                &mut deferred_commands,
                                &inputs,
                                &source_id,
                                &mut epoch,
                                failure_kind(&error),
                                error.to_string(),
                            ).await
                        {
                            return;
                        }
                        continue;
                    }
                };
                let Some(market) = markets.values().find(|market| {
                    market.route.provider_symbol.eq_ignore_ascii_case(event.symbol.as_str())
                }) else { continue };
                if market.market_id().is_some_and(|id| blocked_markets.contains(id))
                    && event.kind != MarketEventKind::BookSnapshot
                {
                    continue;
                }
                match normalize(&source_id, market, event) {
                    Ok(Some(input)) => {
                        let completed = match &input {
                            Normalized::OrderBook(update) if update.snapshot => {
                                blocked_markets.remove(&update.market_id);
                                resyncing.remove(&update.market_id).map(|request_id| {
                                    (request_id, update.market_id.clone())
                                })
                            }
                            _ => None,
                        };
                        let input = with_epoch(input, source_id.clone(), epoch);
                        if let Err(error) = inputs.try_send(input) {
                            match error {
                                mpsc::error::TrySendError::Closed(_) => return,
                                mpsc::error::TrySendError::Full(SourceInput::OrderBook { update, .. }) => {
                                    let market = update.market;
                                    let Some(market_id) = market.market_id().cloned() else {
                                        fail(&inputs, &source_id, epoch, SourceFailureKind::InvalidPayload,
                                            "consolidated source emitted an order book".into()).await;
                                        return;
                                    };
                                    blocked_markets.insert(market_id);
                                    let _ = inputs.send(SourceInput::ResyncRequired {
                                        source_id: source_id.clone(),
                                        epoch,
                                        market,
                                        reason: "source order-book input queue overflowed".into(),
                                    }).await;
                                    continue;
                                }
                                mpsc::error::TrySendError::Full(_) => {
                                    fail(&inputs, &source_id, epoch, SourceFailureKind::Backpressure, "source observation input queue overflowed".into()).await;
                                    let _ = connection.disconnect_channel().await;
                                    return;
                                }
                            }
                        }
                        if let Some((request_id, market_id)) = completed {
                            if inputs.send(SourceInput::ResyncCompleted {
                                source_id: source_id.clone(),
                                epoch,
                                request_id,
                                market_id,
                            }).await.is_err() { return; }
                        }
                    }
                    Ok(None) => {}
                    Err(error) => {
                        fail(&inputs, &source_id, epoch, SourceFailureKind::InvalidPayload, error).await;
                        return;
                    }
                }
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
async fn resync<C: AsyncMarketEventSource>(
    connection: &mut C,
    markets: &mut BTreeMap<IntegrationSubscriptionId, ResolvedMarket>,
    resyncing: &mut BTreeMap<kairos_primitives::MarketId, SourceRequestId>,
    inputs: &mpsc::Sender<SourceInput>,
    source_id: &SourceId,
    epoch: SourceEpoch,
    request_id: SourceRequestId,
    market: ResolvedMarket,
) {
    let Some(market_id) = market.market_id().cloned() else {
        return;
    };
    let previous = markets
        .iter()
        .find_map(|(handle, current)| (current.market_id() == Some(&market_id)).then_some(*handle));
    let result = async {
        if let Some(previous) = previous {
            connection.unsubscribe(previous).await?;
            markets.remove(&previous);
        }
        let handle = connection
            .subscribe(MarketSubscription::new([market
                .route
                .provider_symbol
                .to_string()])?)
            .await?;
        markets.insert(handle, market.clone());
        Ok::<_, kairos_integration::application::IntegrationError>(())
    }
    .await;
    match result {
        Ok(()) => {
            resyncing.insert(market_id, request_id);
        }
        Err(error) => {
            let _ = inputs
                .send(SourceInput::ResyncRejected {
                    source_id: source_id.clone(),
                    epoch,
                    request_id,
                    market_id,
                    error: error.to_string(),
                })
                .await;
        }
    }
}

async fn subscribe<C: AsyncMarketEventSource>(
    connection: &mut C,
    markets: &mut BTreeMap<IntegrationSubscriptionId, ResolvedMarket>,
    inputs: &mpsc::Sender<SourceInput>,
    source_id: &SourceId,
    epoch: SourceEpoch,
    request_id: SourceRequestId,
    market: ResolvedMarket,
) {
    let provider_symbol = market.route.provider_symbol.clone();
    let result = connection
        .subscribe(
            MarketSubscription::new([provider_symbol.to_string()])
                .expect("validated market symbol"),
        )
        .await;
    let input = match result {
        Ok(id) => {
            markets.insert(id, market);
            SourceInput::SubscriptionConfirmed {
                source_id: source_id.clone(),
                epoch,
                request_id,
                handle: ProviderSubscriptionId::new(id.0.to_string())
                    .expect("numeric provider subscription id"),
            }
        }
        Err(error) => SourceInput::SubscriptionRejected {
            source_id: source_id.clone(),
            epoch,
            request_id,
            error: error.to_string(),
        },
    };
    let _ = inputs.send(input).await;
}

async fn unsubscribe<C: AsyncMarketEventSource>(
    connection: &mut C,
    markets: &mut BTreeMap<IntegrationSubscriptionId, ResolvedMarket>,
    inputs: &mpsc::Sender<SourceInput>,
    source_id: &SourceId,
    epoch: SourceEpoch,
    request_id: SourceRequestId,
    handle: ProviderSubscriptionId,
) {
    let result = handle
        .as_str()
        .parse::<u64>()
        .map(IntegrationSubscriptionId)
        .map_err(|error| error.to_string());
    let result = match result {
        Ok(id) => connection.unsubscribe(id).await.map(|()| {
            markets.remove(&id);
        }),
        Err(error) => {
            let _ = inputs
                .send(SourceInput::SubscriptionRejected {
                    source_id: source_id.clone(),
                    epoch,
                    request_id,
                    error,
                })
                .await;
            return;
        }
    };
    let input = match result {
        Ok(()) => SourceInput::Unsubscribed {
            source_id: source_id.clone(),
            epoch,
            request_id,
        },
        Err(error) => SourceInput::SubscriptionRejected {
            source_id: source_id.clone(),
            epoch,
            request_id,
            error: error.to_string(),
        },
    };
    let _ = inputs.send(input).await;
}

pub(super) async fn status(
    inputs: &mpsc::Sender<SourceInput>,
    source_id: &SourceId,
    epoch: SourceEpoch,
    value: SourceStatus,
) -> Result<(), mpsc::error::SendError<SourceInput>> {
    inputs
        .send(SourceInput::StatusChanged {
            source_id: source_id.clone(),
            epoch,
            status: value,
            error: None,
        })
        .await
}

pub(super) async fn fail(
    inputs: &mpsc::Sender<SourceInput>,
    source_id: &SourceId,
    epoch: SourceEpoch,
    kind: SourceFailureKind,
    error: String,
) {
    let _ = inputs
        .send(SourceInput::Failed {
            source_id: source_id.clone(),
            epoch,
            kind,
            error,
        })
        .await;
}

pub(super) fn failure_kind(error: &IntegrationError) -> SourceFailureKind {
    match error {
        IntegrationError::InvalidRequest(_) => SourceFailureKind::InvalidRequest,
        IntegrationError::NotReady => SourceFailureKind::NotReady,
        IntegrationError::UnsupportedOperation => SourceFailureKind::Unsupported,
        IntegrationError::Authentication(_) => SourceFailureKind::Authentication,
        IntegrationError::Authorization(_) => SourceFailureKind::Authorization,
        IntegrationError::Entitlement(_) => SourceFailureKind::Entitlement,
        IntegrationError::RateLimited(_) => SourceFailureKind::RateLimited,
        IntegrationError::Transport(_) => SourceFailureKind::Transport,
        IntegrationError::InvalidPayload(_) => SourceFailureKind::InvalidPayload,
        IntegrationError::SequenceGap(_) => SourceFailureKind::SequenceGap,
        IntegrationError::ResyncRequired(_) => SourceFailureKind::ResyncRequired,
        IntegrationError::Backpressure(_) => SourceFailureKind::Backpressure,
        IntegrationError::Unavailable(_) => SourceFailureKind::Unavailable,
    }
}

#[cfg(test)]
mod tests {
    use super::super::messages::{SourceCommand, SourceInput};
    use super::{normalize, recover_connection, Normalized};
    use crate::domain::market::{MarketDataRoute, ResolvedMarket};
    use crate::domain::source::{SourceEpoch, SourceFailureKind, SourceId, SourceStatus};
    use kairos_integration::application::{
        AsyncMarketEventSource, IntegrationError, MarketEvent, MarketEventKind, MarketSubscription,
        SubscriptionId,
    };
    use kairos_integration::domain::{ConnectionHealth, ConnectionLifecycle};
    use kairos_primitives::{Sequence, Symbol, UnixNanos};
    use std::collections::VecDeque;
    use tokio::sync::mpsc;

    fn event(kind: MarketEventKind) -> MarketEvent {
        MarketEvent {
            symbol: Symbol::new("BTCUSDT").unwrap(),
            kind,
            price: Some("100".parse().unwrap()),
            quantity: Some("2".parse().unwrap()),
            rate: None,
            ask_price: Some("101".parse().unwrap()),
            ask_quantity: Some("3".parse().unwrap()),
            bids: Vec::new(),
            asks: Vec::new(),
            bar: None,
            greeks: None,
            first_sequence: None,
            last_sequence: None,
            sequence: None,
            observed_at_unix_nanos: UnixNanos::new(1),
            venue: Default::default(),
        }
    }

    fn market() -> ResolvedMarket {
        ResolvedMarket::new(
            "market:btc",
            "instrument:btc",
            kairos_primitives::InstrumentKind::Spot,
            "binance",
            MarketDataRoute::new("test:btc", "binance", "spot", "BTCUSDT").unwrap(),
        )
        .unwrap()
    }

    #[test]
    fn quote_event_stops_provider_types_at_source_boundary() {
        let value = normalize(
            &SourceId::new("binance.spot").unwrap(),
            &market(),
            event(MarketEventKind::Quote),
        )
        .unwrap()
        .unwrap();
        let Normalized::Observation(observation) = value else {
            panic!("quote became an order book")
        };
        assert_eq!(observation.source_id(), "binance.spot");
        assert_eq!(
            observation.market_id().map(|value| value.as_str()),
            Some("market:btc")
        );
    }

    #[test]
    fn book_event_preserves_sequence_and_levels() {
        let mut value = event(MarketEventKind::BookSnapshot);
        value.price = None;
        value.quantity = None;
        value.first_sequence = Some(Sequence::new(9));
        value.last_sequence = Some(Sequence::new(10));
        value.bids = vec![("100".parse().unwrap(), "2".parse().unwrap())];
        let value = normalize(&SourceId::new("binance.spot").unwrap(), &market(), value)
            .unwrap()
            .unwrap();
        let Normalized::OrderBook(update) = value else {
            panic!("book became an observation")
        };
        assert!(update.snapshot);
        assert_eq!(update.last_sequence, Sequence::new(10));
        assert_eq!(update.bids.len(), 1);
    }

    struct RecoveringSource {
        reconnects: usize,
    }

    impl AsyncMarketEventSource for RecoveringSource {
        async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
            Ok(())
        }
        async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
            Ok(())
        }
        async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
            self.reconnects += 1;
            Ok(())
        }
        fn channel_health(&self) -> ConnectionHealth {
            ConnectionHealth {
                lifecycle: ConnectionLifecycle::Ready,
                healthy: true,
                authenticated: false,
                last_error: None,
            }
        }
        async fn subscribe(
            &mut self,
            _: MarketSubscription,
        ) -> Result<SubscriptionId, IntegrationError> {
            Ok(SubscriptionId(1))
        }
        async fn unsubscribe(&mut self, _: SubscriptionId) -> Result<(), IntegrationError> {
            Ok(())
        }
        async fn next_market_event(&mut self) -> Result<MarketEvent, IntegrationError> {
            std::future::pending().await
        }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn transport_recovery_advances_epoch_and_reports_ready() {
        let mut source = RecoveringSource { reconnects: 0 };
        let (_command_sender, mut commands) = mpsc::channel::<SourceCommand>(1);
        let (input_sender, mut inputs) = mpsc::channel(8);
        let mut epoch = SourceEpoch::new(1);
        assert!(
            recover_connection(
                &mut source,
                &mut commands,
                &mut VecDeque::new(),
                &input_sender,
                &SourceId::new("test.live").unwrap(),
                &mut epoch,
                SourceFailureKind::Transport,
                "controlled disconnect".into(),
            )
            .await
        );
        assert_eq!(source.reconnects, 1);
        let mut saw_ready = false;
        for _ in 0..3 {
            let input = inputs.recv().await.expect("recovery status");
            if matches!(input, SourceInput::StatusChanged { epoch, status: SourceStatus::Ready, .. } if epoch == SourceEpoch::new(2))
            {
                saw_ready = true;
            }
        }
        assert!(saw_ready);
    }
}
