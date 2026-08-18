//! Async snapshot source. Polling policy lives in this I/O driver, not in the
//! Actor maintenance timeout.

use std::{collections::BTreeMap, time::Duration};

use kairos_integration::{IntegrationError, MarketEvent, MarketEventKind, MarketQuoteQuery};
use tokio::sync::mpsc;

use super::messages::{ProviderSubscriptionId, SourceCommand, SourceInput};
use super::SourceHandle;
use crate::domain::market::ResolvedMarket;
use crate::domain::source::{
    SourceDescriptor, SourceEpoch, SourceFailureKind, SourceId, SourceStatus,
};

const COMMAND_CAPACITY: usize = 1_024;

pub(crate) fn spawn_snapshot<C>(
    descriptor: SourceDescriptor,
    connection: C,
    interval: Duration,
    input_capacity: usize,
) -> SourceHandle
where
    C: MarketQuoteQuery + 'static,
{
    let (commands, receiver) = mpsc::channel(COMMAND_CAPACITY);
    let (input_sender, inputs) = mpsc::channel(input_capacity);
    let task = tokio::spawn(run(
        descriptor.clone(),
        connection,
        interval,
        receiver,
        input_sender,
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
    interval: Duration,
    mut commands: mpsc::Receiver<SourceCommand>,
    inputs: mpsc::Sender<SourceInput>,
) where
    C: MarketQuoteQuery,
{
    let source_id = descriptor.id;
    let epoch = SourceEpoch::new(1);
    let mut markets = BTreeMap::<ProviderSubscriptionId, ResolvedMarket>::new();
    let mut next_subscription = 1_u64;
    let mut ticks = tokio::time::interval(interval);
    ticks.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    if status(&inputs, &source_id, epoch, SourceStatus::Ready)
        .await
        .is_err()
    {
        return;
    }
    loop {
        tokio::select! {
            command = commands.recv() => {
                let Some(command) = command else { return };
                match command {
                    SourceCommand::Subscribe { request_id, market } => {
                        let handle = ProviderSubscriptionId::new(format!("snapshot:{next_subscription}"))
                            .expect("generated snapshot subscription id");
                        next_subscription += 1;
                        markets.insert(handle.clone(), *market);
                        if inputs.send(SourceInput::SubscriptionConfirmed {
                            source_id: source_id.clone(), epoch, request_id, handle,
                        }).await.is_err() { return; }
                    }
                    SourceCommand::Unsubscribe { request_id, handle } => {
                        markets.remove(&handle);
                        if inputs.send(SourceInput::Unsubscribed {
                            source_id: source_id.clone(), epoch, request_id,
                        }).await.is_err() { return; }
                    }
                    SourceCommand::ResyncOrderBook { request_id, market } => {
                        let Some(market_id) = market.market_id().cloned() else {
                            continue;
                        };
                        if inputs.send(SourceInput::ResyncRejected {
                            source_id: source_id.clone(),
                            epoch,
                            request_id,
                            market_id,
                            error: "snapshot source does not provide an order-book stream".into(),
                        }).await.is_err() { return; }
                    }
                    SourceCommand::Pause | SourceCommand::Resume => {}
                    SourceCommand::Reconnect => {
                        let _ = status(&inputs, &source_id, epoch, SourceStatus::Ready).await;
                    }
                    SourceCommand::Shutdown => {
                        let _ = status(&inputs, &source_id, epoch, SourceStatus::Stopped).await;
                        return;
                    }
                }
            }
            _ = ticks.tick(), if !markets.is_empty() => {
                let symbols = markets
                    .values()
                    .map(|market| {
                        kairos_primitives::ParticipantSymbol::new(
                            market.route.provider_symbol.as_str(),
                        )
                        .expect("resolved provider symbol is a valid participant symbol")
                    })
                    .collect::<Vec<_>>();
                match connection.fetch_quotes(&symbols).await {
                    Ok(quotes) => {
                        for quote in quotes {
                            let event = quote_event(quote);
                            let Some(market) = markets.values().find(|market| {
                                market.route.provider_symbol.eq_ignore_ascii_case(event.symbol.as_str())
                            }) else { continue };
                            match super::normalization::normalize(&source_id, market, event) {
                                Ok(Some(super::normalization::Normalized::Observation(observation))) => {
                                    if inputs.send(SourceInput::Observation {
                                        source_id: source_id.clone(), epoch, observation,
                                    }).await.is_err() { return; }
                                }
                                Ok(Some(super::normalization::Normalized::OrderBook(_))) => {
                                    fail(&inputs, &source_id, epoch, SourceFailureKind::InvalidPayload, "snapshot capability returned an order-book event".into()).await;
                                }
                                Ok(None) => {}
                                Err(error) => fail(&inputs, &source_id, epoch, SourceFailureKind::InvalidPayload, error).await,
                            }
                        }
                    }
                    Err(error) => fail(&inputs, &source_id, epoch, failure_kind(&error), error.to_string()).await,
                }
            }
        }
    }
}

fn quote_event(quote: kairos_integration::MarketQuote) -> MarketEvent {
    MarketEvent {
        symbol: quote.symbol,
        kind: MarketEventKind::Quote,
        price: quote.bid_price.or(quote.last_price),
        quantity: quote.bid_quantity,
        rate: None,
        ask_price: quote.ask_price,
        ask_quantity: quote.ask_quantity,
        bids: Vec::new(),
        asks: Vec::new(),
        bar: None,
        greeks: None,
        first_sequence: None,
        last_sequence: None,
        sequence: None,
        observed_at_unix_nanos: quote.observed_at_unix_nanos,
        venue: kairos_integration::MarketVenueEvidence::default(),
    }
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
