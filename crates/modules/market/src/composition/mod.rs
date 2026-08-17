mod assembly;
mod config;
mod diagnostic;
mod history;
mod process;
pub(crate) mod publication;
mod reference;
mod sources;

pub(crate) use assembly::{
    attach_binance_snapshot, attach_binance_stream, attach_massive_source_with_id, attach_stream,
};
pub use assembly::{
    attach_hyperliquid_live_source, attach_hyperliquid_snapshot_source,
    attach_massive_market_source, attach_okx_live_source, attach_okx_snapshot_source,
    default_endpoint, MarketProduct,
};
pub use config::{
    BinanceDerivativeProduct, BinanceDerivativeTransport, BinanceSpotTransport,
    HyperliquidMarketType, MarketConfig as MarketCompositionConfig, MarketProcessRequest,
    MarketReplayClock, MarketReplayConfig, MarketRuntimeProfile, MarketRuntimeScope,
    MarketSourceBinding, MassiveMarketProduct, OkxInstrumentType, PublicMarketTransport,
};
pub use diagnostic::{
    attach_binance_derivatives_source, attach_binance_spot_rest_source, attach_binance_spot_source,
};
pub use process::{build_market_process, MarketStartupError};
pub use publication::MmapMarketChangePublisher;
pub(crate) use sources::ConfiguredMarketSourceActivator;
pub use sources::{
    attach_replay_source, attach_replay_source_with_checkpoint, attach_replay_source_with_policy,
};
