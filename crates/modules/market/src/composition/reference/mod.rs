//! Concrete consumption of the external Reference contract.

mod client;
mod events;
mod projection;

pub(crate) use client::spawn_market_universe_watcher;
pub(crate) use projection::project_market_universe;
