//! Concrete provider connections, Reference mapping, and source fan-in.

use std::collections::{BTreeMap, BTreeSet};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

#[cfg(test)]
use futures_util::future::join_all;
use kairos_conflux::{
    ExternalInstrument, ExternalInstrumentCatalog, ExternalInstrumentKind, InstrumentCatalogQuery,
    MassiveInstrumentQuery, MassiveRestConfig, ParticipantKind,
};
use kairos_primitives::{AssetClass, InstrumentKind};

struct ConnectionRef(kairos_conflux::ConnectionKey);

impl ConnectionRef {
    fn managed(key: kairos_conflux::ConnectionKey) -> Self {
        Self(key)
    }
}

mod binance;
mod fan_in;
mod hyperliquid;
mod massive;
mod okx;
mod plan;

#[cfg(test)]
use fan_in::provider_catalog_uses_current_canonical_shape;
pub use fan_in::CompositeSource;
pub(crate) use fan_in::ParticipantAugmentedSource;
use fan_in::{merge_provider_catalog_views, MASSIVE_PAGES_PER_REFRESH, MASSIVE_PAGE_TIMEOUT};

#[cfg(test)]
use binance::{binance_equity_provider_catalog, binance_provider_catalog};
pub use binance::{
    BinanceDerivativesSource, BinanceEquitySource, BinanceOptionsSource, BinanceSpotSource,
};
#[cfg(test)]
use hyperliquid::hyperliquid_provider_catalog;
pub use hyperliquid::HyperliquidSource;
#[cfg(test)]
use massive::massive_provider_catalog;
pub use massive::{MassiveEquitySource, MassiveOptionsCoverageSource};
#[cfg(test)]
use okx::okx_provider_catalog;
pub use okx::OkxSource;
pub(crate) use plan::{ReferenceProviderPlan, ReferenceSourcePlan};

use crate::domain::{
    Asset, Entity, Instrument, Listing, Market, ProviderCatalog, ProviderHealth, ReferenceError,
    ReferenceResult,
};
use crate::services::source::{ProviderUpdate, ReferenceSource};
use crate::services::sqlx_storage::SqlxProviderSyncStore;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum BinanceProduct {
    Spot,
    UsdMFutures,
    CoinMFutures,
    Option,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum OkxProduct {
    Spot,
    Margin,
    Swap,
    Futures,
    Option,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum HyperliquidProduct {
    Perpetual,
    Spot,
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

fn canonical_expiry(value: Option<kairos_primitives::UnixNanos>) -> ReferenceResult<String> {
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
        }
        ExternalInstrumentKind::Future => Ok(InstrumentKind::Future),
        ExternalInstrumentKind::Option => Ok(InstrumentKind::Option),
    }
}

fn merge_provider_catalog(
    previous: Option<ProviderCatalog>,
    incoming: ProviderCatalog,
) -> ProviderCatalog {
    let previous = previous.unwrap_or_default();
    ProviderCatalog {
        entities: merge_records(previous.entities, incoming.entities, |value| {
            value.entity_id.clone()
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
