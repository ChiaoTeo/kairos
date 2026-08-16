//! Small, infrastructure-free value objects shared by business domains.
//!
//! Wire formats deliberately remain outside this crate.  Adapters should use
//! the fallible constructors and explicit accessors at contract boundaries.

use std::{fmt, str::FromStr};

use rust_decimal::Decimal as RustDecimal;
use serde::{Deserialize, Serialize};

/// Domain decimals use an `i64` coefficient, so at most 18 fractional digits
/// can be represented without pretending that additional precision exists.
pub const MAX_DECIMAL_SCALE: u8 = 18;

fn normalize_decimal_parts(
    mut mantissa: i64,
    mut scale: u8,
    type_name: &'static str,
) -> Result<(i64, u8), DomainTypeError> {
    if scale > MAX_DECIMAL_SCALE {
        return Err(DomainTypeError::Invalid {
            type_name,
            reason: "decimal scale exceeds 18 digits",
        });
    }
    if mantissa == 0 {
        return Ok((0, 0));
    }
    while scale > 0 && mantissa % 10 == 0 {
        mantissa /= 10;
        scale -= 1;
    }
    Ok((mantissa, scale))
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum DomainTypeError {
    Empty {
        type_name: &'static str,
    },
    Whitespace {
        type_name: &'static str,
    },
    Invalid {
        type_name: &'static str,
        reason: &'static str,
    },
    NonPositive {
        type_name: &'static str,
    },
}

impl fmt::Display for DomainTypeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Empty { type_name } => write!(f, "{type_name} cannot be empty"),
            Self::Whitespace { type_name } => {
                write!(
                    f,
                    "{type_name} cannot contain leading or trailing whitespace"
                )
            }
            Self::Invalid { type_name, reason } => write!(f, "invalid {type_name}: {reason}"),
            Self::NonPositive { type_name } => write!(f, "{type_name} must be positive"),
        }
    }
}

impl std::error::Error for DomainTypeError {}

impl From<DomainTypeError> for String {
    fn from(error: DomainTypeError) -> Self {
        error.to_string()
    }
}

macro_rules! text_type {
    ($name:ident) => {
        #[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Hash, Serialize, Deserialize)]
        #[serde(transparent)]
        pub struct $name(String);

        impl $name {
            pub fn new(value: impl Into<String>) -> Result<Self, DomainTypeError> {
                let value = value.into();
                if value.is_empty() {
                    return Err(DomainTypeError::Empty {
                        type_name: stringify!($name),
                    });
                }
                if value.trim() != value {
                    return Err(DomainTypeError::Whitespace {
                        type_name: stringify!($name),
                    });
                }
                Ok(Self(value))
            }

            pub fn as_str(&self) -> &str {
                &self.0
            }

            pub fn into_inner(self) -> String {
                self.0
            }
        }

        impl AsRef<str> for $name {
            fn as_ref(&self) -> &str {
                self.as_str()
            }
        }

        impl std::borrow::Borrow<str> for $name {
            fn borrow(&self) -> &str {
                self.as_str()
            }
        }

        impl std::borrow::Borrow<String> for $name {
            fn borrow(&self) -> &String {
                &self.0
            }
        }

        impl std::ops::Deref for $name {
            type Target = str;

            fn deref(&self) -> &Self::Target {
                self.as_str()
            }
        }

        impl PartialEq<str> for $name {
            fn eq(&self, other: &str) -> bool {
                self.as_str() == other
            }
        }

        impl PartialEq<String> for $name {
            fn eq(&self, other: &String) -> bool {
                self.as_str() == other
            }
        }

        impl PartialEq<&str> for $name {
            fn eq(&self, other: &&str) -> bool {
                self.as_str() == *other
            }
        }

        impl PartialEq<$name> for String {
            fn eq(&self, other: &$name) -> bool {
                self == other.as_str()
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str(self.as_str())
            }
        }

        impl TryFrom<String> for $name {
            type Error = DomainTypeError;

            fn try_from(value: String) -> Result<Self, Self::Error> {
                Self::new(value)
            }
        }

        impl TryFrom<&str> for $name {
            type Error = DomainTypeError;

            fn try_from(value: &str) -> Result<Self, Self::Error> {
                Self::new(value)
            }
        }
    };
}

