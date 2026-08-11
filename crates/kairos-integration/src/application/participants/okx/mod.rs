//! OKX participant-native connection facade and capability projections.

mod config;
mod connection;
mod types;

pub use config::{
    OkxConnectionConfig, OkxPrincipalConfig, OkxPrincipalOrderQuotaAllocation,
    OkxPrincipalQuotaAllocation, OkxPrivateChannelConfig, OkxSharedQuotaConfig,
};
pub use connection::{
    OkxConnection, OkxInstrumentCatalog, OkxLiveMarket, OkxMarketSnapshot, OkxPrincipalConnection,
    OkxTradingAccountEvents, OkxTradingAccountMarketProfile, OkxTradingAccountRead,
    OkxTradingCredentialInspection, OkxTradingOrderEntry, OkxTradingOrderEvents,
    OkxTradingOrderQuery,
};
pub use types::{ConnectionDomain, InstrumentType, TradingMode};

pub mod blocking {
    pub use super::connection::blocking::*;
}
