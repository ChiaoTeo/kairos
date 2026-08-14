//! Async snapshot source. Polling policy lives in this I/O driver, not in the
//! Actor maintenance timeout.

use std::{collections::BTreeMap, time::Duration};

use kairos_integration::application::{AsyncMarketSnapshotConnection, IntegrationError};
use tokio::sync::mpsc;

use super::SourceHandle;
use crate::domain::market::MarketDescriptor;
use crate::domain::source::{
    SourceDescriptor, SourceEpoch, SourceFailureKind, SourceId, SourceStatus,
};
use crate::services::messages::{ProviderSubscriptionId, SourceCommand, SourceInput};

const COMMAND_CAPACITY: usize = 1_024;

pub(crate) fn spawn_snapshot<C>(
    descriptor: SourceDescriptor,
    connection: C,
    interval: Duration,
    input_capacity: usize,
) -> SourceHandle
where
    C: AsyncMarketSnapshotConnection + 'static,
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
    C: AsyncMarketSnapshotConnection,
{
    let source_id = descriptor.id;
    let epoch = SourceEpoch::new(1);
    let mut markets = BTreeMap::<ProviderSubscriptionId, MarketDescriptor>::new();
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
                        if inputs.send(SourceInput::ResyncRejected {
                            source_id: source_id.clone(),
                            epoch,
                            request_id,
                            market_id: market.market_id,
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
                let symbols = markets.values().map(|market| {
                    market.provider_symbol.clone().ok_or_else(|| {
                        format!("MarketDataAccess provider_symbol missing for {}", market.market_id)
                    })
                }).collect::<Result<Vec<_>, _>>();
                let symbols = match symbols {
                    Ok(symbols) => symbols,
                    Err(error) => {
                        fail(&inputs, &source_id, epoch, SourceFailureKind::InvalidRequest, error.to_string()).await;
                        continue;
                    }
                };
                match connection.fetch_snapshot(&symbols).await {
                    Ok(events) => {
                        for event in events {
                            let Some(market) = markets.values().find(|market| {
                                market.provider_symbol.as_ref().is_some_and(|symbol| {
                                    symbol.eq_ignore_ascii_case(event.symbol.as_str())
                                })
                            }) else { continue };
                            match super::stream::normalize(&source_id, market, event) {
                                Ok(Some(super::stream::Normalized::Observation(observation))) => {
                                    if inputs.send(SourceInput::Observation {
                                        source_id: source_id.clone(), epoch, observation,
                                    }).await.is_err() { return; }
                                }
                                Ok(Some(super::stream::Normalized::OrderBook(_))) => {
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
