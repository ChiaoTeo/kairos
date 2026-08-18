//! Concrete Massive REST and asset-class WebSocket connections.

mod config;
mod currencies;
mod futures;
mod indices;
mod rest;
mod types;
mod websocket;

pub use config::{
    MassiveCurrenciesRestConfig, MassiveCurrencyMarket, MassiveFuturesRestConfig,
    MassiveIndicesRestConfig, MassiveRestConfig, MassiveWebSocketConfig,
};
pub use currencies::MassiveCurrenciesRestConnection;
pub use futures::MassiveFuturesRestConnection;
pub use indices::MassiveIndicesRestConnection;
pub use rest::MassiveRestConnection;
pub use types::{
    InstrumentQuery, InstrumentType, MassiveCashDividend, MassiveIndexDefinition,
    MassiveOptionSnapshot,
};
pub use websocket::{
    MassiveCryptoWebSocketConnection, MassiveForexWebSocketConnection,
    MassiveFuturesWebSocketConnection, MassiveIndicesWebSocketConnection,
    MassiveOptionsWebSocketConnection, MassiveStocksWebSocketConnection,
};
