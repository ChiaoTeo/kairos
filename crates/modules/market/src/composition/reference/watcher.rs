use std::time::Duration;

use futures_util::StreamExt;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;
use tokio::time::{self, MissedTickBehavior};

use crate::ReconcileMarketUniverse;

pub(crate) struct ReferenceWatcherGuard(JoinHandle<()>);

impl Drop for ReferenceWatcherGuard {
    fn drop(&mut self) {
        self.0.abort();
    }
}

pub(crate) fn spawn_market_universe_watcher(
    client: kairos_reference_contract::ReferenceClient,
    interval: Duration,
    capacity: usize,
) -> Result<
    (
        mpsc::Receiver<ReconcileMarketUniverse>,
        ReferenceWatcherGuard,
    ),
    String,
> {
    if interval.is_zero() {
        return Err("Reference watcher interval must be positive".into());
    }
    if capacity == 0 {
        return Err("Reference watcher capacity must be positive".into());
    }
    let mut events = client.events(capacity).map_err(|error| error.to_string())?;
    let (sender, receiver) = mpsc::channel(capacity);
    let task = tokio::spawn(async move {
        let mut required_sequence = 0_u64;
        let mut published_sequence = None;
        let mut events_open = true;
        let mut ticks = time::interval(interval);
        ticks.set_missed_tick_behavior(MissedTickBehavior::Delay);
        loop {
            let should_read = tokio::select! {
                event = events.next(), if events_open => {
                    match event {
                        Some(Ok(frame)) => match frame.decode() {
                            Ok(event) => {
                                required_sequence = required_sequence.max(event_sequence(&event));
                                true
                            }
                            Err(_) => true,
                        },
                        Some(Err(_)) | None => {
                            events_open = false;
                            true
                        }
                    }
                }
                _ = ticks.tick() => true,
            };
            if !should_read {
                continue;
            }
            let update = (|| {
                let snapshot = client
                    .market_snapshot()
                    .map_err(|error| error.to_string())?;
                super::projection::project_market_universe_at_sequence(&snapshot, required_sequence)
            })();
            let Ok(update) = update else {
                continue;
            };
            if published_sequence.is_some_and(|sequence| sequence >= update.event_sequence.get()) {
                continue;
            }
            published_sequence = Some(update.event_sequence.get());
            if sender.send(update).await.is_err() {
                break;
            }
        }
    });
    Ok((receiver, ReferenceWatcherGuard(task)))
}

fn event_sequence(event: &kairos_reference_contract::ReferenceEvent<'_>) -> u64 {
    use kairos_reference_contract::ReferenceEvent;
    match event {
        ReferenceEvent::EntityUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::EntityUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::AssetUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::AssetUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::ExecutionAccessUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::ExecutionAccessUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::MarketDataAccessUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::MarketDataAccessUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::InstrumentUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::InstrumentUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::ListingUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::ListingUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::MarketUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::MarketUpdated(value) => value.metadata().sequence(),
    }
}
