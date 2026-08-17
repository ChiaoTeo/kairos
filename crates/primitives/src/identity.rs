use std::fmt;

use serde::{Deserialize, Serialize};

use crate::DomainTypeError;

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
text_type!(ExecutionRouteId);
// Provider-owned symbol. It is valid only at an integration boundary.
text_type!(ProviderSymbol);
// Stable provider identity shared by Reference access records and composition.
text_type!(ProviderId);
// Account-owned broker/custodian identity. Deliberately distinct from an
// Integration provider connection identity.
text_type!(BrokerId);
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
default_text_type!(ExecutionRouteId, "route:unresolved");
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
