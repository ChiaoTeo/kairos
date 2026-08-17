use std::{fmt, str::FromStr};

use serde::{Deserialize, Serialize};

use crate::DomainTypeError;

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
    Active,
    Trading,
    Delisted,
    Inactive,
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
            Self::Active => "active",
            Self::Trading => "trading",
            Self::Delisted => "delisted",
            Self::Inactive => "inactive",
            Self::Unknown => "unknown",
        }
    }
}

impl From<&str> for ReferenceStatus {
    fn from(value: &str) -> Self {
        match value.trim().to_ascii_lowercase().as_str() {
            "active" => Self::Active,
            "trading" => Self::Trading,
            "delisted" => Self::Delisted,
            "inactive" | "break" => Self::Inactive,
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
