use std::path::PathBuf;

use kairos_reference_contract::{
    Asset as RustAsset, AssetCatalogQuery, ContractError, Exchange as RustExchange,
    ExchangeCatalogQuery, Instrument as RustInstrument, InstrumentSearchQuery,
    LifecycleCatalogQuery, Listing as RustListing, ListingCatalogQuery, Market as RustMarket,
    MarketSearchQuery, ReferenceCatalog as RustCatalog, ReferenceLifecycleEvent as RustEvent,
    ReferencePage, ReferenceReadSession as RustSession,
};
use pyo3::create_exception;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

create_exception!(
    _native_reference_contract,
    ReferenceInvalidCatalogError,
    PyValueError
);
create_exception!(
    _native_reference_contract,
    ReferenceCatalogUnavailableError,
    PyRuntimeError
);

const API_VERSION: u32 = 1;

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
struct NativeBuildInfo {
    #[pyo3(get)]
    api_version: u32,
    #[pyo3(get)]
    owner: String,
    #[pyo3(get)]
    package_version: String,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
#[derive(Clone)]
struct ReferenceExchange {
    #[pyo3(get)]
    exchange_id: String,
    #[pyo3(get)]
    name: String,
    #[pyo3(get)]
    status: String,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
#[derive(Clone)]
struct ReferenceAsset {
    #[pyo3(get)]
    asset_id: String,
    #[pyo3(get)]
    code: String,
    #[pyo3(get)]
    name: Option<String>,
    #[pyo3(get)]
    asset_class: String,
    #[pyo3(get)]
    status: String,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
#[derive(Clone)]
struct ReferenceInstrument {
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    symbol: String,
    #[pyo3(get)]
    name: Option<String>,
    #[pyo3(get)]
    instrument_type: String,
    #[pyo3(get)]
    product_family: Option<String>,
    #[pyo3(get)]
    issuer_id: Option<String>,
    #[pyo3(get)]
    share_class: Option<String>,
    #[pyo3(get)]
    primary_currency_asset_id: Option<String>,
    #[pyo3(get)]
    underlying_instrument_id: Option<String>,
    #[pyo3(get)]
    expiry_unix_nanos: Option<u64>,
    #[pyo3(get)]
    strike: Option<String>,
    #[pyo3(get)]
    option_right: Option<String>,
    #[pyo3(get)]
    status: String,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
#[derive(Clone)]
struct ReferenceListing {
    #[pyo3(get)]
    listing_id: String,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    exchange_id: String,
    #[pyo3(get)]
    exchange_symbol: String,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    effective_from_unix_nanos: u64,
    #[pyo3(get)]
    effective_to_unix_nanos: Option<u64>,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
#[derive(Clone)]
struct ReferenceMarket {
    #[pyo3(get)]
    market_id: String,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    listing_id: Option<String>,
    #[pyo3(get)]
    exchange_id: String,
    #[pyo3(get)]
    instrument_kind: String,
    #[pyo3(get)]
    asset_type: Option<String>,
    #[pyo3(get)]
    underlying_instrument_id: Option<String>,
    #[pyo3(get)]
    venue_symbol: Option<String>,
    #[pyo3(get)]
    base_asset_id: Option<String>,
    #[pyo3(get)]
    quote_asset_id: Option<String>,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    price_tick: Option<String>,
    #[pyo3(get)]
    quantity_tick: Option<String>,
    #[pyo3(get)]
    price_precision: i32,
    #[pyo3(get)]
    quantity_precision: i32,
    #[pyo3(get)]
    minimum_quantity: Option<String>,
    #[pyo3(get)]
    minimum_notional: Option<String>,
    #[pyo3(get)]
    contract_size: Option<String>,
    #[pyo3(get)]
    effective_from_unix_nanos: u64,
    #[pyo3(get)]
    effective_to_unix_nanos: Option<u64>,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
#[derive(Clone)]
struct ReferenceLifecycleEvent {
    #[pyo3(get)]
    sequence: u64,
    #[pyo3(get)]
    event_id: String,
    #[pyo3(get)]
    event_type: String,
    #[pyo3(get)]
    event_time_unix_nanos: u64,
    #[pyo3(get)]
    record_kind: Option<String>,
    #[pyo3(get)]
    record_id: Option<String>,
    #[pyo3(get)]
    market_id: Option<String>,
    #[pyo3(get)]
    instrument_id: Option<String>,
    #[pyo3(get)]
    listing_id: Option<String>,
    #[pyo3(get)]
    exchange_id: Option<String>,
    #[pyo3(get)]
    venue_symbol: Option<String>,
    #[pyo3(get)]
    previous_status: Option<String>,
    #[pyo3(get)]
    current_status: Option<String>,
    #[pyo3(get)]
    previous_symbol: Option<String>,
    #[pyo3(get)]
    current_symbol: Option<String>,
    #[pyo3(get)]
    operation: Option<String>,
    #[pyo3(get)]
    provenance: Option<String>,
    #[pyo3(get)]
    conflict_policy: Option<String>,
    #[pyo3(get)]
    generation: u64,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
struct ReferenceCatalogStatus {
    #[pyo3(get)]
    generation: u64,
    #[pyo3(get)]
    event_sequence: u64,
    #[pyo3(get)]
    committed_at_unix_nanos: u64,
    #[pyo3(get)]
    exchange_count: u64,
    #[pyo3(get)]
    asset_count: u64,
    #[pyo3(get)]
    instrument_count: u64,
    #[pyo3(get)]
    listing_count: u64,
    #[pyo3(get)]
    market_count: u64,
    #[pyo3(get)]
    active_market_count: u64,
    #[pyo3(get)]
    lifecycle_event_count: u64,
    #[pyo3(get)]
    missing_equity_markets: u64,
    #[pyo3(get)]
    legacy_exchange_market_ids: u64,
    #[pyo3(get)]
    legacy_exchange_listing_ids: u64,
    #[pyo3(get)]
    option_listings: u64,
    #[pyo3(get)]
    option_markets: u64,
}

#[pyclass(module = "kairospy._native_reference_contract")]
struct ReferenceCatalog {
    inner: RustCatalog,
}

#[pymethods]
impl ReferenceCatalog {
    #[new]
    fn new(database_path: PathBuf) -> PyResult<Self> {
        Ok(Self {
            inner: RustCatalog::open(database_path).map_err(contract_error)?,
        })
    }

