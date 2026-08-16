//! Explicit synchronous facades.
//!
//! Do not call these APIs from a Tokio runtime worker. The default provider
//! namespace is async-first; blocking adapters remain for synchronous callers
//! and provider SDKs that require a dedicated driver thread.

pub use crate::application::capabilities::account::{
    AccountCredentialInspectionConnection, AccountEventReceive, AccountEventStreamConnection,
    AccountMarketProfileConnection, AccountReadConnection, BufferedIntegrationAccountStream,
    IntegrationAccountStream,
};
pub use crate::application::capabilities::execution::{
    OrderEntryConnection, OrderEventSource, OrderQueryConnection,
};
pub use crate::application::capabilities::market::HistoricalMarketDataConnection;
pub use crate::application::capabilities::reference::InstrumentCatalogConnection;

pub use crate::application::participants::binance::blocking::{
    BinanceFundingAccountRead, BinanceFundingCredentialInspection, BinanceInstrumentCatalog,
    BinanceSimpleEarn, BinanceSpotOrderEntry, BinanceSpotOrderEvents, BinanceSpotOrderQuery,
    BinanceTransfer,
};
pub use crate::application::participants::hyperliquid::blocking::HyperliquidInstrumentCatalog;
pub use crate::application::participants::massive::blocking::{
    MassiveHistoricalMarket, MassiveInstrumentCatalog,
};
pub use crate::application::participants::okx::blocking::{
    OkxInstrumentCatalog, OkxTradingAccountEvents, OkxTradingAccountMarketProfile,
    OkxTradingAccountRead, OkxTradingCredentialInspection, OkxTradingOrderEntry,
    OkxTradingOrderQuery,
};

pub use crate::application::participants::binance::blocking as binance;
pub use crate::application::participants::hyperliquid::blocking as hyperliquid;
pub use crate::application::participants::ibkr::blocking as ibkr;
pub use crate::application::participants::massive::blocking as massive;
pub use crate::application::participants::okx::blocking as okx;
