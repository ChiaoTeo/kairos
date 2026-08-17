//! Concrete provider connections, Reference mapping, and source fan-in.

use std::collections::{BTreeMap, BTreeSet};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use futures_util::future::join_all;
use kairos_integration::application::capabilities::reference::{
    AsyncInstrumentCatalogConnection, ExternalInstrument, ExternalInstrumentCatalog,
    ExternalInstrumentKind,
};
use kairos_integration::participants::binance::{
    BinanceCoinMConnection, BinanceEquityInstrumentCatalog, BinanceFuturesConnectionConfig,
    BinanceOptionsConnection, BinanceOptionsConnectionConfig, BinanceQuotaAllocation,
    BinanceSpotConnection, BinanceSpotConnectionConfig, BinanceUsdMConnection,
    InstrumentType as BinanceInstrumentType,
};
use kairos_integration::participants::hyperliquid::{
    HyperliquidConnection, HyperliquidConnectionConfig, HyperliquidInstrumentProduct,
};
use kairos_integration::participants::massive::{
    InstrumentQuery as MassiveInstrumentQuery, MassiveConnection, MassiveConnectionConfig,
};
use kairos_integration::participants::okx::{
    InstrumentType as OkxInstrumentType, OkxConnection, OkxConnectionConfig,
};
use kairos_primitives::{AssetClass, InstrumentKind, ProviderId, ProviderProductCode};

mod binance;
mod fan_in;
mod hyperliquid;
mod massive;
mod okx;

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

use crate::domain::{
    Asset, Entity, ExecutionAccess, Instrument, Listing, Market, ProviderCatalog, ProviderHealth,
    ReferenceError, ReferenceResult,
};
use crate::services::source::{ProviderUpdate, ReferenceSource};
use crate::services::sqlx_storage::SqlxProviderSyncStore;

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

fn populate_market_data_accesses(
    catalog: &mut ProviderCatalog,
    provider: &str,
) -> ReferenceResult<()> {
    let provider_id = ProviderId::new(provider)?;
    for market in &catalog.markets {
        catalog
            .market_data_accesses
            .push(crate::domain::MarketDataAccess {
                source_id: None,
                // A provider symbol is not a market identity. Massive may
                // expose the same ticker on more than one venue, so bind the
                // provider access to the canonical Market it observes.
                access_id: format!("market-data-access:{provider}:{}", market.market_id),
                market_id: market.market_id.clone(),
                provider_id: provider_id.clone(),
                provider_product: market.market_type.clone(),
                provider_symbol: kairos_primitives::ProviderSymbol::new(
                    market.source_symbol.as_str(),
                )?,
                status: market.status,
                effective_from_unix_nanos: market.effective_from_unix_nanos,
                effective_to_unix_nanos: market.effective_to_unix_nanos,
            });
    }
    Ok(())
}

fn populate_execution_accesses(
    catalog: &mut ProviderCatalog,
    provider: &str,
) -> ReferenceResult<()> {
    let provider_id = ProviderId::new(provider)?;
    for market in &catalog.markets {
        catalog.execution_accesses.push(ExecutionAccess {
            source_id: None,
            access_id: kairos_primitives::ExecutionAccessId::new(format!(
                "execution-access:{provider}:{}:{}",
                market.market_type,
                market.source_symbol.as_str().to_ascii_lowercase()
            ))?,
            instrument_id: Some(market.instrument_id.clone()),
            listing_id: market.listing_id.clone(),
            market_id: Some(market.market_id.clone()),
            provider_id: provider_id.clone(),
            provider_product: market.market_type.clone(),
            provider_symbol: kairos_primitives::ProviderSymbol::new(market.source_symbol.as_str())?,
            settlement_asset_id: None,
            status: market.status,
            effective_from_unix_nanos: market.effective_from_unix_nanos,
            effective_to_unix_nanos: market.effective_to_unix_nanos,
        });
    }
    Ok(())
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
        execution_accesses: merge_records(
            previous.execution_accesses,
            incoming.execution_accesses,
            |value| value.access_id.clone(),
        ),
        market_data_accesses: merge_records(
            previous.market_data_accesses,
            incoming.market_data_accesses,
            |value| value.access_id.clone(),
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
