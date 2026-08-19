mod assembly;
mod diagnostic;

pub use assembly::{MarketStartupError, build_market_host};
pub use diagnostic::{DiagnosticProvider, run_diagnostic_once};
