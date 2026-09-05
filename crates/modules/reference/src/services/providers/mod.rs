//! Concrete provider connections, Reference mapping, and source fan-in.

use std::collections::BTreeMap;
use std::fmt::Display;
use std::str::FromStr;
use std::time::{Duration, Instant};

#[cfg(test)]
use futures_util::future::join_all;
use kairos_conflux::{
    BinanceCredential, BinanceRestConfig, ExternalInstrument, ExternalInstrumentCatalog,
    ExternalInstrumentKind, InstrumentCatalogQuery, MassiveInstrumentQuery, MassiveRestConfig,
    ParticipantKind,
};
use kairos_primitives::reference::{AssetClass, InstrumentKind, ReferenceSourceId};

struct ConnectionRef(kairos_conflux::ConnectionKey);

impl ConnectionRef {
    fn managed(key: kairos_conflux::ConnectionKey) -> Self {
        Self(key)
    }
}

mod activation;
mod binance;
mod binding;
mod credentials;
mod fan_in;
mod hyperliquid;
mod massive;
mod okx;
mod plan;

pub(crate) use activation::{
    activate_runtime_source_definition, deactivate_runtime_source_definition,
};
pub use binance::{
    BinanceDerivativesSource, BinanceEquitySource, BinanceOptionsSource, BinanceSpotSource,
};
#[cfg(test)]
use binance::{binance_equity_provider_catalog, binance_provider_catalog};
#[cfg(test)]
pub(crate) use binding::reference_source_definition;
pub(crate) use binding::{BinanceReferenceSource, MassiveReferenceSource, ReferenceSourceBinding};
pub(crate) use credentials::ReferenceCredentialResolver;
use fan_in::MASSIVE_PAGE_TIMEOUT;
pub use fan_in::ProviderFanInSource;
#[cfg(test)]
use fan_in::provider_catalog_uses_current_canonical_shape;
pub use hyperliquid::HyperliquidSource;
#[cfg(test)]
use hyperliquid::hyperliquid_provider_catalog;
use kairos_primitives::reference::ExchangeId;
pub(crate) use massive::massive_options_underlying_from_scope;
#[cfg(test)]
use massive::massive_provider_catalog;
pub use massive::{MassiveEquitySource, MassiveOptionsCoverageSource};
pub use okx::OkxSource;
#[cfg(test)]
use okx::okx_provider_catalog;
pub(crate) use plan::{ReferenceProviderPlan, ReferenceSourcePlan};

use crate::domain::{
    Asset, Exchange, Instrument, Listing, Market, ProviderCatalog, ReferenceError, ReferenceResult,
    ReferenceSourceDefinition, SourceDesiredState, SourceHealth, SourceScope, SourceScopeKind,
    SourceTickBudget,
};
use crate::services::sources::{ReferenceSource, SourceUpdate};
use crate::services::storage::provider_sync::PROVIDER_SCAN_FORMAT_VERSION;
use crate::services::storage::provider_sync_store::SqlxProviderSyncStore;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum BinanceProduct {
    Spot,
    UsdMFutures,
    CoinMFutures,
    Option,
}