text_type!(Symbol);
text_type!(Exchange);
impl Default for Exchange {
    fn default() -> Self {
        Self::new("exchange:unknown").expect("canonical default exchange is valid")
    }
}
// Canonical identity of an asset in the Reference catalog.
text_type!(AssetId);
// Canonical identity of a listing relationship in the Reference catalog.
text_type!(ListingId);
// Canonical identity of an execution route in the Reference catalog.
text_type!(ExecutionAccessId);
// Provider-owned symbol. It is valid only at an integration boundary.
text_type!(ProviderSymbol);
// Stable provider identity shared by Reference access records and composition.
text_type!(ProviderId);
// Opaque provider-owned product discriminator. This is deliberately not a
// global product taxonomy (examples include `swap` and `usd-m-futures`).
text_type!(ProviderProductCode);
// Issuer identity used by securities reference data.
text_type!(IssuerId);
// Exchange segment identity used by securities reference data.
text_type!(MarketSegmentId);
// Trading-session identity.
text_type!(TradingSessionId);
// Trading-calendar identity.
text_type!(TradingCalendarId);
text_type!(AccountId);
text_type!(OrderId);
text_type!(ClientOrderId);
text_type!(Currency);
text_type!(InstrumentId);
text_type!(MarketId);
text_type!(SegmentKey);
text_type!(IntentId);
text_type!(PlanId);
text_type!(LegId);
text_type!(FillId);
text_type!(RemoteOrderId);
text_type!(StrategyId);
text_type!(PolicyId);
text_type!(RequestId);
text_type!(IdempotencyKey);
text_type!(ReservationId);
text_type!(DecisionId);
text_type!(ActorId);

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

macro_rules! default_text_type {
    ($name:ident, $value:literal) => {
        impl Default for $name {
            fn default() -> Self {
                Self::new($value).expect("static default domain identity")
            }
        }
    };
}

