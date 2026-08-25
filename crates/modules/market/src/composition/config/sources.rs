use std::collections::BTreeMap;
use std::ops::{Deref, DerefMut};

use serde::{Deserialize, Deserializer};

use super::defaults::*;

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(tag = "type", rename_all = "kebab-case")]
pub enum MarketProviderBinding {
    BinanceSpot {
        #[serde(default = "enabled_by_default")]
        enabled: bool,
        connection_id: Option<String>,
        #[serde(default)]
        transport: BinanceSpotTransport,
        endpoint: Option<String>,
        #[serde(default = "default_source_snapshot_interval_ms")]
        snapshot_interval_ms: u64,
    },
    BinanceEquity {
        #[serde(default = "enabled_by_default")]
        enabled: bool,
        connection_id: Option<String>,
        credential_id: Option<String>,
        endpoint: Option<String>,
        #[serde(default = "default_source_snapshot_interval_ms")]
        snapshot_interval_ms: u64,
    },
    BinanceDerivatives {
        #[serde(default = "enabled_by_default")]
        enabled: bool,
        connection_id: Option<String>,
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
        connection_id: Option<String>,
        product: MassiveMarketProduct,
        credential_id: Option<String>,
        endpoint: Option<String>,
    },
    Okx {
        #[serde(default = "enabled_by_default")]
        enabled: bool,
        connection_id: Option<String>,
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

/// Provider configuration without user-defined runtime feed names.
/// Stable operational keys are derived privately from provider configuration.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct MarketProviderBindings(BTreeMap<String, MarketProviderBinding>);

impl<'de> Deserialize<'de> for MarketProviderBindings {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let bindings = Vec::<MarketProviderBinding>::deserialize(deserializer)?;
        let mut resolved = BTreeMap::new();
        for binding in bindings {
            let base = binding.feed_base_id();
            let mut key = base.clone();
            let mut ordinal = 2_u32;
            while resolved.contains_key(&key) {
                key = format!("{base}-{ordinal}");
                ordinal = ordinal.saturating_add(1);
            }
            resolved.insert(key, binding);
        }
        Ok(Self(resolved))
    }
}

impl Deref for MarketProviderBindings {
    type Target = BTreeMap<String, MarketProviderBinding>;

    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

impl DerefMut for MarketProviderBindings {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.0
    }
}

impl MarketProviderBindings {
    pub fn into_values(
        self,
    ) -> std::collections::btree_map::IntoValues<String, MarketProviderBinding> {
        self.0.into_values()
    }
}

impl<'a> IntoIterator for &'a MarketProviderBindings {
    type Item = (&'a String, &'a MarketProviderBinding);
    type IntoIter = std::collections::btree_map::Iter<'a, String, MarketProviderBinding>;

    fn into_iter(self) -> Self::IntoIter {
        self.0.iter()
    }
}

impl MarketProviderBinding {
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

    pub fn connection_id(&self) -> Option<&str> {
        match self {
            Self::BinanceSpot { connection_id, .. }
            | Self::BinanceEquity { connection_id, .. }
            | Self::BinanceDerivatives { connection_id, .. }
            | Self::Massive { connection_id, .. }
            | Self::Okx { connection_id, .. } => connection_id.as_deref(),
            Self::Hyperliquid { .. } | Self::Ibkr { .. } => None,
        }
    }

    fn feed_base_id(&self) -> String {
        let (provider, segment) = match self {
            Self::BinanceSpot { .. } => ("binance", "spot"),
            Self::BinanceEquity { .. } => ("binance", "equity"),
            Self::BinanceDerivatives { product, .. } => (
                "binance",
                match product {
                    BinanceDerivativeProduct::UsdMFutures => "usd-m-futures",
                    BinanceDerivativeProduct::CoinMFutures => "coin-m-futures",
                    BinanceDerivativeProduct::Options => "options",
                },
            ),
            Self::Massive { product, .. } => (
                "massive",
                match product {
                    MassiveMarketProduct::Equity => "equity",
                    MassiveMarketProduct::Options => "options",
                },
            ),
            Self::Okx {
                instrument_type, ..
            } => (
                "okx",
                match instrument_type {
                    OkxInstrumentType::Spot => "spot",
                    OkxInstrumentType::Swap => "swap",
                    OkxInstrumentType::Futures => "futures",
                    OkxInstrumentType::Options => "options",
                },
            ),
            Self::Hyperliquid { market_type, .. } => (
                "hyperliquid",
                match market_type {
                    HyperliquidMarketType::Spot => "spot",
                    HyperliquidMarketType::Perpetual => "perpetual",
                },
            ),
            Self::Ibkr { .. } => ("ibkr", "equity"),
        };
        format!("{provider}-{segment}")
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
