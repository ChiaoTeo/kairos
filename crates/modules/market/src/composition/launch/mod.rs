mod assembly;
mod diagnostic;

pub use assembly::{build_market_host, MarketStartupError};
pub use diagnostic::{
    attach_binance_derivatives_source, attach_binance_spot_rest_source, attach_binance_spot_source,
};