default_text_type!(InstrumentId, "instrument:unresolved");
default_text_type!(ListingId, "listing:unresolved");
default_text_type!(MarketId, "market:unresolved");
default_text_type!(ExecutionAccessId, "access:unresolved");
default_text_type!(Symbol, "symbol:unresolved");
default_text_type!(AssetId, "asset:unresolved");
default_text_type!(ProviderId, "provider:unknown");
default_text_type!(ProviderProductCode, "unknown");

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
    /// Canonical Binance-style spot market identity retains the provider symbol.
    pub fn spot(
        exchange: &Exchange,
        provider_symbol: impl AsRef<str>,
    ) -> Result<Self, DomainTypeError> {
        Self::new(format!(
            "market:{}:spot:{}",
            exchange.as_str().trim_start_matches("exchange:"),
            provider_symbol.as_ref().to_ascii_uppercase()
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::{Exchange, InstrumentId, ListingId, MarketId};

    #[test]
    fn spot_identity_keeps_asset_and_market_context_separate() {
        let exchange = Exchange::new("exchange:binance").unwrap();

        assert_eq!(
            InstrumentId::spot("btc").unwrap().as_str(),
            "instrument:spot:BTC"
        );
        assert_eq!(
            ListingId::spot(&exchange, "btc", "usdt").unwrap().as_str(),
            "listing:binance:spot:BTC:USDT"
        );
        assert_eq!(
            MarketId::spot(&exchange, "btcusdt").unwrap().as_str(),
            "market:binance:spot:BTCUSDT"
        );
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Hash)]
pub struct Quantity {
    mantissa: i64,
    scale: u8,
}

/// A signed asset/inventory quantity. Order quantities use `Quantity`; this
/// type is reserved for positions and balance deltas that may cross zero.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Hash)]
pub struct SignedQuantity {
    mantissa: i64,
    scale: u8,
}

impl SignedQuantity {
    pub const ZERO: Self = Self {
        mantissa: 0,
        scale: 0,
    };

    pub fn new(mantissa: i64, scale: u8) -> Result<Self, DomainTypeError> {
        let (mantissa, scale) = normalize_decimal_parts(mantissa, scale, "SignedQuantity")?;
        Ok(Self { mantissa, scale })
    }

    pub const fn mantissa(self) -> i64 {
        self.mantissa
    }

    pub const fn scale(self) -> u8 {
        self.scale
    }
}

impl Quantity {
    pub const ZERO: Self = Self {
        mantissa: 0,
        scale: 0,
    };

    pub fn new(mantissa: i64, scale: u8) -> Result<Self, DomainTypeError> {
        if mantissa < 0 {
            return Err(DomainTypeError::Invalid {
                type_name: "Quantity",
                reason: "negative values are not allowed",
            });
        }
        let (mantissa, scale) = normalize_decimal_parts(mantissa, scale, "Quantity")?;
        Ok(Self { mantissa, scale })
    }

    pub fn positive(mantissa: i64, scale: u8) -> Result<Self, DomainTypeError> {
        let value = Self::new(mantissa, scale)?;
        if value.is_zero() {
            return Err(DomainTypeError::NonPositive {
                type_name: "Quantity",
            });
        }
        Ok(value)
    }

    pub const fn is_zero(self) -> bool {
        self.mantissa == 0
    }

    pub const fn is_positive(self) -> bool {
        self.mantissa > 0
    }

    pub const fn mantissa(self) -> i64 {
        self.mantissa
    }
    pub const fn scale(self) -> u8 {
        self.scale
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash)]
pub struct Price {
    mantissa: i64,
    scale: u8,
}

/// A signed difference between two prices. Unlike `Price`, zero and negative
/// values are valid because a delta describes a relationship, not a quote.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Hash)]
pub struct PriceDelta {
    mantissa: i64,
    scale: u8,
}

impl PriceDelta {
    pub const ZERO: Self = Self {
        mantissa: 0,
        scale: 0,
    };

    pub fn new(mantissa: i64, scale: u8) -> Result<Self, DomainTypeError> {
        let (mantissa, scale) = normalize_decimal_parts(mantissa, scale, "PriceDelta")?;
        Ok(Self { mantissa, scale })
    }

    pub const fn mantissa(self) -> i64 {
        self.mantissa
    }

    pub const fn scale(self) -> u8 {
        self.scale
    }
}

impl Price {
    pub const fn is_positive(self) -> bool {
        self.mantissa > 0
    }

    pub fn new(mantissa: i64, scale: u8) -> Result<Self, DomainTypeError> {
        if mantissa <= 0 {
            return Err(DomainTypeError::NonPositive { type_name: "Price" });
        }
        let (mantissa, scale) = normalize_decimal_parts(mantissa, scale, "Price")?;
        Ok(Self { mantissa, scale })
    }

    pub const fn mantissa(self) -> i64 {
        self.mantissa
    }
    pub const fn scale(self) -> u8 {
        self.scale
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Hash)]
pub struct Money {
    mantissa: i64,
    scale: u8,
}

impl Money {
    pub const ZERO: Self = Self {
        mantissa: 0,
        scale: 0,
    };

    pub fn new(mantissa: i64, scale: u8) -> Result<Self, DomainTypeError> {
        let (mantissa, scale) = normalize_decimal_parts(mantissa, scale, "Money")?;
        Ok(Self { mantissa, scale })
    }

    pub const fn mantissa(self) -> i64 {
        self.mantissa
    }

    pub const fn scale(self) -> u8 {
        self.scale
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Hash)]
pub struct Rate {
    mantissa: i64,
    scale: u8,
}

/// A positive rational configuration value used for proportional policies.
#[derive(
    Clone, Copy, Debug, Default, Eq, Ord, PartialEq, PartialOrd, Hash, Serialize, Deserialize,
)]
pub struct Ratio {
    numerator: u64,
    denominator: u64,
}

impl Ratio {
    pub fn new(numerator: u64, denominator: u64) -> Result<Self, DomainTypeError> {
        if numerator == 0 || denominator == 0 {
            return Err(DomainTypeError::NonPositive { type_name: "Ratio" });
        }
        Ok(Self {
            numerator,
            denominator,
        })
    }

    pub const fn numerator(self) -> u64 {
        self.numerator
    }
    pub const fn denominator(self) -> u64 {
        self.denominator
    }

    pub fn apply_to_nonnegative(self, value: i64) -> Result<i64, DomainTypeError> {
        if value < 0 {
            return Err(DomainTypeError::Invalid {
                type_name: "Ratio",
                reason: "cannot be applied to a negative value",
            });
        }
        let result = u128::try_from(value)
            .map_err(|_| DomainTypeError::Invalid {
                type_name: "Ratio",
                reason: "value is too large",
            })?
            .checked_mul(u128::from(self.numerator))
            .ok_or(DomainTypeError::Invalid {
                type_name: "Ratio",
                reason: "arithmetic overflow",
            })?
            / u128::from(self.denominator);
        i64::try_from(result).map_err(|_| DomainTypeError::Invalid {
            type_name: "Ratio",
            reason: "arithmetic overflow",
        })
    }
}

impl Rate {
    pub const ZERO: Self = Self {
        mantissa: 0,
        scale: 0,
    };

