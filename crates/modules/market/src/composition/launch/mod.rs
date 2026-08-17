mod diagnostic;
mod process;

pub use diagnostic::{
    attach_binance_derivatives_source, attach_binance_spot_rest_source, attach_binance_spot_source,
};
pub use process::{build_market_process, MarketStartupError};
