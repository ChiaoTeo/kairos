//! Wake-driven driver for Integration live market capabilities.

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::time::Duration;

use kairos_primitives::Money;
use kairos_integration::application::{
    AsyncMarketEventSource, IntegrationError, MarketEvent, MarketEventKind, MarketSubscription,
    SubscriptionId as IntegrationSubscriptionId,
};
use tokio::sync::mpsc;

use super::SourceHandle;
use crate::domain::market::MarketDescriptor;
use crate::domain::observations::{
    Bar, FundingRate, IndexPrice, MarkPrice, MarketObservation, OpenInterest, OptionGreeks, Quote,
    QuoteBar, Rate, Ticker24h, Trade, TradeBar,
};
use crate::domain::orderbook::PriceLevel;
use crate::domain::source::{
    SourceDescriptor, SourceEpoch, SourceFailureKind, SourceId, SourceStatus,
};
use crate::services::messages::{
    ProviderSubscriptionId, SourceCommand, SourceInput, SourceOrderBookUpdate, SourceRequestId,
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
    let mut markets = BTreeMap::<IntegrationSubscriptionId, MarketDescriptor>::new();
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
                                market.provider_symbol.as_ref().is_some_and(|symbol| {
                                    reason.contains(symbol.as_str())
                                })
                            })
                            .cloned()
                            .collect::<Vec<_>>();
                        if affected.is_empty() {
                            fail(&inputs, &source_id, epoch, SourceFailureKind::ResyncRequired, reason).await;
                            return;
                        }
                        for market in affected {
                            blocked_markets.insert(market.market_id.clone());
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
                    market.provider_symbol.as_ref().is_some_and(|symbol| {
                        symbol.eq_ignore_ascii_case(event.symbol.as_str())
                    })
                }) else { continue };
                if blocked_markets.contains(&market.market_id)
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
                                    blocked_markets.insert(market.market_id.clone());
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

fn reconnectable(error: &IntegrationError) -> bool {
    matches!(
        error,
        IntegrationError::NotReady
            | IntegrationError::RateLimited(_)
            | IntegrationError::Transport(_)
            | IntegrationError::Backpressure(_)
            | IntegrationError::Unavailable(_)
    )
}

async fn recover_connection<C: AsyncMarketEventSource>(
    connection: &mut C,
    commands: &mut mpsc::Receiver<SourceCommand>,
    deferred_commands: &mut VecDeque<SourceCommand>,
    inputs: &mpsc::Sender<SourceInput>,
    source_id: &SourceId,
    epoch: &mut SourceEpoch,
    mut kind: SourceFailureKind,
    mut reason: String,
) -> bool {
    let mut delay = Duration::from_millis(250);
    loop {
        fail(inputs, source_id, *epoch, kind, reason.clone()).await;
        if status(inputs, source_id, *epoch, SourceStatus::Reconnecting)
            .await
            .is_err()
        {
            return false;
        }
        tokio::select! {
            _ = tokio::time::sleep(delay) => {}
            command = commands.recv() => match command {
                Some(SourceCommand::Shutdown) | None => {
                    let _ = connection.disconnect_channel().await;
                    let _ = status(inputs, source_id, *epoch, SourceStatus::Stopped).await;
                    return false;
                }
                Some(command) => {
                    deferred_commands.push_back(command);
                    continue;
                }
            }
        }
        match connection.reconnect_channel().await {
            Ok(()) => {
                epoch.advance();
                return status(inputs, source_id, *epoch, SourceStatus::Ready)
                    .await
                    .is_ok();
            }
            Err(error) if reconnectable(&error) => {
                kind = failure_kind(&error);
                reason = error.to_string();
                delay = delay.saturating_mul(2).min(Duration::from_secs(5));
            }
            Err(error) => {
                fail(
                    inputs,
                    source_id,
                    *epoch,
                    failure_kind(&error),
                    error.to_string(),
                )
                .await;
                return false;
            }
        }
    }
}

pub(super) enum Normalized {
    Observation(MarketObservation),
    OrderBook(SourceOrderBookUpdate),
}

fn with_epoch(value: Normalized, source_id: SourceId, epoch: SourceEpoch) -> SourceInput {
    match value {
        Normalized::Observation(observation) => SourceInput::Observation {
            source_id,
            epoch,
            observation,
        },
        Normalized::OrderBook(update) => SourceInput::OrderBook {
            source_id,
            epoch,
            update,
        },
    }
}