    pub fn new(mantissa: i64, scale: u8) -> Result<Self, DomainTypeError> {
        let (mantissa, scale) = normalize_decimal_parts(mantissa, scale, "Rate")?;
        Ok(Self { mantissa, scale })
    }

    pub const fn mantissa(self) -> i64 {
        self.mantissa
    }

    pub const fn scale(self) -> u8 {
        self.scale
    }
}

macro_rules! fixed_decimal_impl {
    ($name:ident) => {
        impl std::str::FromStr for $name {
            type Err = DomainTypeError;

            fn from_str(value: &str) -> Result<Self, Self::Err> {
                let (whole, fraction) = value.split_once('.').unwrap_or((value, ""));
                if whole.is_empty()
                    || fraction.len() > 18
                    || !whole.bytes().all(|byte| byte.is_ascii_digit())
                    || !fraction.bytes().all(|byte| byte.is_ascii_digit())
                {
                    return Err(DomainTypeError::Invalid {
                        type_name: stringify!($name),
                        reason: "expected a non-negative decimal",
                    });
                }
                let scale = u8::try_from(fraction.len()).map_err(|_| DomainTypeError::Invalid {
                    type_name: stringify!($name),
                    reason: "decimal scale is too large",
                })?;
                let digits = format!("{whole}{fraction}");
                let mantissa = digits
                    .parse::<i64>()
                    .map_err(|_| DomainTypeError::Invalid {
                        type_name: stringify!($name),
                        reason: "decimal value is too large",
                    })?;
                Self::new(mantissa, scale)
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                if self.scale == 0 {
                    return write!(f, "{}", self.mantissa);
                }
                let factor = 10_i64.pow(u32::from(self.scale));
                let whole = self.mantissa / factor;
                let fraction = format!(
                    "{:0width$}",
                    self.mantissa % factor,
                    width = usize::from(self.scale)
                );
                write!(f, "{whole}.{fraction}")
            }
        }
    };
}

fixed_decimal_impl!(Quantity);
fixed_decimal_impl!(Price);

macro_rules! signed_fixed_decimal_impl {
    ($name:ident) => {
        impl std::str::FromStr for $name {
            type Err = DomainTypeError;

            fn from_str(value: &str) -> Result<Self, Self::Err> {
                let (negative, unsigned) = value
                    .strip_prefix('-')
                    .map_or((false, value), |value| (true, value));
                let (whole, fraction) = unsigned.split_once('.').unwrap_or((unsigned, ""));
                if whole.is_empty()
                    || fraction.len() > 18
                    || !whole.bytes().all(|byte| byte.is_ascii_digit())
                    || !fraction.bytes().all(|byte| byte.is_ascii_digit())
                {
                    return Err(DomainTypeError::Invalid {
                        type_name: stringify!($name),
                        reason: "expected a signed decimal",
                    });
                }
                let scale = u8::try_from(fraction.len()).map_err(|_| DomainTypeError::Invalid {
                    type_name: stringify!($name),
                    reason: "decimal scale is too large",
                })?;
                let digits = format!("{whole}{fraction}");
                let magnitude = digits
                    .parse::<i128>()
                    .map_err(|_| DomainTypeError::Invalid {
                        type_name: stringify!($name),
                        reason: "decimal value is too large",
                    })?;
                let mantissa = if negative { -magnitude } else { magnitude };
                let mantissa = i64::try_from(mantissa).map_err(|_| DomainTypeError::Invalid {
                    type_name: stringify!($name),
                    reason: "decimal value is too large",
                })?;
                Self::new(mantissa, scale)
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                let mantissa = i128::from(self.mantissa());
                let negative = mantissa < 0;
                let magnitude = mantissa.abs();
                if self.scale() == 0 {
                    return write!(f, "{}{magnitude}", if negative { "-" } else { "" });
                }
                let factor = 10_i128.pow(u32::from(self.scale()));
                let whole = magnitude / factor;
                let fraction = format!(
                    "{:0width$}",
                    magnitude % factor,
                    width = usize::from(self.scale())
                );
                write!(f, "{}{whole}.{fraction}", if negative { "-" } else { "" })
            }
        }
    };
}

signed_fixed_decimal_impl!(Money);
signed_fixed_decimal_impl!(Rate);
signed_fixed_decimal_impl!(PriceDelta);

signed_fixed_decimal_impl!(SignedQuantity);

