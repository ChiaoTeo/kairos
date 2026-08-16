//! Binance participant-native connection facade.

mod account;
mod config;
mod connection;
mod execution;
mod market_data;
mod market_snapshot;
pub(crate) mod types;

pub use config::{
    BinanceFuturesChannelConfig, BinanceFuturesConnectionConfig, BinanceMarginChannelConfig,
    BinanceOptionsChannelConfig, BinanceOptionsConnectionConfig, BinancePrincipalConfig,
    BinancePrincipalOrderQuotaAllocation, BinanceQuotaAllocation, BinanceSharedQuotaConfig,
    BinanceSpotChannelConfig, BinanceSpotConnectionConfig,
};
pub use connection::{
    BinanceCoinMConnection, BinanceEquityInstrumentCatalog, BinanceEquityMarketQuote,
    BinanceFundingAccountRead, BinanceFundingCredentialInspection, BinanceFuturesAccountEvents,
    BinanceFuturesAccountRead, BinanceFuturesCredentialInspection, BinanceFuturesOrderEntry,
    BinanceFuturesOrderEvents, BinanceFuturesOrderQuery, BinanceFuturesPrincipalConnection,
    BinanceInstrumentCatalog, BinanceMarginAccountEvents, BinanceMarginAccountRead,
    BinanceMarginCredentialInspection, BinanceMarginOrderEntry, BinanceMarginOrderEvents,
    BinanceMarginOrderQuery, BinanceMarginPrincipalConnection, BinanceOptionsAccountEvents,
    BinanceOptionsAccountRead, BinanceOptionsConnection, BinanceOptionsCredentialInspection,
    BinanceOptionsOrderEntry, BinanceOptionsOrderEvents, BinanceOptionsOrderQuery,
    BinanceOptionsPrincipalConnection, BinanceSimpleEarn, BinanceSpotAccountEvents,
    BinanceSpotAccountMarketProfile, BinanceSpotAccountRead, BinanceSpotConnection,
    BinanceSpotOrderEntry, BinanceSpotOrderEvents, BinanceSpotOrderQuery,
    BinanceSpotPrincipalConnection, BinanceTransfer, BinanceUsdMConnection,
};
pub use market_data::{
    futures_websocket_market, options_websocket_market, spot_historical_market,
    spot_websocket_market, BinanceAsyncMarket, BinanceSpotHistoricalMarket,
};
pub use market_snapshot::{derivatives_snapshot, spot_snapshot, BinanceAsyncMarketSnapshot};
pub use types::{ConnectionDomain, InstrumentType};

pub mod blocking {
    pub use super::account::{
        blocking_futures_account as futures_account,
        blocking_futures_account_stream as futures_account_stream,
        blocking_futures_credential_inspection as futures_credential_inspection,
        blocking_margin_account as margin_account,
        blocking_margin_account_stream as margin_account_stream,
        blocking_margin_credential_inspection as margin_credential_inspection,
        blocking_options_account as options_account,
        blocking_options_credential_inspection as options_credential_inspection,
        blocking_spot_account as spot_account, blocking_spot_account_stream as spot_account_stream,
        blocking_spot_credential_inspection as spot_credential_inspection,
        blocking_spot_market_profile as spot_market_profile,
    };
    pub use super::connection::blocking::*;
    pub use super::execution::blocking_order_query as order_query;
    pub use super::market_data::blocking_spot_historical_market as spot_historical_market;
}
