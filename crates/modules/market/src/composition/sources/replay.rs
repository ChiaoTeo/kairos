//! Deterministic replay source composition.

use super::super::config::MarketReplayClock;
use crate::MarketApplication;
use crate::domain::source::{SourceDescriptor, SourceId};
use crate::services::source::{ReplayClock, ReplaySource, spawn_replay};

/// Attach deterministic replay to the same wake-driven Actor input path used
/// by live providers.
pub fn attach_replay_source(
    runtime: &mut MarketApplication,
    events: impl IntoIterator<Item = crate::domain::observation::MarketObservation>,
) -> Result<(), String> {
    attach_replay(runtime, ReplaySource::new(events))
}

pub fn attach_replay_source_with_checkpoint(
    runtime: &mut MarketApplication,
    events: impl IntoIterator<Item = crate::domain::observation::MarketObservation>,
    start_unix_nanos: Option<u64>,
    end_unix_nanos: Option<u64>,
    checkpoint: impl Into<std::path::PathBuf>,
) -> Result<(), String> {
    attach_replay(
        runtime,
        ReplaySource::with_checkpoint(events, start_unix_nanos, end_unix_nanos, checkpoint)?,
    )
}

pub fn attach_replay_source_with_policy(
    runtime: &mut MarketApplication,
    events: impl IntoIterator<Item = crate::domain::observation::MarketObservation>,
    start_unix_nanos: Option<u64>,
    end_unix_nanos: Option<u64>,
    checkpoint: impl Into<std::path::PathBuf>,
    clock: MarketReplayClock,
    speed_multiplier: u32,
    start_paused: bool,
) -> Result<(), String> {
    attach_replay(
        runtime,
        ReplaySource::with_policy(
            events,
            start_unix_nanos,
            end_unix_nanos,
            checkpoint,
            match clock {
                MarketReplayClock::Maximum => ReplayClock::Maximum,
                MarketReplayClock::EventTime => ReplayClock::EventTime,
            },
            speed_multiplier,
            start_paused,
        )?,
    )
}

fn attach_replay(runtime: &mut MarketApplication, source: ReplaySource) -> Result<(), String> {
    let descriptor = SourceDescriptor::all_routes(SourceId::new("replay")?);
    let handle = spawn_replay(descriptor, source, runtime.source_input_capacity());
    runtime.attach_source(handle)
}
