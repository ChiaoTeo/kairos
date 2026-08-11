//! Binance participant-native connection facade.

mod account;
mod config;
mod connection;
mod execution;
mod market_data;
pub(crate) mod types;

pub use config::{
    BinanceConnectionConfig, BinancePrincipalConfig, BinancePrincipalOrderQuotaAllocation,
    BinanceQuotaAllocation, BinanceSharedQuotaConfig, BinanceSpotChannelConfig,
};
pub use connection::{
    BinanceConnection, BinanceEquityInstrumentCatalog, BinanceFundingAccountRead,
    BinanceFundingCredentialInspection, BinanceInstrumentCatalog, BinancePrincipalConnection,
    BinanceSimpleEarn, BinanceSpotAccountEvents, BinanceSpotAccountMarketProfile,
    BinanceSpotAccountRead, BinanceSpotOrderEntry, BinanceSpotOrderEvents, BinanceSpotOrderQuery,
    BinanceTransfer,
};
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
    pub use super::execution::{
        blocking_equity_order_entry as equity_order_entry,
        blocking_equity_order_query as equity_order_query,
        blocking_futures_order_entry as futures_order_entry,
        blocking_margin_order_entry as margin_order_entry,
        blocking_options_order_entry as options_order_entry, blocking_order_query as order_query,
    };
    pub use super::market_data::{
        blocking_derivatives_rest_market as derivatives_rest_market,
        blocking_equity_rest_market as equity_rest_market,
        blocking_options_websocket_market as options_websocket_market,
        blocking_spot_historical_market as spot_historical_market,
        blocking_spot_rest_market as spot_rest_market,
        blocking_spot_websocket_market as spot_websocket_market,
    };
}
