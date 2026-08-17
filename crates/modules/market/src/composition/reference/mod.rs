//! Concrete consumption of the external Reference contract.

mod projection;
mod watcher;

pub(crate) use projection::project_market_universe;
pub(crate) use watcher::spawn_market_universe_watcher;
