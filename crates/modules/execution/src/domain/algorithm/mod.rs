//! Deterministic, provider-neutral execution-algorithm state.

mod decision;
mod error;
mod immediate;
mod maker_taker;
mod passive_limit;
mod state;
mod twap;

pub use decision::*;
pub use error::*;
pub use immediate::decide_immediate;
pub use maker_taker::decide_maker_taker_hedge;
pub use passive_limit::decide_passive_limit;
pub use state::*;
pub use twap::decide_twap;

#[cfg(test)]
mod tests;
