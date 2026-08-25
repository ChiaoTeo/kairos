//! Deterministic, provider-neutral execution-algorithm state.

mod decision;
mod immediate;
mod maker_taker;
mod state;
mod twap;

pub use decision::*;
pub use immediate::decide_immediate;
pub use maker_taker::decide_maker_taker_hedge;
pub use state::*;
pub use twap::decide_twap;

#[cfg(test)]
mod tests;