    fn snapshot(&self) -> PyResult<ReferenceReadSession> {
        let inner = self.inner.read_session().map_err(contract_error)?;
        let watermark = inner.watermark();
        Ok(ReferenceReadSession {
            inner: Some(inner),
            generation: watermark.generation.get(),
            event_sequence: watermark.event_sequence.get(),
        })
    }
}

#[pyclass(module = "kairospy._native_reference_contract", unsendable)]
struct ReferenceReadSession {
    inner: Option<RustSession>,
    #[pyo3(get)]
    generation: u64,
    #[pyo3(get)]
    event_sequence: u64,
}

#[pymethods]
impl ReferenceReadSession {
    fn close(&mut self) {
        self.inner.take();
    }

    fn status(&self) -> PyResult<ReferenceCatalogStatus> {
        let value = self.session()?.status().map_err(contract_error)?;
        Ok(ReferenceCatalogStatus {
            generation: value.watermark.generation.get(),
            event_sequence: value.watermark.event_sequence.get(),
            committed_at_unix_nanos: value.watermark.committed_at_unix_nanos.get(),
            exchange_count: value.counts.exchanges,
            asset_count: value.counts.assets,
            instrument_count: value.counts.instruments,
            listing_count: value.counts.listings,
            market_count: value.counts.markets,
            active_market_count: value.counts.active_markets,
            lifecycle_event_count: value.counts.lifecycle_events,
            missing_equity_markets: value.integrity.missing_equity_markets,
            legacy_exchange_market_ids: value.integrity.legacy_exchange_market_ids,
            legacy_exchange_listing_ids: value.integrity.legacy_exchange_listing_ids,
            option_listings: value.integrity.option_listings,
            option_markets: value.integrity.option_markets,
        })
    }

