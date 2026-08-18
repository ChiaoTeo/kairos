mod assembly;
mod diagnostic;

pub use assembly::{build_market_host, MarketStartupError};
pub use diagnostic::{run_diagnostic_once, DiagnosticProvider};
