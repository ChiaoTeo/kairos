use std::fmt;
use std::str::FromStr;

use serde::{Deserialize, Serialize};

use crate::DomainTypeError;
use crate::text::text_type;

text_type!(Symbol);
text_type!(Exchange);
text_type!(AssetId);
text_type!(ListingId);
text_type!(IssuerId);
text_type!(MarketSegmentId);
text_type!(TradingSessionId);
text_type!(TradingCalendarId);
text_type!(Currency);
text_type!(InstrumentId);
text_type!(MarketId);

macro_rules! legacy_default {
    ($name:ident, $value:literal) => {
        impl Default for $name {
            fn default() -> Self {
                Self::new($value).expect("legacy unresolved reference identity is valid")
            }
        }
    };
}

impl Default for Exchange {
    fn default() -> Self {
        Self::new("exchange:unknown").expect("canonical default exchange is valid")
    }
}

legacy_default!(InstrumentId, "instrument:unresolved");
legacy_default!(ListingId, "listing:unresolved");
legacy_default!(MarketId, "market:unresolved");
legacy_default!(Symbol, "symbol:unresolved");
legacy_default!(AssetId, "asset:unresolved");

fn exchange_key(exchange: &Exchange) -> &str {
    exchange.as_str().trim_start_matches("exchange:")
}

impl InstrumentId {
    /// Canonical spot identity: the instrument is the base asset, not a quote pair.
    pub fn spot(base_asset: impl AsRef<str>) -> Result<Self, DomainTypeError> {
        Self::new(format!(
            "instrument:spot:{}",
            base_asset.as_ref().to_ascii_uppercase()
        ))
    }
}

impl ListingId {
    /// Canonical venue listing identity. Quote/currency context belongs to a market.
    pub fn venue(
        exchange: &Exchange,
        kind: InstrumentKind,
        listing_key: impl AsRef<str>,
    ) -> Result<Self, DomainTypeError> {
        Self::new(format!(
            "listing:{}:{}:{}",
            exchange_key(exchange),
            kind.as_str(),
            listing_key.as_ref().to_ascii_uppercase()
        ))
    }

    /// Canonical spot listing identity, including its exchange and quote context.
    pub fn spot(
        exchange: &Exchange,
        base_asset: impl AsRef<str>,
        quote_asset: impl AsRef<str>,
    ) -> Result<Self, DomainTypeError> {
        Self::new(format!(
            "listing:{}:spot:{}:{}",
            exchange.as_str().trim_start_matches("exchange:"),
            base_asset.as_ref().to_ascii_uppercase(),
            quote_asset.as_ref().to_ascii_uppercase()
        ))
    }
}

impl MarketId {
    /// Canonical venue market identity for an observable/tradable entry point.
    pub fn venue(
        exchange: &Exchange,
        kind: InstrumentKind,
        market_key: impl AsRef<str>,
    ) -> Result<Self, DomainTypeError> {
        Self::new(format!(
            "market:{}:{}:{}",
            exchange_key(exchange),
            kind.as_str(),
            market_key.as_ref().to_ascii_uppercase()
        ))
    }

    /// Canonical spot market identity retains the venue symbol.
    pub fn spot(
        exchange: &Exchange,
        venue_symbol: impl AsRef<str>,
    ) -> Result<Self, DomainTypeError> {
        Self::venue(exchange, InstrumentKind::Spot, venue_symbol)
    }
}

/// Canonical economic lifecycle of a tradable instrument.
#[derive(
    Clone, Copy, Debug, Default, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize,
)]
#[serde(rename_all = "kebab-case")]
pub enum InstrumentKind {
    Equity,
    Spot,
    Perpetual,
    Future,
    Option,
    Index,
    #[default]
    Unknown,
}

impl InstrumentKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Equity => "equity",
            Self::Spot => "spot",
            Self::Perpetual => "perpetual",
            Self::Future => "future",
            Self::Option => "option",
            Self::Index => "index",
            Self::Unknown => "unknown",
        }
    }

    /// Parses persisted/wire data. Unknown spellings fail instead of entering
    /// domain state; the explicit `Unknown` value is reserved for wire evolution.
    pub fn parse_known(value: &str) -> Result<Self, DomainTypeError> {
        match value {
            "equity" => Ok(Self::Equity),
            "spot" => Ok(Self::Spot),
            "perpetual" => Ok(Self::Perpetual),
            "future" => Ok(Self::Future),
            "option" => Ok(Self::Option),
            "index" => Ok(Self::Index),
            _ => Err(DomainTypeError::Invalid {
                type_name: "InstrumentKind",
                reason: "unknown canonical instrument kind",
            }),
        }
    }
}

impl fmt::Display for InstrumentKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl FromStr for InstrumentKind {
    type Err = DomainTypeError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        Self::parse_known(value)
    }
}

impl PartialEq<str> for InstrumentKind {
    fn eq(&self, other: &str) -> bool {
        self.as_str() == other
    }
}

impl PartialEq<&str> for InstrumentKind {
    fn eq(&self, other: &&str) -> bool {
        self.as_str() == *other
    }
}

/// Canonical class of an asset, independent of a provider product surface.
#[derive(
    Clone, Copy, Debug, Default, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize,
)]
#[serde(rename_all = "kebab-case")]
pub enum AssetClass {
    Fiat,
    Crypto,
    Equity,
    #[default]
    Unknown,
}

impl AssetClass {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Fiat => "fiat",
            Self::Crypto => "crypto",
            Self::Equity => "equity",
            Self::Unknown => "unknown",
        }
    }

    pub fn parse_known(value: &str) -> Result<Self, DomainTypeError> {
        match value {
            "fiat" => Ok(Self::Fiat),
            "crypto" => Ok(Self::Crypto),
            "equity" => Ok(Self::Equity),
            _ => Err(DomainTypeError::Invalid {
                type_name: "AssetClass",
                reason: "unknown canonical asset class",
            }),
        }
    }
}

impl fmt::Display for AssetClass {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl FromStr for AssetClass {
    type Err = DomainTypeError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        Self::parse_known(value)
    }
}

impl PartialEq<str> for AssetClass {
    fn eq(&self, other: &str) -> bool {
        self.as_str() == other
    }
}

impl PartialEq<&str> for AssetClass {
    fn eq(&self, other: &&str) -> bool {
        self.as_str() == *other
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceStatus {
    Draft,
    Active,
    Trading,
    Suspended,
    Delisted,
    Inactive,
    Retired,
    Expired,
    #[default]
    Unknown,
}

impl<'de> Deserialize<'de> for ReferenceStatus {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        String::deserialize(deserializer).map(Self::from)
    }
}

impl ReferenceStatus {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Draft => "draft",
            Self::Active => "active",
            Self::Trading => "trading",
            Self::Suspended => "suspended",
            Self::Delisted => "delisted",
            Self::Inactive => "inactive",
            Self::Retired => "retired",
            Self::Expired => "expired",
            Self::Unknown => "unknown",
        }
    }
}

impl From<&str> for ReferenceStatus {
    fn from(value: &str) -> Self {
        match value.trim().to_ascii_lowercase().as_str() {
            "draft" => Self::Draft,
            "active" => Self::Active,
            "trading" => Self::Trading,
            "suspended" => Self::Suspended,
            "delisted" => Self::Delisted,
            "inactive" | "break" => Self::Inactive,
            "retired" => Self::Retired,
            "expired" => Self::Expired,
            _ => Self::Unknown,
        }
    }
}

impl From<String> for ReferenceStatus {
    fn from(value: String) -> Self {
        Self::from(value.as_str())
    }
}

impl fmt::Display for ReferenceStatus {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}