    #[pyo3(signature = (exchange_ids=Vec::new(), query=None, status=None, active_only=false, limit=None, offset=0))]
    fn exchanges(&self, exchange_ids: Vec<String>, query: Option<String>, status: Option<String>, active_only: bool, limit: Option<u64>, offset: u64) -> PyResult<Vec<ReferenceExchange>> {
        self.session()?.exchanges(&ExchangeCatalogQuery { exchange_ids, search: query, status, active_only, page: page(limit, offset) })
            .map(|values| values.into_iter().map(Into::into).collect()).map_err(contract_error)
    }

    #[pyo3(signature = (asset_ids=Vec::new(), query=None, code=None, asset_class=None, status=None, active_only=false, limit=None, offset=0))]
    fn assets(&self, asset_ids: Vec<String>, query: Option<String>, code: Option<String>, asset_class: Option<String>, status: Option<String>, active_only: bool, limit: Option<u64>, offset: u64) -> PyResult<Vec<ReferenceAsset>> {
        self.session()?.assets(&AssetCatalogQuery { asset_ids, search: query, code, asset_class, status, active_only, page: page(limit, offset) })
            .map(|values| values.into_iter().map(Into::into).collect()).map_err(contract_error)
    }

    #[pyo3(signature = (instrument_ids=Vec::new(), query=None, symbol=None, instrument_type=None, product_family=None, underlying_instrument_id=None, expiry_unix_nanos=None, expiry_from_unix_nanos=None, expiry_to_unix_nanos=None, option_right=None, status=None, active_only=false, limit=None, offset=0))]
    #[allow(clippy::too_many_arguments)]
    fn instruments(&self, instrument_ids: Vec<String>, query: Option<String>, symbol: Option<String>, instrument_type: Option<String>, product_family: Option<String>, underlying_instrument_id: Option<String>, expiry_unix_nanos: Option<u64>, expiry_from_unix_nanos: Option<u64>, expiry_to_unix_nanos: Option<u64>, option_right: Option<String>, status: Option<String>, active_only: bool, limit: Option<u64>, offset: u64) -> PyResult<Vec<ReferenceInstrument>> {
        let option_right = option_right.map(|value| value.trim().to_lowercase());
        self.session()?.instruments(&InstrumentSearchQuery { instrument_ids, search: query, symbol, instrument_type, product_family, underlying_instrument_id, expiry_unix_nanos, expiry_from_unix_nanos, expiry_to_unix_nanos, option_right, status, active_only, page: page(limit, offset) })
            .map(|values| values.into_iter().map(Into::into).collect()).map_err(contract_error)
    }

    #[pyo3(signature = (listing_ids=Vec::new(), query=None, instrument_id=None, exchange_id=None, exchange_symbol=None, status=None, active_only=false, limit=None, offset=0))]
    fn listings(&self, listing_ids: Vec<String>, query: Option<String>, instrument_id: Option<String>, exchange_id: Option<String>, exchange_symbol: Option<String>, status: Option<String>, active_only: bool, limit: Option<u64>, offset: u64) -> PyResult<Vec<ReferenceListing>> {
        let exchange_id = exchange_id.map(normalize_exchange_id);
        self.session()?.listings(&ListingCatalogQuery { listing_ids, search: query, instrument_id, exchange_id, exchange_symbol, status, active_only, page: page(limit, offset) })
            .map(|values| values.into_iter().map(Into::into).collect()).map_err(contract_error)
    }