macro_rules! decimal_value_semantics {
    ($name:ident) => {
        impl Serialize for $name {
            fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
            where
                S: serde::Serializer,
            {
                serializer.collect_str(self)
            }
        }

        impl<'de> Deserialize<'de> for $name {
            fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
            where
                D: serde::Deserializer<'de>,
            {
                let value = String::deserialize(deserializer)?;
                value.parse().map_err(serde::de::Error::custom)
            }
        }

        impl Ord for $name {
            fn cmp(&self, other: &Self) -> std::cmp::Ordering {
                decimal_value(self.mantissa, self.scale)
                    .expect("domain decimal invariant")
                    .cmp(
                        &decimal_value(other.mantissa, other.scale)
                            .expect("domain decimal invariant"),
                    )
            }
        }

        impl PartialOrd for $name {
            fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
                Some(self.cmp(other))
            }
        }
    };
}

decimal_value_semantics!(Quantity);
decimal_value_semantics!(SignedQuantity);
decimal_value_semantics!(Price);
decimal_value_semantics!(PriceDelta);
decimal_value_semantics!(Money);
decimal_value_semantics!(Rate);

fn decimal_value(mantissa: i64, scale: u8) -> Result<RustDecimal, DomainTypeError> {
    RustDecimal::try_new(mantissa, u32::from(scale)).map_err(|_| DomainTypeError::Invalid {
        type_name: "fixed decimal",
        reason: "decimal value is too large",
    })
}

fn decimal_parts(value: RustDecimal) -> Result<(i64, u8), DomainTypeError> {
    let mantissa = i64::try_from(value.mantissa()).map_err(|_| DomainTypeError::Invalid {
        type_name: "fixed decimal",
        reason: "decimal value is too large",
    })?;
    let scale = u8::try_from(value.scale()).map_err(|_| DomainTypeError::Invalid {
        type_name: "fixed decimal",
        reason: "decimal scale is too large",
    })?;
    Ok((mantissa, scale))
}

impl Quantity {
    pub fn checked_add(self, other: Self) -> Result<Self, DomainTypeError> {
        let (mantissa, scale) = decimal_parts(
            decimal_value(self.mantissa, self.scale)?
                .checked_add(decimal_value(other.mantissa, other.scale)?)
                .ok_or(DomainTypeError::Invalid {
                    type_name: "Quantity",
                    reason: "arithmetic overflow",
                })?,
        )?;
        Self::new(mantissa, scale)
    }

    pub fn checked_sub(self, other: Self) -> Result<Self, DomainTypeError> {
        let (mantissa, scale) = decimal_parts(
            decimal_value(self.mantissa, self.scale)?
                .checked_sub(decimal_value(other.mantissa, other.scale)?)
                .ok_or(DomainTypeError::Invalid {
                    type_name: "Quantity",
                    reason: "arithmetic overflow",
                })?,
        )?;
        Self::new(mantissa, scale)
    }

    pub fn cmp_value(self, other: Self) -> Result<std::cmp::Ordering, DomainTypeError> {
        Ok(decimal_value(self.mantissa, self.scale)?
            .cmp(&decimal_value(other.mantissa, other.scale)?))
    }

    pub fn is_multiple_of(self, increment: Self) -> Result<bool, DomainTypeError> {
        if increment.is_zero() {
            return Err(DomainTypeError::Invalid {
                type_name: "Quantity",
                reason: "increment cannot be zero",
            });
        }
        Ok((decimal_value(self.mantissa, self.scale)?
            % decimal_value(increment.mantissa, increment.scale)?)
        .is_zero())
    }

    pub fn checked_mul(self, price: Price) -> Result<Money, DomainTypeError> {
        let value = decimal_value(self.mantissa, self.scale)?
            .checked_mul(decimal_value(price.mantissa, price.scale)?)
            .ok_or(DomainTypeError::Invalid {
                type_name: "Money",
                reason: "arithmetic overflow",
            })?;
        let (mantissa, scale) = decimal_parts(value)?;
        Money::new(mantissa, scale)
    }
}

impl SignedQuantity {
    pub fn is_zero(self) -> bool {
        self.mantissa == 0
    }
    pub fn is_positive(self) -> bool {
        self.mantissa > 0
    }
    pub fn is_negative(self) -> bool {
        self.mantissa < 0
    }

    pub fn checked_neg(self) -> Result<Self, DomainTypeError> {
        self.mantissa
            .checked_neg()
            .ok_or(DomainTypeError::Invalid {
                type_name: "SignedQuantity",
                reason: "arithmetic overflow",
            })
            .and_then(|mantissa| Self::new(mantissa, self.scale))
    }

