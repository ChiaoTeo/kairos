//! Deterministic finite source driven by the same messages as live providers.

use std::{collections::BTreeMap, collections::VecDeque, path::PathBuf};

use serde::{Deserialize, Serialize};
use tokio::sync::mpsc;

use super::messages::{ProviderSubscriptionId, SourceCommand, SourceInput};
use super::SourceHandle;
use crate::domain::market::ResolvedMarket;
use crate::domain::observation::MarketObservation;
use crate::domain::source::{
    SourceDescriptor, SourceEpoch, SourceFailureKind, SourceId, SourceStatus,
};

const COMMAND_CAPACITY: usize = 1_024;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ReplayClock {
    Maximum,
    EventTime,
}

pub(crate) struct ReplaySource {
    events: VecDeque<MarketObservation>,
    checkpoint_path: Option<PathBuf>,
    cursor: usize,
    virtual_time_unix_nanos: Option<u64>,
    paused: bool,
    clock: ReplayClock,
    speed_multiplier: u32,
    completed: bool,
    replay_checkpoint: Option<crate::services::actor::ReplayCheckpoint>,
}

impl ReplaySource {
    pub(crate) fn new(events: impl IntoIterator<Item = MarketObservation>) -> Self {
        Self::with_window(events, None, None)
    }

    pub(crate) fn with_window(
        events: impl IntoIterator<Item = MarketObservation>,
        start_unix_nanos: Option<u64>,
        end_unix_nanos: Option<u64>,
    ) -> Self {
        assert!(
            start_unix_nanos
                .zip(end_unix_nanos)
                .is_none_or(|(start, end)| start <= end),
            "replay start must not be after replay end"
        );
        let mut events = events
            .into_iter()
            .filter(|event| {
                let time = event.observed_at_unix_nanos().get();
                start_unix_nanos.is_none_or(|start| time >= start)
                    && end_unix_nanos.is_none_or(|end| time <= end)
            })
            .collect::<Vec<_>>();
        events.sort_by(|left, right| {
            left.observed_at_unix_nanos()
                .cmp(&right.observed_at_unix_nanos())
                .then_with(|| left.source_id().cmp(right.source_id()))
                .then_with(|| left.market_id().cmp(right.market_id()))
                .then_with(|| left.kind().cmp(&right.kind()))
        });
        Self {
            events: events.into(),
            checkpoint_path: None,
            cursor: 0,
            virtual_time_unix_nanos: start_unix_nanos,
            paused: false,
            clock: ReplayClock::Maximum,
            speed_multiplier: 1,
            completed: false,
            replay_checkpoint: None,
        }
    }

    pub(crate) fn with_checkpoint(
        events: impl IntoIterator<Item = MarketObservation>,
        start_unix_nanos: Option<u64>,
        end_unix_nanos: Option<u64>,
        checkpoint: impl Into<PathBuf>,
    ) -> Result<Self, String> {
        let checkpoint = checkpoint.into();
        let mut source = Self::with_window(events, start_unix_nanos, end_unix_nanos);
        if checkpoint.is_file() {
            let value: Checkpoint = serde_json::from_slice(
                &std::fs::read(&checkpoint).map_err(|error| error.to_string())?,
            )
            .map_err(|error| format!("invalid replay checkpoint: {error}"))?;
            if value.cursor > source.events.len() {
                return Err("replay checkpoint cursor exceeds event source".into());
            }
            if value.replay_checkpoint.is_some() {
                source.events.drain(..value.cursor);
                source.cursor = value.cursor;
                source.virtual_time_unix_nanos = value.virtual_time_unix_nanos;
                source.completed = value.completed;
                source.replay_checkpoint = value.replay_checkpoint;
            }
        }
        source.checkpoint_path = Some(checkpoint);
        Ok(source)
    }

    pub(crate) fn with_policy(
        events: impl IntoIterator<Item = MarketObservation>,
        start_unix_nanos: Option<u64>,
        end_unix_nanos: Option<u64>,
        checkpoint: impl Into<PathBuf>,
        clock: ReplayClock,
        speed_multiplier: u32,
        start_paused: bool,
    ) -> Result<Self, String> {
        if speed_multiplier == 0 {
            return Err("replay speed multiplier must be positive".into());
        }
        let mut source =
            Self::with_checkpoint(events, start_unix_nanos, end_unix_nanos, checkpoint)?;
        source.clock = clock;
        source.speed_multiplier = speed_multiplier;
        source.paused = start_paused;
        Ok(source)
    }

    fn next_delay(&self) -> std::time::Duration {
        if self.clock == ReplayClock::Maximum {
            return std::time::Duration::ZERO;
        }
        let Some(next) = self.events.front() else {
            return std::time::Duration::ZERO;
        };
        let Some(current) = self.virtual_time_unix_nanos else {
            return std::time::Duration::ZERO;
        };
        std::time::Duration::from_nanos(
            next.observed_at_unix_nanos().get().saturating_sub(current)
                / u64::from(self.speed_multiplier),
        )
    }