    #[pyo3(signature = (market_ids=Vec::new(), query=None, symbol=None, asset_code=None, exchange_id=None, instrument_kind=None, asset_type=None, instrument_id=None, listing_id=None, underlying_instrument_id=None, active_only=false, status=None, limit=None, offset=0))]
    #[allow(clippy::too_many_arguments)]
    fn markets(&self, market_ids: Vec<String>, query: Option<String>, symbol: Option<String>, asset_code: Option<String>, exchange_id: Option<String>, instrument_kind: Option<String>, asset_type: Option<String>, instrument_id: Option<String>, listing_id: Option<String>, underlying_instrument_id: Option<String>, active_only: bool, status: Option<String>, limit: Option<u64>, offset: u64) -> PyResult<Vec<ReferenceMarket>> {
        let exchange_id = exchange_id.map(normalize_exchange_id);
        self.session()?.markets(&MarketSearchQuery { market_ids, search: query, venue_symbol: symbol, asset_code, exchange_id, instrument_kind, asset_type, instrument_id, listing_id, underlying_instrument_id, status, active_only, page: page(limit, offset) })
            .map(|values| values.into_iter().map(Into::into).collect()).map_err(contract_error)
    }

    #[pyo3(signature = (sequence_from=None, sequence_to=None, limit=256))]
    fn events(&self, sequence_from: Option<u64>, sequence_to: Option<u64>, limit: u64) -> PyResult<Vec<ReferenceLifecycleEvent>> {
        self.session()?.lifecycle_events(&LifecycleCatalogQuery { sequence_from, sequence_to, limit })
            .map(|values| values.into_iter().map(Into::into).collect()).map_err(contract_error)
    }

    fn option_coverage(&self) -> PyResult<Vec<String>> {
        self.session()?.option_coverage().map(|value| value.underlyings).map_err(contract_error)
    }