    pub fn checked_add(self, other: Self) -> Result<Self, DomainTypeError> {
        let value = decimal_value(self.mantissa, self.scale)?
            .checked_add(decimal_value(other.mantissa, other.scale)?)
            .ok_or(DomainTypeError::Invalid {
                type_name: "SignedQuantity",
                reason: "arithmetic overflow",
            })?;
        let (mantissa, scale) = decimal_parts(value)?;
        Self::new(mantissa, scale)
    }

    pub fn checked_sub(self, other: Self) -> Result<Self, DomainTypeError> {
        self.checked_add(other.checked_neg()?)
    }

    pub fn cmp_value(self, other: Self) -> Result<std::cmp::Ordering, DomainTypeError> {
        Ok(decimal_value(self.mantissa, self.scale)?
            .cmp(&decimal_value(other.mantissa, other.scale)?))
    }

    pub fn checked_mul(self, price: Price) -> Result<Money, DomainTypeError> {
        let value = decimal_value(self.mantissa, self.scale)?
            .checked_mul(decimal_value(price.mantissa, price.scale)?)
            .ok_or(DomainTypeError::Invalid {
                type_name: "Money",
                reason: "arithmetic overflow",
            })?;
        let (mantissa, scale) = decimal_parts(value)?;
        Money::new(mantissa, scale)
    }
}

impl Price {
    pub fn is_multiple_of(self, increment: Self) -> Result<bool, DomainTypeError> {
        Ok((decimal_value(self.mantissa, self.scale)?
            % decimal_value(increment.mantissa, increment.scale)?)
        .is_zero())
    }

    pub fn checked_sub(self, other: Self) -> Result<PriceDelta, DomainTypeError> {
        let value = decimal_value(self.mantissa, self.scale)?
            .checked_sub(decimal_value(other.mantissa, other.scale)?)
            .ok_or(DomainTypeError::Invalid {
                type_name: "PriceDelta",
                reason: "arithmetic overflow",
            })?;
        let (mantissa, scale) = decimal_parts(value)?;
        PriceDelta::new(mantissa, scale)
    }

    pub fn checked_mul(self, quantity: Quantity) -> Result<Money, DomainTypeError> {
        quantity.checked_mul(self)
    }

    pub fn checked_div(self, quantity: Quantity) -> Result<Self, DomainTypeError> {
        if quantity.is_zero() {
            return Err(DomainTypeError::Invalid {
                type_name: "Price",
                reason: "division by zero",
            });
        }
        let value = decimal_value(self.mantissa, self.scale)?
            .checked_div(decimal_value(quantity.mantissa(), quantity.scale())?)
            .ok_or(DomainTypeError::Invalid {
                type_name: "Price",
                reason: "arithmetic overflow",
            })?;
        let (mantissa, scale) = decimal_parts(value)?;
        Self::new(mantissa, scale)
    }
}

impl PriceDelta {
    pub fn checked_mul(self, quantity: SignedQuantity) -> Result<Money, DomainTypeError> {
        let value = decimal_value(self.mantissa, self.scale)?
            .checked_mul(decimal_value(quantity.mantissa, quantity.scale)?)
            .ok_or(DomainTypeError::Invalid {
                type_name: "Money",
                reason: "arithmetic overflow",
            })?;
        let (mantissa, scale) = decimal_parts(value)?;
        Money::new(mantissa, scale)
    }
}

impl Money {
    pub fn is_zero(self) -> bool {
        self.mantissa == 0
    }
    pub fn is_negative(self) -> bool {
        self.mantissa < 0
    }
    pub fn checked_neg(self) -> Result<Self, DomainTypeError> {
        self.mantissa
            .checked_neg()
            .ok_or(DomainTypeError::Invalid {
                type_name: "Money",
                reason: "arithmetic overflow",
            })
            .and_then(|mantissa| Self::new(mantissa, self.scale))
    }
    pub fn checked_add(self, other: Self) -> Result<Self, DomainTypeError> {
        let value = decimal_value(self.mantissa, self.scale)?
            .checked_add(decimal_value(other.mantissa, other.scale)?)
            .ok_or(DomainTypeError::Invalid {
                type_name: "Money",
                reason: "arithmetic overflow",
            })?;
        let (mantissa, scale) = decimal_parts(value)?;
        Self::new(mantissa, scale)
    }

    pub fn checked_sub(self, other: Self) -> Result<Self, DomainTypeError> {
        self.checked_add(other.checked_neg()?)
    }

