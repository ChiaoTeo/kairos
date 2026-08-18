use super::defaults::*;
use serde::Deserialize;

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(tag = "type", rename_all = "kebab-case")]
pub enum MarketSourceBinding {
    BinanceSpot {
        #[serde(default = "enabled_by_default")]
        enabled: bool,
        #[serde(default)]
        transport: BinanceSpotTransport,
        endpoint: Option<String>,
        #[serde(default = "default_source_snapshot_interval_ms")]
        snapshot_interval_ms: u64,
    },
    BinanceEquity {
        #[serde(default = "enabled_by_default")]
        enabled: bool,
        credential_id: String,
        endpoint: Option<String>,
        #[serde(default = "default_source_snapshot_interval_ms")]
        snapshot_interval_ms: u64,
    },
    BinanceDerivatives {
        #[serde(default = "enabled_by_default")]
        enabled: bool,
        product: BinanceDerivativeProduct,
        #[serde(default)]
        transport: BinanceDerivativeTransport,
        endpoint: Option<String>,
        #[serde(default = "default_source_snapshot_interval_ms")]
        snapshot_interval_ms: u64,
    },
    Massive {
        #[serde(default = "enabled_by_default")]
        enabled: bool,
        product: MassiveMarketProduct,
        credential_id: String,
        endpoint: Option<String>,
    },
    Okx {
        #[serde(default = "enabled_by_default")]
        enabled: bool,
        instrument_type: OkxInstrumentType,
        #[serde(default)]
        transport: PublicMarketTransport,
        endpoint: Option<String>,
        #[serde(default = "default_source_snapshot_interval_ms")]
        snapshot_interval_ms: u64,
    },
    Hyperliquid {
        #[serde(default = "enabled_by_default")]
        enabled: bool,
        market_type: HyperliquidMarketType,
        #[serde(default)]
        transport: PublicMarketTransport,
        endpoint: Option<String>,
        #[serde(default = "default_source_snapshot_interval_ms")]
        snapshot_interval_ms: u64,
    },
    Ibkr {
        #[serde(default = "enabled_by_default")]
        enabled: bool,
        host: String,
        port: u16,
        client_id: i32,
        exchange: String,
        currency: String,
        #[serde(default = "default_source_snapshot_interval_ms")]
        snapshot_interval_ms: u64,
    },
}

impl MarketSourceBinding {
    pub fn enabled(&self) -> bool {
        match self {
            Self::BinanceSpot { enabled, .. }
            | Self::BinanceEquity { enabled, .. }
            | Self::BinanceDerivatives { enabled, .. }
            | Self::Massive { enabled, .. }
            | Self::Okx { enabled, .. }
            | Self::Hyperliquid { enabled, .. }
            | Self::Ibkr { enabled, .. } => *enabled,
        }
    }
}

#[derive(Debug, Clone, Copy, Default, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum BinanceSpotTransport {
    Rest,
    #[default]
    Websocket,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum BinanceDerivativeProduct {
    UsdMFutures,
    CoinMFutures,
    Options,
}

#[derive(Debug, Clone, Copy, Default, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum BinanceDerivativeTransport {
    #[default]
    Rest,
    Websocket,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum MassiveMarketProduct {
    Equity,
    Options,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum OkxInstrumentType {
    Spot,
    Swap,
    Futures,
    Options,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum HyperliquidMarketType {
    Spot,
    Perpetual,
}

#[derive(Debug, Clone, Copy, Default, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum PublicMarketTransport {
    Rest,
    #[default]
    Websocket,
}