    fn outbox_depth(&self) -> PyResult<u64> {
        self.session()?.outbox_depth().map_err(contract_error)
    }
}

impl ReferenceReadSession {
    fn session(&self) -> PyResult<&RustSession> {
        self.inner.as_ref().ok_or_else(|| ReferenceCatalogUnavailableError::new_err("Reference read session is closed"))
    }
}

fn page(limit: Option<u64>, offset: u64) -> ReferencePage { ReferencePage { limit, offset } }

fn normalize_exchange_id(value: String) -> String {
    if value.starts_with("exchange:") { value } else { format!("exchange:{value}") }
}

impl From<RustExchange> for ReferenceExchange {
    fn from(value: RustExchange) -> Self { Self { exchange_id: value.exchange_id.to_string(), name: value.name, status: value.status.to_string() } }
}

impl From<RustAsset> for ReferenceAsset {
    fn from(value: RustAsset) -> Self { Self { asset_id: value.asset_id.to_string(), code: value.code.to_string(), name: value.name, asset_class: value.asset_class.to_string(), status: value.status.to_string() } }
}

impl From<RustInstrument> for ReferenceInstrument {
    fn from(value: RustInstrument) -> Self {
        Self { instrument_id: value.instrument_id.to_string(), symbol: value.symbol.to_string(), name: value.name, instrument_type: value.instrument_type.to_string(), product_family: value.product_family, issuer_id: value.issuer_id.map(|v| v.to_string()), share_class: value.share_class, primary_currency_asset_id: value.primary_currency_asset_id.map(|v| v.to_string()), underlying_instrument_id: value.underlying_instrument_id.map(|v| v.to_string()), expiry_unix_nanos: value.expiry_unix_nanos.map(|v| v.get()), strike: value.strike.map(|v| v.to_string()), option_right: value.option_right, status: value.status.to_string() }
    }
}

impl From<RustListing> for ReferenceListing {
    fn from(value: RustListing) -> Self { Self { listing_id: value.listing_id.to_string(), instrument_id: value.instrument_id.to_string(), exchange_id: value.exchange_id.to_string(), exchange_symbol: value.exchange_symbol.to_string(), status: value.status.to_string(), effective_from_unix_nanos: value.effective_from_unix_nanos.get(), effective_to_unix_nanos: value.effective_to_unix_nanos.map(|v| v.get()) } }
}

impl From<RustMarket> for ReferenceMarket {
    fn from(value: RustMarket) -> Self {
        Self { market_id: value.market_id.to_string(), instrument_id: value.instrument_id.to_string(), listing_id: value.listing_id.map(|v| v.to_string()), exchange_id: value.exchange_id.to_string(), instrument_kind: value.instrument_kind.to_string(), asset_type: value.asset_type.map(|v| v.to_string()), underlying_instrument_id: value.underlying_instrument_id.map(|v| v.to_string()), venue_symbol: value.venue_symbol.map(|v| v.to_string()), base_asset_id: value.base_asset_id.map(|v| v.to_string()), quote_asset_id: value.quote_asset_id.map(|v| v.to_string()), status: value.status.to_string(), price_tick: value.price_tick.map(|v| v.to_string()), quantity_tick: value.quantity_tick.map(|v| v.to_string()), price_precision: value.price_precision, quantity_precision: value.quantity_precision, minimum_quantity: value.minimum_quantity.map(|v| v.to_string()), minimum_notional: value.minimum_notional.map(|v| v.to_string()), contract_size: value.contract_size.map(|v| v.to_string()), effective_from_unix_nanos: value.effective_from_unix_nanos.get(), effective_to_unix_nanos: value.effective_to_unix_nanos.map(|v| v.get()) }
    }
}

impl From<RustEvent> for ReferenceLifecycleEvent {
    fn from(value: RustEvent) -> Self {
        Self { sequence: value.sequence, event_id: value.event_id, event_type: value.event_type, event_time_unix_nanos: value.event_time_unix_nanos.get(), record_kind: value.record_kind, record_id: value.record_id, market_id: value.market_id.map(|v| v.to_string()), instrument_id: value.instrument_id.map(|v| v.to_string()), listing_id: value.listing_id.map(|v| v.to_string()), exchange_id: value.exchange_id.map(|v| v.to_string()), venue_symbol: value.venue_symbol.map(|v| v.to_string()), previous_status: value.previous_status.map(|v| v.to_string()), current_status: value.current_status.map(|v| v.to_string()), previous_symbol: value.previous_symbol, current_symbol: value.current_symbol, operation: value.operation, provenance: value.provenance, conflict_policy: value.conflict_policy, generation: value.generation.get() }
    }
}

fn contract_error(error: ContractError) -> PyErr {
    match error {
        ContractError::Invalid(message) => ReferenceInvalidCatalogError::new_err(message),
        ContractError::Transport(message) => ReferenceCatalogUnavailableError::new_err(message),
    }
}

#[pyfunction]
fn build_info() -> NativeBuildInfo {
    NativeBuildInfo { api_version: API_VERSION, owner: "Reference".into(), package_version: env!("CARGO_PKG_VERSION").into() }
}

#[pymodule]
fn _native_reference_contract(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("ReferenceInvalidCatalogError", module.py().get_type::<ReferenceInvalidCatalogError>())?;
    module.add("ReferenceCatalogUnavailableError", module.py().get_type::<ReferenceCatalogUnavailableError>())?;
    module.add_class::<NativeBuildInfo>()?;
    module.add_class::<ReferenceExchange>()?;
    module.add_class::<ReferenceAsset>()?;
    module.add_class::<ReferenceInstrument>()?;
    module.add_class::<ReferenceListing>()?;
    module.add_class::<ReferenceMarket>()?;
    module.add_class::<ReferenceLifecycleEvent>()?;
    module.add_class::<ReferenceCatalogStatus>()?;
    module.add_class::<ReferenceCatalog>()?;
    module.add_class::<ReferenceReadSession>()?;
    module.add_function(wrap_pyfunction!(build_info, module)?)?;
    Ok(())
}