    pub fn checked_div(self, quantity: SignedQuantity) -> Result<Price, DomainTypeError> {
        if quantity.is_zero() {
            return Err(DomainTypeError::Invalid {
                type_name: "Price",
                reason: "division by zero",
            });
        }
        let value = decimal_value(self.mantissa, self.scale)?
            .checked_div(decimal_value(quantity.mantissa, quantity.scale)?)
            .ok_or(DomainTypeError::Invalid {
                type_name: "Price",
                reason: "arithmetic overflow",
            })?;
        let (mantissa, scale) = decimal_parts(value)?;
        Price::new(mantissa, scale)
    }
}

macro_rules! unit_u64_type {
    ($name:ident) => {
        #[derive(
            Clone,
            Copy,
            Debug,
            Default,
            Eq,
            Ord,
            PartialEq,
            PartialOrd,
            Hash,
            Serialize,
            Deserialize,
        )]
        #[serde(transparent)]
        pub struct $name(u64);

        impl $name {
            pub const fn new(value: u64) -> Self {
                Self(value)
            }
            pub const fn get(self) -> u64 {
                self.0
            }
            pub const fn checked_add(self, other: Self) -> Option<Self> {
                match self.0.checked_add(other.0) {
                    Some(value) => Some(Self(value)),
                    None => None,
                }
            }
            pub const fn saturating_sub(self, other: Self) -> Self {
                Self(self.0.saturating_sub(other.0))
            }
            pub const fn is_multiple_of(self, other: u64) -> bool {
                self.0.is_multiple_of(other)
            }
        }

        impl From<u64> for $name {
            fn from(value: u64) -> Self {
                Self::new(value)
            }
        }

        impl From<$name> for u64 {
            fn from(value: $name) -> Self {
                value.get()
            }
        }

        impl std::ops::Add<u64> for $name {
            type Output = Self;

            fn add(self, rhs: u64) -> Self::Output {
                Self(self.0 + rhs)
            }
        }

        impl std::ops::AddAssign<u64> for $name {
            fn add_assign(&mut self, rhs: u64) {
                self.0 += rhs;
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                self.0.fmt(f)
            }
        }
    };
}

unit_u64_type!(Sequence);
unit_u64_type!(UnixNanos);
unit_u64_type!(Generation);
unit_u64_type!(DurationNanos);
unit_u64_type!(BasisPoints);

/// Shared business-time context for cross-module commands and events.
///
/// `event_time` is the provider/replay time which determines business
/// behavior.  Processing and wall-clock timestamps belong to observability or
/// transport layers and must not replace this value in deterministic flows.
#[derive(
    Clone, Copy, Debug, Default, Eq, Ord, PartialEq, PartialOrd, Hash, Serialize, Deserialize,
)]
pub struct EventContext {
    pub event_time: UnixNanos,
    pub sequence: Sequence,
}

