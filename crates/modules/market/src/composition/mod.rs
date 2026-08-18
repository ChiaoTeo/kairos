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
pub use launch::{
    attach_binance_derivatives_source, attach_binance_spot_rest_source, attach_binance_spot_source,
    build_market_host, MarketStartupError,
};
pub use sources::{
    attach_hyperliquid_live_source, attach_hyperliquid_snapshot_source,
    attach_massive_market_source, attach_okx_live_source, attach_okx_snapshot_source,
    default_endpoint, MarketProduct,
};
pub use sources::{
    attach_replay_source, attach_replay_source_with_checkpoint, attach_replay_source_with_policy,
};
