mod config;
mod history;
mod host;
mod launch;
mod reference;
mod sources;

pub use config::{
    BinanceDerivativeProduct, BinanceDerivativeTransport, BinanceSpotTransport,
    HyperliquidMarketType, MarketConfig as MarketCompositionConfig, MarketHostRequest,
    MarketReplayClock, MarketReplayConfig, MarketRuntimeProfile, MarketRuntimeScope,
    MarketSourceBinding, MassiveMarketProduct, OkxInstrumentType, PublicMarketTransport,
};
pub use host::MarketHost;
pub use launch::{build_market_host, run_diagnostic_once, DiagnosticProvider, MarketStartupError};
pub use sources::default_endpoint;
pub use sources::{
    attach_replay_source, attach_replay_source_with_checkpoint, attach_replay_source_with_policy,
};