impl BinanceProduct {
    const fn source_id(self) -> &'static str {
        match self {
            Self::Spot => "binance-spot",
            Self::UsdMFutures => "binance-usdm-futures",
            Self::CoinMFutures => "binance-coinm-futures",
            Self::Option => "binance-options",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum OkxProduct {
    Spot,
    Margin,
    Swap,
    Futures,
    Option,
}

impl OkxProduct {
    pub(crate) const fn source_id(self) -> &'static str {
        match self {
            Self::Spot => "okx-spot",
            Self::Margin => "okx-margin",
            Self::Swap => "okx-swap",
            Self::Futures => "okx-futures",
            Self::Option => "okx-options",
        }
    }

    pub(crate) const fn profile_product(self) -> &'static str {
        match self {
            Self::Spot => "spot",
            Self::Margin => "margin",
            Self::Swap => "swap",
            Self::Futures => "futures",
            Self::Option => "options",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum HyperliquidProduct {
    Perpetual,
    Spot,
}

impl HyperliquidProduct {
    pub(crate) const fn source_id(self) -> &'static str {
        match self {
            Self::Perpetual => "hyperliquid-perpetual",
            Self::Spot => "hyperliquid-spot",
        }
    }
}

pub(crate) fn default_endpoint(provider: &str) -> &'static str {
    match provider {
        "hyperliquid" => "https://api.hyperliquid.xyz/info",
        "binance-spot" | "binance-spot-rest" => "https://api.binance.com",
        "binance-equity" | "binance-equity-rest" => "https://api.binance.com",
        "binance-options" | "binance-options-rest" => "https://eapi.binance.com",
        "binance-usdm-futures" | "binance-usdm-futures-rest" => "https://fapi.binance.com",
        "binance-coinm-futures" | "binance-coinm-futures-rest" => "https://dapi.binance.com",
        "okx-spot" | "okx-margin" | "okx-equity" | "okx-swap" | "okx-futures" | "okx-options"
        | "okx-spot-rest" | "okx-margin-rest" | "okx-swap-rest" | "okx-futures-rest"
        | "okx-options-rest" => "https://www.okx.com",
        "massive"
        | "massive-equity"
        | "massive-equity-websocket"
        | "massive-options"
        | "massive-options-websocket" => "http://api.massiveprivateserver.site",
        _ => "",
    }
}

pub(super) fn binance_config(
    endpoint: &str,
    credential: Option<BinanceCredential>,
) -> BinanceRestConfig {
    BinanceRestConfig {
        environment: "public".into(),
        endpoint: endpoint.into(),
        credential,
    }
}

pub(super) fn provider_error(error: impl ToString) -> ReferenceError {
    ReferenceError::Provider(error.to_string())
}

fn normalize_option_underlying(value: &str) -> ReferenceResult<String> {
    let value = value.trim().to_ascii_uppercase();
    if value.is_empty()
        || value.len() > 32
        || !value.bytes().all(|byte| {
            byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'.' || byte == b'-'
        })
    {
        return Err(ReferenceError::Invalid(format!(
            "invalid option underlying: {value:?}"
        )));
    }
    Ok(value)
}

fn canonical_expiry(value: Option<kairos_primitives::time::UnixNanos>) -> ReferenceResult<String> {
    let value = value
        .ok_or_else(|| ReferenceError::Provider("expiring instrument expiry is missing".into()))?;
    let seconds = i64::try_from(value.get() / 1_000_000_000)
        .map_err(|_| ReferenceError::Provider("instrument expiry is out of range".into()))?;
    let date = chrono::DateTime::from_timestamp(seconds, 0)
        .ok_or_else(|| ReferenceError::Provider("instrument expiry is invalid".into()))?;
    Ok(date.format("%Y%m%d").to_string())
}

/// The single Reference-owned conversion from normalized Integration facts to
/// canonical economic lifecycle. Provider access/trading modes deliberately do
/// not become instrument kinds.
fn canonical_instrument_kind(kind: ExternalInstrumentKind) -> ReferenceResult<InstrumentKind> {
    match kind {
        ExternalInstrumentKind::Equity => Ok(InstrumentKind::Equity),
        ExternalInstrumentKind::Spot | ExternalInstrumentKind::Margin => Ok(InstrumentKind::Spot),
        ExternalInstrumentKind::Perpetual | ExternalInstrumentKind::EquityPerpetual => {
            Ok(InstrumentKind::Perpetual)
        },
        ExternalInstrumentKind::Future => Ok(InstrumentKind::Future),
        ExternalInstrumentKind::Option => Ok(InstrumentKind::Option),
    }
}

fn optional_decimal<T>(value: Option<String>, label: &str) -> ReferenceResult<Option<T>>
where
    T: FromStr,
    T::Err: Display,
{
    value
        .map(|value| {
            value.parse::<T>().map_err(|error| {
                ReferenceError::Provider(format!("invalid {label} {value:?}: {error}"))
            })
        })
        .transpose()
}

fn merge_provider_catalog(
    previous: Option<ProviderCatalog>,
    incoming: ProviderCatalog,
) -> ProviderCatalog {
    let previous = previous.unwrap_or_default();
    ProviderCatalog {
        venues: merge_records(previous.venues, incoming.venues, |value| {
            value.venue_id.clone()
        }),
        exchanges: merge_records(previous.exchanges, incoming.exchanges, |value| {
            value.exchange_id.clone()
        }),
        assets: merge_records(previous.assets, incoming.assets, |value| {
            value.asset_id.clone()
        }),
        instruments: merge_records(previous.instruments, incoming.instruments, |value| {
            value.instrument_id.clone()
        }),
        listings: merge_records(previous.listings, incoming.listings, |value| {
            value.listing_id.clone()
        }),
        markets: merge_records(previous.markets, incoming.markets, |value| {
            value.market_id.clone()
        }),
        venue_listings: merge_records(previous.venue_listings, incoming.venue_listings, |value| {
            value.listing_id.clone()
        }),
        venue_markets: merge_records(previous.venue_markets, incoming.venue_markets, |value| {
            value.market_id.clone()
        }),
        provider_catalog_memberships: merge_records(
            previous.provider_catalog_memberships,
            incoming.provider_catalog_memberships,
            |value| (value.source_id.clone(), value.instrument_id.clone()),
        ),
        venue_identifier_mappings: merge_records(
            previous.venue_identifier_mappings,
            incoming.venue_identifier_mappings,
            |value| {
                (
                    value.provider.clone(),
                    value.provider_product.clone(),
                    value.identifier_kind,
                    value.identifier.clone(),
                )
            },
        ),
    }
}

fn merge_records<T, K>(previous: Vec<T>, incoming: Vec<T>, key: impl Fn(&T) -> K) -> Vec<T>
where
    K: Ord,
{
    let mut merged = BTreeMap::new();
    for value in previous {
        merged.insert(key(&value), value);
    }
    for value in incoming {
        merged.insert(key(&value), value);
    }
    merged.into_values().collect()
}

#[cfg(test)]
mod tests;