    async fn persist_checkpoint(&self) -> Result<(), String> {
        let Some(path) = &self.checkpoint_path else {
            return Ok(());
        };
        if let Some(parent) = path.parent() {
            tokio::fs::create_dir_all(parent)
                .await
                .map_err(|error| error.to_string())?;
        }
        let temporary = path.with_extension("tmp");
        let bytes = serde_json::to_vec_pretty(&Checkpoint {
            cursor: self.cursor,
            virtual_time_unix_nanos: self.virtual_time_unix_nanos,
            completed: self.completed,
            replay_checkpoint: self.replay_checkpoint.clone(),
        })
        .map_err(|error| error.to_string())?;
        tokio::fs::write(&temporary, bytes)
            .await
            .map_err(|error| error.to_string())?;
        tokio::fs::rename(temporary, path)
            .await
            .map_err(|error| error.to_string())
    }
}

#[derive(Serialize, Deserialize)]
struct Checkpoint {
    cursor: usize,
    virtual_time_unix_nanos: Option<u64>,
    completed: bool,
    #[serde(default, alias = "actor_snapshot")]
    replay_checkpoint: Option<crate::services::actor::ReplayCheckpoint>,
}

pub(crate) fn load_replay_checkpoint(
    path: &std::path::Path,
) -> Result<Option<crate::services::actor::ReplayCheckpoint>, String> {
    if !path.is_file() {
        return Ok(None);
    }
    let checkpoint: Checkpoint =
        serde_json::from_slice(&std::fs::read(path).map_err(|e| e.to_string())?)
            .map_err(|e| format!("invalid replay checkpoint: {e}"))?;
    Ok(checkpoint.replay_checkpoint)
}

pub(crate) fn spawn_replay(
    descriptor: SourceDescriptor,
    source: ReplaySource,
    input_capacity: usize,
) -> SourceHandle {
    let (commands, command_receiver) = mpsc::channel(COMMAND_CAPACITY);
    let (input_sender, inputs) = mpsc::channel(input_capacity);
    let task_descriptor = descriptor.clone();
    let task = tokio::spawn(run(task_descriptor, source, command_receiver, input_sender));
    SourceHandle {
        descriptor,
        commands,
        inputs,
        task,
    }
}

async fn run(
    descriptor: SourceDescriptor,
    mut source: ReplaySource,
    mut commands: mpsc::Receiver<SourceCommand>,
    inputs: mpsc::Sender<SourceInput>,
) {
    let source_id = descriptor.id;
    let epoch = SourceEpoch::new(1);
    let mut subscriptions = BTreeMap::<ProviderSubscriptionId, ResolvedMarket>::new();
    let mut next_subscription = 1_u64;
    let initial_status = if source.paused {
        SourceStatus::Paused
    } else {
        SourceStatus::Ready
    };
    if send_status(&inputs, &source_id, epoch, initial_status)
        .await
        .is_err()
    {
        return;
    }
    if source.completed {
        let _ = inputs
            .send(SourceInput::Completed {
                source_id: source_id.clone(),
                epoch,
            })
            .await;
        return;
    }

    loop {
        tokio::select! {
            command = commands.recv() => {
                let Some(command) = command else { return };
                match command {
                    SourceCommand::Subscribe { request_id, market } => {
                        let handle = ProviderSubscriptionId::new(format!("replay:{next_subscription}"))
                            .expect("generated replay subscription id");
                        next_subscription += 1;
                        subscriptions.insert(handle.clone(), *market);
                        if inputs.send(SourceInput::SubscriptionConfirmed {
                            source_id: source_id.clone(), epoch, request_id, handle,
                        }).await.is_err() { return; }
                    }
                    SourceCommand::Unsubscribe { request_id, handle } => {
                        subscriptions.remove(&handle);
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
                            error: "replay observations do not provide a live resync operation".into(),
                        }).await.is_err() { return; }
                    }
                    SourceCommand::Pause => {
                        source.paused = true;
                        if send_status(&inputs, &source_id, epoch, SourceStatus::Paused).await.is_err() { return; }
                    }
                    SourceCommand::Resume => {
                        source.paused = false;
                        if send_status(&inputs, &source_id, epoch, SourceStatus::Ready).await.is_err() { return; }
                    }
                    SourceCommand::Reconnect => {}
                    SourceCommand::Shutdown => {
                        if let Err(error) = source.persist_checkpoint().await {
                            let _ = inputs.send(SourceInput::Failed {
                                source_id: source_id.clone(), epoch, kind: SourceFailureKind::Replay, error,
                            }).await;
                            return;
                        }
                        let _ = send_status(&inputs, &source_id, epoch, SourceStatus::Stopped).await;
                        return;
                    }
                }
            }
            _ = tokio::time::sleep(source.next_delay()), if !subscriptions.is_empty() && !source.completed && !source.paused => {
                let Some(observation) = source.events.pop_front() else {
                    complete(&mut source, &inputs, &source_id, epoch).await;
                    continue;
                };
                let event_time = observation.observed_at_unix_nanos().get();
                let (accepted, acceptance) = tokio::sync::oneshot::channel();
                if inputs.send(SourceInput::ReplayObservation {
                    source_id: source_id.clone(), epoch, observation, accepted,
                }).await.is_err() { return; }
                match acceptance.await {
                    Ok(Ok(checkpoint)) => source.replay_checkpoint = Some(checkpoint),
                    Ok(Err(error)) => {
                        let _ = inputs.send(SourceInput::Failed { source_id: source_id.clone(), epoch, kind: SourceFailureKind::Replay, error }).await;
                        return;
                    }
                    Err(_) => return,
                }
                source.cursor += 1;
                source.virtual_time_unix_nanos = Some(event_time);
                if let Err(error) = source.persist_checkpoint().await {
                    let _ = inputs.send(SourceInput::Failed {
                        source_id: source_id.clone(), epoch, kind: SourceFailureKind::Replay, error,
                    }).await;
                    return;
                }
                if source.events.is_empty() {
                    complete(&mut source, &inputs, &source_id, epoch).await;
                }
            }
        }
    }
}

