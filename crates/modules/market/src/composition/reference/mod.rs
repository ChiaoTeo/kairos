//! Concrete consumption of the external Reference contract.

mod client;
mod events;
mod projection;

pub(crate) use client::{spawn_market_universe_watcher, ReferenceWatcherGuard};
pub(crate) use projection::{adapter_observation_capabilities, project_market_universe};
