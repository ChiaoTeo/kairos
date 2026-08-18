//! Runtime and provider source configuration at the Composition boundary.

mod defaults;
mod dto;
mod profile;
mod sources;

pub use dto::{MarketCollectionConfig, MarketConfig};
pub use profile::{
    MarketHostRequest, MarketReplayClock, MarketReplayConfig, MarketRuntimeProfile,
    MarketRuntimeScope,
};
pub use sources::{
    BinanceDerivativeProduct, BinanceDerivativeTransport, BinanceSpotTransport,
    HyperliquidMarketType, MarketSourceBinding, MassiveMarketProduct, OkxInstrumentType,
    PublicMarketTransport,
};