impl EventContext {
    pub const fn new(event_time: UnixNanos, sequence: Sequence) -> Self {
        Self {
            event_time,
            sequence,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OrderSide {
    Buy,
    Sell,
}

impl std::str::FromStr for OrderSide {
    type Err = DomainTypeError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value.trim().to_ascii_lowercase().as_str() {
            "buy" | "bid" => Ok(Self::Buy),
            "sell" | "ask" => Ok(Self::Sell),
            _ => Err(DomainTypeError::Invalid {
                type_name: "OrderSide",
                reason: "expected buy or sell",
            }),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OrderStatus {
    Pending,
    Acknowledged,
    Accepted,
    PartiallyFilled,
    Filled,
    Canceled,
    Rejected,
    Expired,
    Unknown,
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

impl std::str::FromStr for OrderStatus {
    type Err = DomainTypeError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value.trim().to_ascii_lowercase().as_str() {
            "pending" => Ok(Self::Pending),
            "acknowledged" | "new" | "open" => Ok(Self::Acknowledged),
            "accepted" => Ok(Self::Accepted),
            "partially_filled" | "partial" => Ok(Self::PartiallyFilled),
            "filled" => Ok(Self::Filled),
            "canceled" | "cancelled" => Ok(Self::Canceled),
            "rejected" => Ok(Self::Rejected),
            "expired" => Ok(Self::Expired),
            "unknown" => Ok(Self::Unknown),
            _ => Err(DomainTypeError::Invalid {
                type_name: "OrderStatus",
                reason: "unrecognized order status",
            }),
        }
    }
}

#[cfg(test)]
mod more_tests {
    use super::*;

    #[test]
    fn text_types_reject_ambiguous_whitespace() {
        assert!(Symbol::new(" BTC").is_err());
        assert!(Exchange::new("").is_err());
        assert_eq!(Currency::new("USD").unwrap().as_str(), "USD");
    }

    #[test]
    fn numeric_types_are_semantically_distinct() {
        let quantity = Quantity::new(10, 2).unwrap();
        let price = Price::new(10, 2).unwrap();
        let price_delta = PriceDelta::new(0, 2).unwrap();
        assert_eq!(quantity.mantissa(), price.mantissa());
        assert_eq!(price_delta.to_string(), "0");
        assert!(Quantity::positive(0, 0).is_err());
    }

    #[test]
    fn decimals_are_canonical_and_compare_by_numeric_value() {
        assert_eq!(Quantity::new(100, 2).unwrap(), Quantity::new(1, 0).unwrap());
        assert!(Quantity::new(15, 1).unwrap() < Quantity::new(2, 0).unwrap());
        assert_eq!(Money::new(-1200, 3).unwrap().to_string(), "-1.2");
        assert_eq!(SignedQuantity::new(0, 18).unwrap().scale(), 0);
    }

    #[test]
    fn decimal_json_is_a_validated_canonical_string() {
        let price = serde_json::from_str::<Price>("\"42110.500\"").unwrap();
        assert_eq!(price, Price::new(421_105, 1).unwrap());
        assert_eq!(serde_json::to_string(&price).unwrap(), "\"42110.5\"");
        assert!(serde_json::from_str::<Price>(r#"{"mantissa":421105,"scale":1}"#).is_err());
        assert!(serde_json::from_str::<Price>("\"0\"").is_err());
        assert!(serde_json::from_str::<Quantity>("\"0.0000000000000000001\"").is_err());
    }

    #[test]
    fn increments_are_checked_with_exact_decimal_arithmetic() {
        assert!(Quantity::new(125, 3)
            .unwrap()
            .is_multiple_of(Quantity::new(5, 3).unwrap())
            .unwrap());
        assert!(!Price::new(10_001, 2)
            .unwrap()
            .is_multiple_of(Price::new(5, 2).unwrap())
            .unwrap());
    }

    #[test]
    fn reference_status_reads_legacy_provider_break_as_inactive() {
        assert_eq!(ReferenceStatus::from("break"), ReferenceStatus::Inactive);
        assert_eq!(
            serde_json::from_str::<ReferenceStatus>("\"break\"").unwrap(),
            ReferenceStatus::Inactive
        );
        assert_eq!(
            serde_json::to_string(&ReferenceStatus::Inactive).unwrap(),
            "\"inactive\""
        );
        assert_eq!(
            serde_json::from_str::<ReferenceStatus>("\"halted\"").unwrap(),
            ReferenceStatus::Unknown
        );
    }

    #[test]
    fn canonical_reference_taxonomy_rejects_provider_vocabulary() {
        assert_eq!(
            "spot".parse::<InstrumentKind>().unwrap(),
            InstrumentKind::Spot
        );
        assert_eq!(
            "perpetual".parse::<InstrumentKind>().unwrap(),
            InstrumentKind::Perpetual
        );
        assert!("margin".parse::<InstrumentKind>().is_err());
        assert!("usd-m-futures".parse::<InstrumentKind>().is_err());
        assert!("swap".parse::<InstrumentKind>().is_err());
        assert_eq!("crypto".parse::<AssetClass>().unwrap(), AssetClass::Crypto);
        assert!("coin-m".parse::<AssetClass>().is_err());
    }

    #[test]
    fn canonical_reference_taxonomy_has_stable_wire_spellings() {
        for (kind, spelling) in [
            (InstrumentKind::Equity, "equity"),
            (InstrumentKind::Spot, "spot"),
            (InstrumentKind::Perpetual, "perpetual"),
            (InstrumentKind::Future, "future"),
            (InstrumentKind::Option, "option"),
            (InstrumentKind::Index, "index"),
        ] {
            assert_eq!(
                serde_json::to_string(&kind).unwrap(),
                format!("\"{spelling}\"")
            );
            assert_eq!(
                serde_json::from_str::<InstrumentKind>(&format!("\"{spelling}\"")).unwrap(),
                kind
            );
        }
        assert!(serde_json::from_str::<InstrumentKind>("\"future-value\"").is_err());
    }
}