pub(super) fn normalize(
    source_id: &SourceId,
    market: &MarketDescriptor,
    event: MarketEvent,
) -> Result<Option<Normalized>, String> {
    let source_id = source_id.to_string();
    let observation = match event.kind {
        MarketEventKind::Quote | MarketEventKind::Snapshot => MarketObservation::Quote(Quote {
            market_id: market.market_id.clone(),
            instrument_id: market.instrument_id.clone(),
            bid_price: event.price,
            bid_quantity: event.quantity,
            ask_price: event.ask_price,
            ask_quantity: event.ask_quantity,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::Trade => MarketObservation::Trade(Trade {
            market_id: market.market_id.clone(),
            instrument_id: market.instrument_id.clone(),
            trade_id: None,
            price: event.price.ok_or("trade event has no price")?,
            quantity: event.quantity.ok_or("trade event has no quantity")?,
            cost: None,
            aggressor_side: None,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::Bar | MarketEventKind::TradeBar | MarketEventKind::QuoteBar => {
            let value = event.bar.ok_or("bar event has no bar payload")?;
            let bar = Bar {
                market_id: market.market_id.clone(),
                instrument_id: market.instrument_id.clone(),
                timeframe: value.timeframe,
                open: value.open,
                high: value.high,
                low: value.low,
                close: value.close,
                volume: value.volume,
                observed_at_unix_nanos: event.observed_at_unix_nanos,
                source_id,
                derivation: value.derivation,
            };
            match event.kind {
                MarketEventKind::TradeBar => MarketObservation::TradeBar(TradeBar { bar }),
                MarketEventKind::QuoteBar => MarketObservation::QuoteBar(QuoteBar { bar }),
                _ => MarketObservation::Bar(bar),
            }
        }
        MarketEventKind::Greeks => {
            let value = event.greeks.ok_or("greeks event has no greeks payload")?;
            MarketObservation::OptionGreeks(OptionGreeks {
                market_id: market.market_id.clone(),
                instrument_id: market.instrument_id.clone(),
                expiry_unix_nanos: value.expiry_unix_nanos,
                strike: value.strike,
                delta: value.delta,
                gamma: value.gamma,
                vega: value.vega,
                theta: value.theta,
                implied_volatility: value.implied_volatility,
                observed_at_unix_nanos: event.observed_at_unix_nanos,
                source_id,
                derivation: value.derivation,
            })
        }
        MarketEventKind::Rate => MarketObservation::Rate(Rate {
            rate_id: format!("funding:{}", market.market_id),
            market_id: market.market_id.clone(),
            instrument_id: market.instrument_id.clone(),
            basis: "funding".into(),
            value: event.rate.ok_or("rate event has no value")?,
            mark_price: event.ask_price,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::Ticker24h => MarketObservation::Ticker24h(Ticker24h {
            market_id: market.market_id.clone(),
            instrument_id: market.instrument_id.clone(),
            last_price: event.price,
            bid_price: None,
            bid_quantity: None,
            ask_price: event.ask_price,
            ask_quantity: event.ask_quantity,
            open_price: None,
            high_price: None,
            low_price: None,
            volume_base: event.quantity,
            volume_quote: None,
            price_change_abs: None,
            price_change_pct: None,
            vwap: None,
            mark_price: None,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::MarkPrice => MarketObservation::MarkPrice(MarkPrice {
            market_id: market.market_id.clone(),
            instrument_id: market.instrument_id.clone(),
            mark_price: event.price.ok_or("mark price event has no price")?,
            index_price: event.ask_price,
            estimated_settlement_price: None,
            funding_rate: None,
            next_funding_time_unix_nanos: None,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::IndexPrice => MarketObservation::IndexPrice(IndexPrice {
            market_id: market.market_id.clone(),
            instrument_id: market.instrument_id.clone(),
            spot_index_price: event.price,
            contract_index_price: None,
            index_price: event.price,
            funding_rate: None,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::FundingRate => MarketObservation::FundingRate(FundingRate {
            market_id: market.market_id.clone(),
            instrument_id: market.instrument_id.clone(),
            funding_rate: event.rate.ok_or("funding rate event has no value")?,
            funding_period_seconds: None,
            next_funding_time_unix_nanos: None,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::OpenInterest => MarketObservation::OpenInterest(OpenInterest {
            market_id: market.market_id.clone(),
            instrument_id: market.instrument_id.clone(),
            contracts: event
                .quantity
                .ok_or("open interest event has no quantity")?,
            quote_value: event
                .price
                .map(|value| Money::new(value.mantissa(), value.scale()))
                .transpose()
                .map_err(|error| error.to_string())?,
            change_24h: None,
            change_pct_24h: None,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::InstrumentStatus => {
            return Err("InstrumentStatus is not part of Market v2".into());
        }
        MarketEventKind::BookSnapshot | MarketEventKind::BookDelta => {
            let update = SourceOrderBookUpdate {
                market: Box::new(market.clone()),
                source_id,
                market_id: market.market_id.clone(),
                instrument_id: market.instrument_id.clone(),
                first_sequence: event
                    .first_sequence
                    .ok_or("order book event has no first sequence")?,
                last_sequence: event
                    .last_sequence
                    .or(event.sequence)
                    .ok_or("order book event has no last sequence")?,
                event_time_unix_nanos: event.observed_at_unix_nanos,
                bids: event
                    .bids
                    .into_iter()
                    .map(|(price, quantity)| PriceLevel { price, quantity })
                    .collect(),
                asks: event
                    .asks
                    .into_iter()
                    .map(|(price, quantity)| PriceLevel { price, quantity })
                    .collect(),
                snapshot: event.kind == MarketEventKind::BookSnapshot,
            };
            return Ok(Some(Normalized::OrderBook(update)));
        }
        MarketEventKind::Heartbeat => return Ok(None),
    };
    Ok(Some(Normalized::Observation(observation)))
}

#[allow(clippy::too_many_arguments)]
async fn resync<C: AsyncMarketEventSource>(
    connection: &mut C,
    markets: &mut BTreeMap<IntegrationSubscriptionId, MarketDescriptor>,
    resyncing: &mut BTreeMap<kairos_primitives::MarketId, SourceRequestId>,
    inputs: &mpsc::Sender<SourceInput>,
    source_id: &SourceId,
    epoch: SourceEpoch,
    request_id: SourceRequestId,
    market: MarketDescriptor,
) {
    let previous = markets
        .iter()
        .find_map(|(handle, current)| (current.market_id == market.market_id).then_some(*handle));
    let result = async {
        if let Some(previous) = previous {
            connection.unsubscribe(previous).await?;
            markets.remove(&previous);
        }
        let handle = connection
            .subscribe(MarketSubscription::new([market
                .provider_symbol
                .clone()
                .ok_or_else(|| {
                    kairos_integration::application::IntegrationError::InvalidRequest(
                        "MarketDataAccess provider_symbol missing".into(),
                    )
                })?
                .to_string()])?)
            .await?;
        markets.insert(handle, market.clone());
        Ok::<_, kairos_integration::application::IntegrationError>(())
    }
    .await;
    match result {
        Ok(()) => {
            resyncing.insert(market.market_id, request_id);
        }
        Err(error) => {
            let _ = inputs
                .send(SourceInput::ResyncRejected {
                    source_id: source_id.clone(),
                    epoch,
                    request_id,
                    market_id: market.market_id,
                    error: error.to_string(),
                })
                .await;
        }
    }
}

async fn subscribe<C: AsyncMarketEventSource>(
    connection: &mut C,
    markets: &mut BTreeMap<IntegrationSubscriptionId, MarketDescriptor>,
    inputs: &mpsc::Sender<SourceInput>,
    source_id: &SourceId,
    epoch: SourceEpoch,
    request_id: SourceRequestId,
    market: MarketDescriptor,
) {
    let Some(provider_symbol) = market.provider_symbol.clone() else {
        let _ = inputs
            .send(SourceInput::SubscriptionRejected {
                source_id: source_id.clone(),
                epoch,
                request_id,
                error: "MarketDataAccess provider_symbol missing".into(),
            })
            .await;
        return;
    };
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
    markets: &mut BTreeMap<IntegrationSubscriptionId, MarketDescriptor>,
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

async fn status(
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

async fn fail(
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

fn failure_kind(error: &IntegrationError) -> SourceFailureKind {
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
    use super::{normalize, recover_connection, Normalized};
    use crate::domain::market::MarketDescriptor;
    use crate::domain::source::{SourceEpoch, SourceFailureKind, SourceId, SourceStatus};
    use crate::services::messages::{SourceCommand, SourceInput};
    use kairos_primitives::{Sequence, Symbol, UnixNanos};
    use kairos_integration::application::{
        AsyncMarketEventSource, IntegrationError, MarketEvent, MarketEventKind, MarketSubscription,
        SubscriptionId,
    };
    use kairos_integration::domain::{ConnectionHealth, ConnectionLifecycle};
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
        }
    }

    fn market() -> MarketDescriptor {
        MarketDescriptor::new("market:btc", "instrument:btc", "binance", "spot", "BTCUSDT").unwrap()
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
        assert_eq!(observation.market_id(), "market:btc");
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