async fn complete(
    source: &mut ReplaySource,
    inputs: &mpsc::Sender<SourceInput>,
    source_id: &SourceId,
    epoch: SourceEpoch,
) {
    source.completed = true;
    let input = match source.persist_checkpoint().await {
        Ok(()) => SourceInput::Completed {
            source_id: source_id.clone(),
            epoch,
        },
        Err(error) => SourceInput::Failed {
            source_id: source_id.clone(),
            epoch,
            kind: SourceFailureKind::Replay,
            error,
        },
    };
    let _ = inputs.send(input).await;
}

async fn send_status(
    inputs: &mpsc::Sender<SourceInput>,
    source_id: &SourceId,
    epoch: SourceEpoch,
    status: SourceStatus,
) -> Result<(), mpsc::error::SendError<SourceInput>> {
    inputs
        .send(SourceInput::StatusChanged {
            source_id: source_id.clone(),
            epoch,
            status,
            error: None,
        })
        .await
}

#[cfg(test)]
mod tests {
    use super::{ReplayClock, ReplaySource};
    use crate::domain::observation::{Bar, MarketObservation};
    use kairos_primitives::{InstrumentId, MarketId, UnixNanos};

    fn bar(time: u64) -> MarketObservation {
        MarketObservation::Bar(Bar {
            market_id: MarketId::new("market:test:spot:TEST").unwrap(),
            instrument_id: InstrumentId::new("instrument:test:spot:TEST").unwrap(),
            timeframe: "1m".into(),
            open: "1".parse().unwrap(),
            high: "1".parse().unwrap(),
            low: "1".parse().unwrap(),
            close: "1".parse().unwrap(),
            volume: None,
            observed_at_unix_nanos: UnixNanos::new(time),
            source_id: "replay-fixture".into(),
            derivation: "test".into(),
        })
    }

    #[test]
    fn event_time_clock_scales_virtual_delay_without_changing_event_time() {
        let directory = tempfile::tempdir().unwrap();
        let source = ReplaySource::with_policy(
            [bar(5_000_000_000)],
            Some(1_000_000_000),
            None,
            directory.path().join("checkpoint.json"),
            ReplayClock::EventTime,
            2,
            true,
        )
        .unwrap();

        assert!(source.paused);
        assert_eq!(source.next_delay(), std::time::Duration::from_secs(2));
        assert_eq!(
            source
                .events
                .front()
                .unwrap()
                .observed_at_unix_nanos()
                .get(),
            5_000_000_000
        );
    }

    #[tokio::test(flavor = "current_thread")]
    async fn checkpoint_restores_actor_watermark_and_next_cursor_from_one_atomic_record() {
        let directory = tempfile::tempdir().unwrap();
        let checkpoint = directory.path().join("checkpoint.json");
        let first = bar(1);
        let second = bar(2);
        let mut source = ReplaySource::with_policy(
            [first.clone(), second],
            None,
            None,
            &checkpoint,
            ReplayClock::Maximum,
            1,
            false,
        )
        .unwrap();
        source.events.pop_front();
        source.cursor = 1;
        source.virtual_time_unix_nanos = Some(1);
        let mut replay_checkpoint = crate::services::actor::ReplayCheckpoint::default();
        replay_checkpoint.event_sequence = kairos_primitives::Sequence::new(1);
        replay_checkpoint
            .views
            .insert(first.view_key().unwrap().as_str(), first);
        source.replay_checkpoint = Some(replay_checkpoint);
        source.persist_checkpoint().await.unwrap();

        let restored = ReplaySource::with_policy(
            [bar(1), bar(2)],
            None,
            None,
            &checkpoint,
            ReplayClock::Maximum,
            1,
            false,
        )
        .unwrap();
        assert_eq!(restored.cursor, 1);
        assert_eq!(
            restored
                .events
                .front()
                .unwrap()
                .observed_at_unix_nanos()
                .get(),
            2
        );
        let restored = super::load_replay_checkpoint(&checkpoint).unwrap().unwrap();
        assert_eq!(restored.event_sequence.get(), 1);
        assert_eq!(restored.views.len(), 1);
    }
}
