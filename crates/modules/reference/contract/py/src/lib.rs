use std::path::PathBuf;
use std::sync::Arc;

use _native_transport::direct::DirectAeronSubscription;
use _native_transport::lease::{EventLease, EventLeaseError};
use kairos_primitives::reference::ReferenceStatus;
use kairos_primitives::time::UnixNanos;
use kairos_protocol::generated::kairos::common::v_2::Decimal64;
use kairos_protocol::generated::kairos::reference::{v_2 as fb, v_3 as fb3};
use kairos_protocol::{EventMetadataOwned, decode_event_metadata};
use kairos_reference_contract::{
    Asset as RustAsset, AssetCatalogQuery, ContractError, Exchange as RustExchange,
    ExchangeCatalogQuery, Instrument as RustInstrument, InstrumentAvailabilityQuery,
    InstrumentSearchQuery, Listing as RustListing, ListingCatalogQuery, Market as RustMarket,
    MarketResolutionQuery, MarketSearchQuery, ProviderCatalogMembershipQuery,
    ReferenceCatalog as RustCatalog, ReferenceCoverageEvidence as RustCoverageEvidence,
    ReferenceInstrumentAvailability as RustInstrumentAvailability,
    ReferenceLifecycleEvent as RustEvent, ReferencePage,
    ReferenceQueryEvidence as RustQueryEvidence, ReferenceReadSession as RustSession,
    Venue as RustVenue, VenueKind, VenueListing as RustVenueListing, VenueListingSearchQuery,
    VenueMarket as RustVenueMarket, VenueMarketSearchQuery, VenueRole, VenueSearchQuery,
};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::{PyClass, create_exception};

create_exception!(
    _native_reference_contract,
    ReferenceInvalidCatalogError,
    PyValueError
);
create_exception!(
    _native_reference_contract,
    ReferenceInvalidEventError,
    PyValueError
);
create_exception!(
    _native_reference_contract,
    ReferenceCatalogUnavailableError,
    PyRuntimeError
);

const API_VERSION: u32 = 1;

#[pyclass(
    name = "_NativeSemanticDecimal",
    frozen,
    module = "kairospy._native_reference_contract"
)]
#[derive(Clone)]
struct NativeDecimal {
    #[pyo3(get)]
    mantissa: i64,
    #[pyo3(get)]
    scale: u8,
    semantic_type: &'static str,
}

#[pymethods]
impl NativeDecimal {
    fn __str__(&self) -> String {
        decimal_text(self.mantissa, self.scale)
    }

    #[getter]
    fn semantic_type(&self) -> &'static str {
        self.semantic_type
    }

    #[getter]
    fn value(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let decimal = PyModule::import(py, "decimal")?.getattr("Decimal")?;
        Ok(decimal
            .call1((decimal_text(self.mantissa, self.scale),))?
            .unbind())
    }
}

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
struct ReferenceEventMetadata {
    #[pyo3(get)]
    event_id: String,
    #[pyo3(get)]
    stream_id: String,
    #[pyo3(get)]
    sequence: u64,
    #[pyo3(get)]
    producer: String,
    #[pyo3(get)]
    producer_incarnation: u64,
    #[pyo3(get)]
    workspace_id: String,
    #[pyo3(get)]
    launch_id: Option<String>,
    #[pyo3(get)]
    instance_id: Option<String>,
    #[pyo3(get)]
    correlation_id: Option<String>,
    #[pyo3(get)]
    causation_id: Option<String>,
    #[pyo3(get)]
    occurred_at_unix_nanos: u64,
    #[pyo3(get)]
    published_at_unix_nanos: u64,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
struct ReferenceEvent {
    #[pyo3(get)]
    metadata: ReferenceEventMetadata,
    #[pyo3(get)]
    kind: String,
    #[pyo3(get)]
    catalog_revision: u64,
    data: Py<PyAny>,
}

#[pyclass(
    name = "ReferenceLiveEventView",
    frozen,
    module = "kairospy._native_reference_contract"
)]
struct ReferenceLiveEventView {
    lease: Arc<EventLease>,
}

#[pymethods]
impl ReferenceLiveEventView {
    #[getter]
    fn kind(&self) -> PyResult<String> {
        self.with_event(|event| event.kind().as_str().to_owned())
    }

    #[getter]
    fn metadata(&self) -> PyResult<ReferenceEventMetadata> {
        self.with_event(|event| {
            decode_event_metadata(event.metadata())
                .map(event_metadata)
                .map_err(|error| ReferenceInvalidEventError::new_err(error.to_string()))
        })?
    }

    #[getter]
    fn catalog_revision(&self, py: Python<'_>) -> PyResult<u64> {
        self.with_event(|event| {
            project_reference_event(py, event).map(|value| value.catalog_revision)
        })?
    }

    #[getter]
    fn data(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.with_event(|event| project_reference_event(py, event).map(|value| value.data))?
    }
}

impl ReferenceLiveEventView {
    fn with_event<R>(
        &self,
        read: impl for<'frame> FnOnce(kairos_reference_contract::ReferenceEvent<'frame>) -> R,
    ) -> PyResult<R> {
        self.lease
            .with_frame(|frame| {
                kairos_reference_contract::decode_event(frame)
                    .map(read)
                    .map_err(|error| ReferenceInvalidEventError::new_err(error.to_string()))
            })
            .map_err(reference_lease_error)?
    }
}

#[pyclass(
    name = "ReferenceLiveSubscription",
    module = "kairospy._native_reference_contract",
    unsendable
)]
struct ReferenceLiveSubscription {
    inner: DirectAeronSubscription,
}

#[pymethods]
impl ReferenceLiveSubscription {
    #[new]
    #[pyo3(signature = (*, aeron_dir=None, channel=None, stream_id=None, max_payload_len=_native_transport::DEFAULT_MAX_PAYLOAD_LEN))]
    fn new(
        aeron_dir: Option<String>,
        channel: Option<String>,
        stream_id: Option<i32>,
        max_payload_len: usize,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: DirectAeronSubscription::connect(
                aeron_dir.as_deref(),
                channel
                    .as_deref()
                    .unwrap_or(kairos_reference_contract::DEFAULT_AERON_CHANNEL),
                stream_id.unwrap_or(kairos_reference_contract::REFERENCE_EVENTS_STREAM_ID),
                max_payload_len,
            )?,
        })
    }

    #[pyo3(signature = (visitor, *, fragment_limit=64))]
    fn poll_visit(
        &self,
        py: Python<'_>,
        visitor: Py<PyAny>,
        fragment_limit: i32,
    ) -> PyResult<usize> {
        self.inner.poll_visit(py, fragment_limit, |py, lease| {
            lease
                .with_frame(|frame| kairos_reference_contract::decode_event(frame).map(|_| ()))
                .map_err(reference_lease_error)?
                .map_err(|error| ReferenceInvalidEventError::new_err(error.to_string()))?;
            visitor.call1(py, (Py::new(py, ReferenceLiveEventView { lease })?,))?;
            Ok(())
        })
    }

    fn close(&self) -> PyResult<()> {
        self.inner.close()
    }
}

fn reference_lease_error(error: EventLeaseError) -> PyErr {
    PyRuntimeError::new_err(error.to_string())
}

#[pymethods]
impl ReferenceEvent {
    #[getter]
    fn data(&self, py: Python<'_>) -> Py<PyAny> {
        self.data.clone_ref(py)
    }
    #[getter]
    fn event_id(&self) -> &str {
        &self.metadata.event_id
    }
    #[getter]
    fn stream_id(&self) -> &str {
        &self.metadata.stream_id
    }
    #[getter]
    fn sequence(&self) -> u64 {
        self.metadata.sequence
    }
    #[getter]
    fn producer(&self) -> &str {
        &self.metadata.producer
    }
    #[getter]
    fn producer_incarnation(&self) -> u64 {
        self.metadata.producer_incarnation
    }
    #[getter]
    fn launch_id(&self) -> Option<&str> {
        self.metadata.launch_id.as_deref()
    }
    #[getter]
    fn instance_id(&self) -> Option<&str> {
        self.metadata.instance_id.as_deref()
    }
    #[getter]
    fn occurred_at_unix_nanos(&self) -> u64 {
        self.metadata.occurred_at_unix_nanos
    }
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

#[pymethods]
impl ReferenceExchange {
    #[getter]
    fn id(&self) -> &str {
        &self.exchange_id
    }
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

#[pymethods]
impl ReferenceAsset {
    #[getter]
    fn id(&self) -> &str {
        &self.asset_id
    }
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
#[derive(Clone)]
struct ReferenceInstrumentRef {
    #[pyo3(get)]
    id: String,
    #[pyo3(get)]
    display_symbol: String,
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
    settlement_asset_id: Option<String>,
    #[pyo3(get)]
    underlying_instrument_id: Option<String>,
    #[pyo3(get)]
    expiry_unix_nanos: Option<u64>,
    #[pyo3(get)]
    strike: Option<NativeDecimal>,
    #[pyo3(get)]
    option_right: Option<String>,
    #[pyo3(get)]
    status: String,
}

#[pymethods]
impl ReferenceInstrument {
    #[getter]
    fn id(&self) -> &str {
        &self.instrument_id
    }

    #[getter]
    fn r#ref(&self) -> ReferenceInstrumentRef {
        ReferenceInstrumentRef {
            id: self.instrument_id.clone(),
            display_symbol: self.symbol.clone(),
        }
    }
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
#[derive(Clone)]
struct ReferenceInstrumentAvailability {
    #[pyo3(get)]
    source_id: String,
    #[pyo3(get)]
    instrument: ReferenceInstrument,
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

#[pymethods]
impl ReferenceListing {
    #[getter]
    fn id(&self) -> &str {
        &self.listing_id
    }
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
#[derive(Clone)]
struct ReferenceTradingRules {
    #[pyo3(get)]
    price_increment: Option<NativeDecimal>,
    #[pyo3(get)]
    quantity_increment: Option<NativeDecimal>,
    #[pyo3(get)]
    minimum_quantity: Option<NativeDecimal>,
    #[pyo3(get)]
    minimum_notional: Option<NativeDecimal>,
    #[pyo3(get)]
    contract_multiplier: Option<NativeDecimal>,
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
    price_tick: Option<NativeDecimal>,
    #[pyo3(get)]
    quantity_tick: Option<NativeDecimal>,
    #[pyo3(get)]
    price_precision: i32,
    #[pyo3(get)]
    quantity_precision: i32,
    #[pyo3(get)]
    minimum_quantity: Option<NativeDecimal>,
    #[pyo3(get)]
    minimum_notional: Option<NativeDecimal>,
    #[pyo3(get)]
    contract_size: Option<NativeDecimal>,
    #[pyo3(get)]
    effective_from_unix_nanos: u64,
    #[pyo3(get)]
    effective_to_unix_nanos: Option<u64>,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
#[derive(Clone)]
struct ReferenceVenue {
    #[pyo3(get)]
    venue_id: String,
    #[pyo3(get)]
    name: String,
    #[pyo3(get)]
    venue_kind: String,
    #[pyo3(get)]
    roles: Vec<String>,
    #[pyo3(get)]
    mic: Option<String>,
    #[pyo3(get)]
    operating_mic: Option<String>,
    #[pyo3(get)]
    parent_venue_id: Option<String>,
    #[pyo3(get)]
    jurisdiction: Option<String>,
    #[pyo3(get)]
    status: String,
}

#[pymethods]
impl ReferenceVenue {
    #[getter]
    fn id(&self) -> &str {
        &self.venue_id
    }
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
#[derive(Clone)]
struct ReferenceVenueListing {
    #[pyo3(get)]
    listing_id: String,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    listing_venue_id: String,
    #[pyo3(get)]
    market_segment_id: Option<String>,
    #[pyo3(get)]
    listing_symbol: String,
    #[pyo3(get)]
    listing_role: String,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    effective_from_unix_nanos: u64,
    #[pyo3(get)]
    effective_to_unix_nanos: Option<u64>,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
#[derive(Clone)]
struct ReferenceVenueMarket {
    #[pyo3(get)]
    market_id: String,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    execution_venue_id: String,
    #[pyo3(get)]
    origin_listing_id: Option<String>,
    #[pyo3(get)]
    market_segment_id: Option<String>,
    #[pyo3(get)]
    venue_symbol: Option<String>,
    #[pyo3(get)]
    trading_calendar_id: Option<String>,
    #[pyo3(get)]
    trading_session_ids: Vec<String>,
    #[pyo3(get)]
    base_asset_id: Option<String>,
    #[pyo3(get)]
    quote_asset_id: Option<String>,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    trading_rules: ReferenceTradingRules,
    #[pyo3(get)]
    effective_from_unix_nanos: u64,
    #[pyo3(get)]
    effective_to_unix_nanos: Option<u64>,
}

#[pymethods]
impl ReferenceVenueMarket {
    #[getter]
    fn id(&self) -> &str {
        &self.market_id
    }
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
#[derive(Clone)]
struct ReferenceProviderCatalogMembership {
    #[pyo3(get)]
    source_id: String,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    provider_symbol: Option<String>,
    #[pyo3(get)]
    provider_product: Option<String>,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    effective_from_unix_nanos: u64,
    #[pyo3(get)]
    effective_to_unix_nanos: Option<u64>,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
#[derive(Clone)]
struct ReferenceCoverage {
    #[pyo3(get)]
    coverage_id: String,
    #[pyo3(get)]
    source_id: String,
    #[pyo3(get)]
    fact_kinds: Vec<String>,
    #[pyo3(get)]
    scope_kind: String,
    #[pyo3(get)]
    scope_binding: Option<String>,
    #[pyo3(get)]
    scope_ids: Vec<String>,
    #[pyo3(get)]
    scope_instrument_kind: Option<String>,
    #[pyo3(get)]
    completeness: String,
    #[pyo3(get)]
    state: String,
    #[pyo3(get)]
    generation: u64,
    #[pyo3(get)]
    event_sequence: u64,
    #[pyo3(get)]
    last_attempt_unix_nanos: Option<u64>,
    #[pyo3(get)]
    last_success_unix_nanos: Option<u64>,
    #[pyo3(get)]
    stale_after_unix_nanos: Option<u64>,
    #[pyo3(get)]
    has_last_known_good: bool,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
struct ReferenceCoverageStateChange {
    #[pyo3(get)]
    coverage: ReferenceCoverage,
    #[pyo3(get)]
    previous_state: String,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
#[derive(Clone)]
struct ReferenceMarketResolutionRecord {
    #[pyo3(get)]
    instrument: ReferenceInstrument,
    #[pyo3(get)]
    market: ReferenceVenueMarket,
    #[pyo3(get)]
    venue: ReferenceVenue,
    #[pyo3(get)]
    origin_listing: Option<ReferenceVenueListing>,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
struct ReferenceMarketResolutionResponse {
    #[pyo3(get)]
    resolution: Option<ReferenceMarketResolutionRecord>,
    #[pyo3(get)]
    candidate_count: u64,
    #[pyo3(get)]
    evidence: ReferenceQueryEvidence,
}

#[pymethods]
impl ReferenceMarket {
    #[getter]
    fn id(&self) -> &str {
        &self.market_id
    }

    #[getter]
    fn instrument(&self) -> ReferenceInstrumentRef {
        ReferenceInstrumentRef {
            id: self.instrument_id.clone(),
            display_symbol: self.venue_symbol.clone().unwrap_or_default(),
        }
    }

    #[getter]
    fn base_asset(&self) -> Option<&str> {
        self.base_asset_id.as_deref()
    }

    #[getter]
    fn quote_asset(&self) -> Option<&str> {
        self.quote_asset_id.as_deref()
    }

    #[getter]
    fn trading_rules(&self) -> ReferenceTradingRules {
        ReferenceTradingRules {
            price_increment: self.price_tick.clone(),
            quantity_increment: self.quantity_tick.clone(),
            minimum_quantity: self.minimum_quantity.clone(),
            minimum_notional: self.minimum_notional.clone(),
            contract_multiplier: self.contract_size.clone(),
        }
    }
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

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
#[derive(Clone)]
struct ReferenceCoverageEvidence {
    #[pyo3(get)]
    coverage_id: String,
    #[pyo3(get)]
    source_id: String,
    #[pyo3(get)]
    scope: String,
    #[pyo3(get)]
    completeness: String,
    #[pyo3(get)]
    state: String,
    #[pyo3(get)]
    last_success_unix_nanos: Option<u64>,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
#[derive(Clone)]
struct ReferenceQueryEvidence {
    #[pyo3(get)]
    generation: u64,
    #[pyo3(get)]
    event_sequence: u64,
    #[pyo3(get)]
    committed_at_unix_nanos: u64,
    #[pyo3(get)]
    conclusion: String,
    #[pyo3(get)]
    coverages: Vec<ReferenceCoverageEvidence>,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
struct ReferenceInstrumentSearchResponse {
    #[pyo3(get)]
    instruments: Vec<ReferenceInstrument>,
    #[pyo3(get)]
    evidence: ReferenceQueryEvidence,
    #[pyo3(get)]
    next_cursor: Option<String>,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
struct ReferenceVenueSearchResponse {
    #[pyo3(get)]
    venues: Vec<ReferenceVenue>,
    #[pyo3(get)]
    evidence: ReferenceQueryEvidence,
    #[pyo3(get)]
    next_cursor: Option<String>,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
struct ReferenceVenueListingSearchResponse {
    #[pyo3(get)]
    listings: Vec<ReferenceVenueListing>,
    #[pyo3(get)]
    evidence: ReferenceQueryEvidence,
    #[pyo3(get)]
    next_cursor: Option<String>,
}

#[pyclass(frozen, module = "kairospy._native_reference_contract")]
struct ReferenceVenueMarketSearchResponse {
    #[pyo3(get)]
    markets: Vec<ReferenceVenueMarket>,
    #[pyo3(get)]
    instruments: Vec<ReferenceInstrument>,
    #[pyo3(get)]
    evidence: ReferenceQueryEvidence,
    #[pyo3(get)]
    next_cursor: Option<String>,
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

    fn read_session(&self) -> PyResult<ReferenceReadSession> {
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

    #[pyo3(signature = (exchange_ids=None, query=None, status=None, active_only=false, limit=None, offset=0))]
    fn exchanges(
        &self,
        exchange_ids: Option<Vec<String>>,
        query: Option<String>,
        status: Option<String>,
        active_only: bool,
        limit: Option<i64>,
        offset: i64,
    ) -> PyResult<Vec<ReferenceExchange>> {
        self.session()?
            .exchanges(&ExchangeCatalogQuery {
                exchange_ids: validated_values(exchange_ids, "exchange_id")?,
                search: query,
                status: reference_status(status)?,
                active_only,
                page: page(limit, offset)?,
            })
            .map(|values| values.into_iter().map(Into::into).collect())
            .map_err(contract_error)
    }

    #[pyo3(signature = (asset_ids=None, query=None, code=None, asset_class=None, status=None, active_only=false, limit=None, offset=0))]
    fn assets(
        &self,
        asset_ids: Option<Vec<String>>,
        query: Option<String>,
        code: Option<String>,
        asset_class: Option<String>,
        status: Option<String>,
        active_only: bool,
        limit: Option<i64>,
        offset: i64,
    ) -> PyResult<Vec<ReferenceAsset>> {
        self.session()?
            .assets(&AssetCatalogQuery {
                asset_ids: validated_values(asset_ids, "asset_id")?,
                search: query,
                code: validated_value(code, "asset code")?,
                asset_class: parsed_value(asset_class, "asset_class")?,
                status: reference_status(status)?,
                active_only,
                page: page(limit, offset)?,
            })
            .map(|values| values.into_iter().map(Into::into).collect())
            .map_err(contract_error)
    }

    #[pyo3(signature = (instrument_ids=None, query=None, symbol=None, instrument_type=None, product_family=None, underlying_instrument_id=None, expiry_unix_nanos=None, expiry_from_unix_nanos=None, expiry_to_unix_nanos=None, option_right=None, status=None, active_only=false, limit=None, offset=0))]
    #[allow(clippy::too_many_arguments)]
    fn instruments(
        &self,
        instrument_ids: Option<Vec<String>>,
        query: Option<String>,
        symbol: Option<String>,
        instrument_type: Option<String>,
        product_family: Option<String>,
        underlying_instrument_id: Option<String>,
        expiry_unix_nanos: Option<i64>,
        expiry_from_unix_nanos: Option<i64>,
        expiry_to_unix_nanos: Option<i64>,
        option_right: Option<String>,
        status: Option<String>,
        active_only: bool,
        limit: Option<i64>,
        offset: i64,
    ) -> PyResult<Vec<ReferenceInstrument>> {
        let option_right = option_right.map(|value| value.trim().to_lowercase());
        self.session()?
            .instruments(&InstrumentSearchQuery {
                instrument_ids: validated_values(instrument_ids, "instrument_id")?,
                search: query,
                symbol: validated_value(symbol, "symbol")?,
                instrument_type: parsed_value(instrument_type, "instrument_type")?,
                product_family,
                underlying_instrument_id: validated_value(
                    underlying_instrument_id,
                    "underlying_instrument_id",
                )?,
                expiry_unix_nanos: optional_non_negative(expiry_unix_nanos, "expiry_unix_nanos")?
                    .map(UnixNanos::new),
                expiry_from_unix_nanos: optional_non_negative(
                    expiry_from_unix_nanos,
                    "expiry_from_unix_nanos",
                )?
                .map(UnixNanos::new),
                expiry_to_unix_nanos: optional_non_negative(
                    expiry_to_unix_nanos,
                    "expiry_to_unix_nanos",
                )?
                .map(UnixNanos::new),
                option_right,
                status: reference_status(status)?,
                active_only,
                coverage_scope: None,
                page: page(limit, offset)?,
            })
            .map(|values| values.into_iter().map(Into::into).collect())
            .map_err(contract_error)
    }

    #[pyo3(signature = (query=None, instrument_type=None, active_only=true, limit=25, offset=0))]
    fn search_instruments(
        &self,
        query: Option<String>,
        instrument_type: Option<String>,
        active_only: bool,
        limit: i64,
        offset: i64,
    ) -> PyResult<ReferenceInstrumentSearchResponse> {
        self.session()?
            .search_instruments(&InstrumentSearchQuery {
                search: query,
                instrument_type: parsed_value(instrument_type, "instrument_type")?,
                active_only,
                coverage_scope: None,
                page: page(Some(limit), offset)?,
                ..Default::default()
            })
            .map(|response| ReferenceInstrumentSearchResponse {
                instruments: response.instruments.into_iter().map(Into::into).collect(),
                evidence: response.evidence.into(),
                next_cursor: response.next_cursor,
            })
            .map_err(contract_error)
    }

    #[pyo3(signature = (query=None, venue_kind=None, role=None, active_only=true, limit=25, offset=0))]
    fn search_venues(
        &self,
        query: Option<String>,
        venue_kind: Option<String>,
        role: Option<String>,
        active_only: bool,
        limit: i64,
        offset: i64,
    ) -> PyResult<ReferenceVenueSearchResponse> {
        self.session()?
            .search_venues(&VenueSearchQuery {
                search: query,
                venue_kind: parse_venue_kind(venue_kind)?,
                role: parse_venue_role(role)?,
                active_only,
                page: page(Some(limit), offset)?,
                ..Default::default()
            })
            .map(|response| ReferenceVenueSearchResponse {
                venues: response.venues.into_iter().map(Into::into).collect(),
                evidence: response.evidence.into(),
                next_cursor: response.next_cursor,
            })
            .map_err(contract_error)
    }

    #[pyo3(signature = (query=None, instrument_id=None, listing_venue_id=None, active_only=true, limit=25, offset=0))]
    fn search_venue_listings(
        &self,
        query: Option<String>,
        instrument_id: Option<String>,
        listing_venue_id: Option<String>,
        active_only: bool,
        limit: i64,
        offset: i64,
    ) -> PyResult<ReferenceVenueListingSearchResponse> {
        self.session()?
            .search_venue_listings(&VenueListingSearchQuery {
                search: query,
                instrument_id: validated_value(instrument_id, "instrument_id")?,
                listing_venue_id: validated_value(listing_venue_id, "listing_venue_id")?,
                active_only,
                page: page(Some(limit), offset)?,
                ..Default::default()
            })
            .map(|response| ReferenceVenueListingSearchResponse {
                listings: response.listings.into_iter().map(Into::into).collect(),
                evidence: response.evidence.into(),
                next_cursor: response.next_cursor,
            })
            .map_err(contract_error)
    }

    #[pyo3(signature = (query=None, instrument_id=None, execution_venue_id=None, origin_listing_id=None, instrument_kind=None, active_only=true, limit=25, offset=0))]
    #[allow(clippy::too_many_arguments)]
    fn search_venue_markets(
        &self,
        query: Option<String>,
        instrument_id: Option<String>,
        execution_venue_id: Option<String>,
        origin_listing_id: Option<String>,
        instrument_kind: Option<String>,
        active_only: bool,
        limit: i64,
        offset: i64,
    ) -> PyResult<ReferenceVenueMarketSearchResponse> {
        self.session()?
            .search_venue_markets(&VenueMarketSearchQuery {
                search: query,
                instrument_id: validated_value(instrument_id, "instrument_id")?,
                execution_venue_id: validated_value(execution_venue_id, "execution_venue_id")?,
                origin_listing_id: validated_value(origin_listing_id, "origin_listing_id")?,
                instrument_kind: parsed_value(instrument_kind, "instrument_kind")?,
                active_only,
                page: page(Some(limit), offset)?,
                ..Default::default()
            })
            .map(|response| ReferenceVenueMarketSearchResponse {
                markets: response.markets.into_iter().map(Into::into).collect(),
                instruments: response.instruments.into_values().map(Into::into).collect(),
                evidence: response.evidence.into(),
                next_cursor: response.next_cursor,
            })
            .map_err(contract_error)
    }

    #[pyo3(signature = (source_ids=None, instrument_ids=None, active_only=true, limit=256, offset=0))]
    fn provider_catalog_memberships(
        &self,
        source_ids: Option<Vec<String>>,
        instrument_ids: Option<Vec<String>>,
        active_only: bool,
        limit: i64,
        offset: i64,
    ) -> PyResult<Vec<ReferenceProviderCatalogMembership>> {
        self.session()?
            .provider_catalog_memberships(&ProviderCatalogMembershipQuery {
                source_ids: validated_values(source_ids, "source_id")?,
                instrument_ids: validated_values(instrument_ids, "instrument_id")?,
                active_only,
                page: page(Some(limit), offset)?,
                ..Default::default()
            })
            .map(|values| {
                values
                    .into_iter()
                    .map(|value| ReferenceProviderCatalogMembership {
                        source_id: value.source_id.to_string(),
                        instrument_id: value.instrument_id.to_string(),
                        provider_symbol: value.provider_symbol.map(|item| item.to_string()),
                        provider_product: value.provider_product,
                        status: value.status.to_string(),
                        effective_from_unix_nanos: value.effective_from_unix_nanos.get(),
                        effective_to_unix_nanos: value.effective_to_unix_nanos.map(UnixNanos::get),
                    })
                    .collect()
            })
            .map_err(contract_error)
    }

    #[pyo3(signature = (*, market_id=None, instrument_id=None, execution_venue_id=None, active_only=true))]
    fn resolve_market(
        &self,
        market_id: Option<String>,
        instrument_id: Option<String>,
        execution_venue_id: Option<String>,
        active_only: bool,
    ) -> PyResult<ReferenceMarketResolutionResponse> {
        self.session()?
            .resolve_market(&MarketResolutionQuery {
                market_id: validated_value(market_id, "market_id")?,
                instrument_id: validated_value(instrument_id, "instrument_id")?,
                execution_venue_id: validated_value(execution_venue_id, "execution_venue_id")?,
                active_only,
                coverage_scope: None,
            })
            .map(|response| ReferenceMarketResolutionResponse {
                resolution: response
                    .resolution
                    .map(|value| ReferenceMarketResolutionRecord {
                        instrument: value.instrument.into(),
                        market: value.market.into(),
                        venue: value.venue.into(),
                        origin_listing: value.origin_listing.map(Into::into),
                    }),
                candidate_count: response.candidate_count,
                evidence: response.evidence.into(),
            })
            .map_err(contract_error)
    }

    #[pyo3(signature = (source_ids=None, instrument_ids=None, query=None, symbol=None, instrument_type=None, active_only=false, limit=None, offset=0))]
    #[allow(clippy::too_many_arguments)]
    fn instrument_availability(
        &self,
        source_ids: Option<Vec<String>>,
        instrument_ids: Option<Vec<String>>,
        query: Option<String>,
        symbol: Option<String>,
        instrument_type: Option<String>,
        active_only: bool,
        limit: Option<i64>,
        offset: i64,
    ) -> PyResult<Vec<ReferenceInstrumentAvailability>> {
        self.session()?
            .instrument_availability(&InstrumentAvailabilityQuery {
                source_ids: validated_values(source_ids, "source_id")?,
                instrument_ids: validated_values(instrument_ids, "instrument_id")?,
                search: query,
                symbol: validated_value(symbol, "symbol")?,
                instrument_type: parsed_value(instrument_type, "instrument_type")?,
                active_only,
                page: page(limit, offset)?,
            })
            .map(|values| values.into_iter().map(Into::into).collect())
            .map_err(contract_error)
    }

    #[pyo3(signature = (listing_ids=None, query=None, instrument_id=None, exchange_id=None, exchange_symbol=None, status=None, active_only=false, limit=None, offset=0))]
    fn listings(
        &self,
        listing_ids: Option<Vec<String>>,
        query: Option<String>,
        instrument_id: Option<String>,
        exchange_id: Option<String>,
        exchange_symbol: Option<String>,
        status: Option<String>,
        active_only: bool,
        limit: Option<i64>,
        offset: i64,
    ) -> PyResult<Vec<ReferenceListing>> {
        let exchange_id = exchange_id.map(normalize_exchange_id);
        self.session()?
            .listings(&ListingCatalogQuery {
                listing_ids: validated_values(listing_ids, "listing_id")?,
                search: query,
                instrument_id: validated_value(instrument_id, "instrument_id")?,
                exchange_id: validated_value(exchange_id, "exchange_id")?,
                exchange_symbol: validated_value(exchange_symbol, "exchange_symbol")?,
                status: reference_status(status)?,
                active_only,
                page: page(limit, offset)?,
            })
            .map(|values| values.into_iter().map(Into::into).collect())
            .map_err(contract_error)
    }

    #[pyo3(signature = (market_ids=None, query=None, symbol=None, asset_code=None, exchange_id=None, instrument_kind=None, asset_type=None, instrument_id=None, listing_id=None, underlying_instrument_id=None, active_only=false, status=None, limit=None, offset=0))]
    #[allow(clippy::too_many_arguments)]
    fn markets(
        &self,
        market_ids: Option<Vec<String>>,
        query: Option<String>,
        symbol: Option<String>,
        asset_code: Option<String>,
        exchange_id: Option<String>,
        instrument_kind: Option<String>,
        asset_type: Option<String>,
        instrument_id: Option<String>,
        listing_id: Option<String>,
        underlying_instrument_id: Option<String>,
        active_only: bool,
        status: Option<String>,
        limit: Option<i64>,
        offset: i64,
    ) -> PyResult<Vec<ReferenceMarket>> {
        let exchange_id = exchange_id.map(normalize_exchange_id);
        self.session()?
            .markets(&MarketSearchQuery {
                market_ids: validated_values(market_ids, "market_id")?,
                search: query,
                venue_symbol: validated_value(symbol, "symbol")?,
                asset_code: validated_value(asset_code, "asset_code")?,
                exchange_id: validated_value(exchange_id, "exchange_id")?,
                instrument_kind: parsed_value(instrument_kind, "instrument_kind")?,
                asset_type: parsed_value(asset_type, "asset_type")?,
                instrument_id: validated_value(instrument_id, "instrument_id")?,
                listing_id: validated_value(listing_id, "listing_id")?,
                underlying_instrument_id: validated_value(
                    underlying_instrument_id,
                    "underlying_instrument_id",
                )?,
                status: reference_status(status)?,
                active_only,
                page: page(limit, offset)?,
            })
            .map(|values| values.into_iter().map(Into::into).collect())
            .map_err(contract_error)
    }

    fn option_coverage(&self) -> PyResult<Vec<String>> {
        self.session()?
            .option_coverage()
            .map(|value| value.underlyings)
            .map_err(contract_error)
    }

    fn outbox_depth(&self) -> PyResult<u64> {
        self.session()?.outbox_depth().map_err(contract_error)
    }
}

impl ReferenceReadSession {
    fn session(&self) -> PyResult<&RustSession> {
        self.inner.as_ref().ok_or_else(|| {
            ReferenceCatalogUnavailableError::new_err("Reference read session is closed")
        })
    }
}

impl From<RustCoverageEvidence> for ReferenceCoverageEvidence {
    fn from(value: RustCoverageEvidence) -> Self {
        Self {
            coverage_id: value.coverage_id.to_string(),
            source_id: value.source_id.to_string(),
            scope: coverage_scope_label(&value.scope),
            completeness: match value.completeness {
                kairos_reference_contract::CoverageCompleteness::Unknown => "unknown",
                kairos_reference_contract::CoverageCompleteness::Partial => "partial",
                kairos_reference_contract::CoverageCompleteness::CompleteForDeclaredScope => {
                    "complete_for_declared_scope"
                },
            }
            .to_owned(),
            state: match value.state {
                kairos_reference_contract::CoverageState::NotConfigured => "not_configured",
                kairos_reference_contract::CoverageState::Waiting => "waiting",
                kairos_reference_contract::CoverageState::Scanning => "scanning",
                kairos_reference_contract::CoverageState::Promoting => "promoting",
                kairos_reference_contract::CoverageState::Usable => "usable",
                kairos_reference_contract::CoverageState::Stale => "stale",
                kairos_reference_contract::CoverageState::RetryWaiting => "retry_waiting",
                kairos_reference_contract::CoverageState::Paused => "paused",
                kairos_reference_contract::CoverageState::Unavailable => "unavailable",
            }
            .to_owned(),
            last_success_unix_nanos: value.last_success_unix_nanos.map(UnixNanos::get),
        }
    }
}

impl From<RustQueryEvidence> for ReferenceQueryEvidence {
    fn from(value: RustQueryEvidence) -> Self {
        Self {
            generation: value.watermark.generation.get(),
            event_sequence: value.watermark.event_sequence.get(),
            committed_at_unix_nanos: value.watermark.committed_at_unix_nanos.get(),
            conclusion: match value.conclusion {
                kairos_reference_contract::ReferenceKnowledgeConclusion::Found => "found",
                kairos_reference_contract::ReferenceKnowledgeConclusion::NotFoundInCoveredScope => {
                    "not_found_in_covered_scope"
                },
                kairos_reference_contract::ReferenceKnowledgeConclusion::UnknownOutsideCoverage => {
                    "unknown_outside_coverage"
                },
                kairos_reference_contract::ReferenceKnowledgeConclusion::Preparing => "preparing",
                kairos_reference_contract::ReferenceKnowledgeConclusion::KnownButStale => {
                    "known_but_stale"
                },
                kairos_reference_contract::ReferenceKnowledgeConclusion::SourceUnavailable => {
                    "source_unavailable"
                },
            }
            .to_owned(),
            coverages: value.coverages.into_iter().map(Into::into).collect(),
        }
    }
}

fn coverage_scope_label(scope: &kairos_reference_contract::ReferenceCoverageScope) -> String {
    match scope {
        kairos_reference_contract::ReferenceCoverageScope::ProviderCatalog { binding } => {
            format!("provider_catalog:{}", binding.source_id())
        },
        kairos_reference_contract::ReferenceCoverageScope::VenueListings {
            venue_ids,
            instrument_kind,
        } => format!(
            "venue_listings:{}:{}",
            venue_ids
                .iter()
                .map(ToString::to_string)
                .collect::<Vec<_>>()
                .join(","),
            instrument_kind.as_str()
        ),
        kairos_reference_contract::ReferenceCoverageScope::VenueMarkets {
            venue_ids,
            instrument_kind,
        } => format!(
            "venue_markets:{}:{}",
            venue_ids
                .iter()
                .map(ToString::to_string)
                .collect::<Vec<_>>()
                .join(","),
            instrument_kind.as_str()
        ),
        kairos_reference_contract::ReferenceCoverageScope::UnderlyingOptions {
            underlying_instrument_ids,
        } => format!(
            "underlying_options:{}",
            underlying_instrument_ids
                .iter()
                .map(ToString::to_string)
                .collect::<Vec<_>>()
                .join(",")
        ),
    }
}

fn page(limit: Option<i64>, offset: i64) -> PyResult<ReferencePage> {
    Ok(ReferencePage {
        limit: optional_non_negative(limit, "limit")?,
        offset: non_negative(offset, "offset")?,
    })
}

fn optional_non_negative(value: Option<i64>, label: &str) -> PyResult<Option<u64>> {
    value.map(|value| non_negative(value, label)).transpose()
}

fn non_negative(value: i64, label: &str) -> PyResult<u64> {
    u64::try_from(value)
        .map_err(|_| ReferenceInvalidCatalogError::new_err(format!("{label} must be non-negative")))
}

fn validated_values<T>(values: Option<Vec<String>>, label: &str) -> PyResult<Option<Vec<T>>>
where
    T: TryFrom<String>,
    T::Error: std::fmt::Display,
{
    values
        .map(|values| {
            values
                .into_iter()
                .map(|value| validated(value, label))
                .collect()
        })
        .transpose()
}

fn validated_value<T>(value: Option<String>, label: &str) -> PyResult<Option<T>>
where
    T: TryFrom<String>,
    T::Error: std::fmt::Display,
{
    value.map(|value| validated(value, label)).transpose()
}

fn validated<T>(value: String, label: &str) -> PyResult<T>
where
    T: TryFrom<String>,
    T::Error: std::fmt::Display,
{
    T::try_from(value)
        .map_err(|error| ReferenceInvalidCatalogError::new_err(format!("invalid {label}: {error}")))
}

fn parsed_value<T>(value: Option<String>, label: &str) -> PyResult<Option<T>>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    value
        .map(|value| {
            value.parse().map_err(|error| {
                ReferenceInvalidCatalogError::new_err(format!("invalid {label}: {error}"))
            })
        })
        .transpose()
}

fn reference_status(value: Option<String>) -> PyResult<Option<ReferenceStatus>> {
    value
        .map(|value| match value.trim().to_ascii_lowercase().as_str() {
            "draft" => Ok(ReferenceStatus::Draft),
            "active" => Ok(ReferenceStatus::Active),
            "trading" => Ok(ReferenceStatus::Trading),
            "suspended" => Ok(ReferenceStatus::Suspended),
            "delisted" => Ok(ReferenceStatus::Delisted),
            "inactive" => Ok(ReferenceStatus::Inactive),
            "retired" => Ok(ReferenceStatus::Retired),
            "expired" => Ok(ReferenceStatus::Expired),
            "unknown" => Ok(ReferenceStatus::Unknown),
            _ => Err(ReferenceInvalidCatalogError::new_err(
                "invalid Reference status",
            )),
        })
        .transpose()
}

fn parse_venue_kind(value: Option<String>) -> PyResult<Option<VenueKind>> {
    value
        .map(|value| match value.trim().to_ascii_lowercase().as_str() {
            "regulated_exchange" => Ok(VenueKind::RegulatedExchange),
            "regulated_market" => Ok(VenueKind::RegulatedMarket),
            "trading_platform" => Ok(VenueKind::TradingPlatform),
            "ats" => Ok(VenueKind::Ats),
            "pts" => Ok(VenueKind::Pts),
            "otc_facility" => Ok(VenueKind::OtcFacility),
            "dealer" => Ok(VenueKind::Dealer),
            "trade_reporting_facility" => Ok(VenueKind::TradeReportingFacility),
            "unknown" => Ok(VenueKind::Unknown),
            _ => Err(ReferenceInvalidCatalogError::new_err(
                "invalid venue_kind: expected regulated_exchange, regulated_market, trading_platform, ats, pts, otc_facility, dealer, trade_reporting_facility, or unknown",
            )),
        })
        .transpose()
}

fn parse_venue_role(value: Option<String>) -> PyResult<Option<VenueRole>> {
    value
        .map(|value| match value.trim().to_ascii_lowercase().as_str() {
            "listing" => Ok(VenueRole::Listing),
            "execution" => Ok(VenueRole::Execution),
            "reporting" => Ok(VenueRole::Reporting),
            _ => Err(ReferenceInvalidCatalogError::new_err(
                "invalid venue_role: expected listing, execution, or reporting",
            )),
        })
        .transpose()
}

fn normalize_exchange_id(value: String) -> String {
    if value.starts_with("exchange:") {
        value
    } else {
        format!("exchange:{value}")
    }
}

impl From<RustExchange> for ReferenceExchange {
    fn from(value: RustExchange) -> Self {
        Self {
            exchange_id: value.exchange_id.to_string(),
            name: value.name,
            status: value.status.to_string(),
        }
    }
}

impl From<RustAsset> for ReferenceAsset {
    fn from(value: RustAsset) -> Self {
        Self {
            asset_id: value.asset_id.to_string(),
            code: value.code.to_string(),
            name: value.name,
            asset_class: value.asset_class.to_string(),
            status: value.status.to_string(),
        }
    }
}

impl From<RustInstrument> for ReferenceInstrument {
    fn from(value: RustInstrument) -> Self {
        Self {
            instrument_id: value.instrument_id.to_string(),
            symbol: value.symbol.to_string(),
            name: value.name,
            instrument_type: value.instrument_type.to_string(),
            product_family: value.product_family,
            issuer_id: value.issuer_id.map(|v| v.to_string()),
            share_class: value.share_class,
            primary_currency_asset_id: value.primary_currency_asset_id.map(|v| v.to_string()),
            settlement_asset_id: value.settlement_asset_id.map(|v| v.to_string()),
            underlying_instrument_id: value.underlying_instrument_id.map(|v| v.to_string()),
            expiry_unix_nanos: value.expiry_unix_nanos.map(|v| v.get()),
            strike: value
                .strike
                .map(|value| native_decimal(value.mantissa(), value.scale(), "price")),
            option_right: value.option_right,
            status: value.status.to_string(),
        }
    }
}

impl From<RustVenue> for ReferenceVenue {
    fn from(value: RustVenue) -> Self {
        Self {
            venue_id: value.venue_id.to_string(),
            name: value.name,
            venue_kind: value.venue_kind.as_str().to_owned(),
            roles: value
                .roles
                .into_iter()
                .map(|role| role.as_str().to_owned())
                .collect(),
            mic: value.mic.map(|item| item.to_string()),
            operating_mic: value.operating_mic.map(|item| item.to_string()),
            parent_venue_id: value.parent_venue_id.map(|item| item.to_string()),
            jurisdiction: value.jurisdiction.map(|item| item.to_string()),
            status: value.status.to_string(),
        }
    }
}

impl From<RustVenueListing> for ReferenceVenueListing {
    fn from(value: RustVenueListing) -> Self {
        Self {
            listing_id: value.listing_id.to_string(),
            instrument_id: value.instrument_id.to_string(),
            listing_venue_id: value.listing_venue_id.to_string(),
            market_segment_id: value.market_segment_id.map(|item| item.to_string()),
            listing_symbol: value.listing_symbol.to_string(),
            listing_role: value.listing_role.as_str().to_owned(),
            status: value.status.to_string(),
            effective_from_unix_nanos: value.effective_from_unix_nanos.get(),
            effective_to_unix_nanos: value.effective_to_unix_nanos.map(UnixNanos::get),
        }
    }
}

impl From<RustVenueMarket> for ReferenceVenueMarket {
    fn from(value: RustVenueMarket) -> Self {
        Self {
            market_id: value.market_id.to_string(),
            instrument_id: value.instrument_id.to_string(),
            execution_venue_id: value.execution_venue_id.to_string(),
            origin_listing_id: value.origin_listing_id.map(|item| item.to_string()),
            market_segment_id: value.market_segment_id.map(|item| item.to_string()),
            venue_symbol: value.venue_symbol.map(|item| item.to_string()),
            trading_calendar_id: value.trading_calendar_id.map(|item| item.to_string()),
            trading_session_ids: value
                .trading_session_ids
                .into_iter()
                .map(|item| item.to_string())
                .collect(),
            base_asset_id: value.base_asset_id.map(|item| item.to_string()),
            quote_asset_id: value.quote_asset_id.map(|item| item.to_string()),
            status: value.status.to_string(),
            trading_rules: ReferenceTradingRules {
                price_increment: value
                    .trading_rules
                    .price_tick
                    .map(|item| native_decimal(item.mantissa(), item.scale(), "price")),
                quantity_increment: value
                    .trading_rules
                    .quantity_tick
                    .map(|item| native_decimal(item.mantissa(), item.scale(), "quantity")),
                minimum_quantity: value
                    .trading_rules
                    .minimum_quantity
                    .map(|item| native_decimal(item.mantissa(), item.scale(), "quantity")),
                minimum_notional: value
                    .trading_rules
                    .minimum_notional
                    .map(|item| native_decimal(item.mantissa(), item.scale(), "money")),
                contract_multiplier: value
                    .trading_rules
                    .contract_size
                    .map(|item| native_decimal(item.mantissa(), item.scale(), "rate")),
            },
            effective_from_unix_nanos: value.effective_from_unix_nanos.get(),
            effective_to_unix_nanos: value.effective_to_unix_nanos.map(UnixNanos::get),
        }
    }
}

impl From<RustInstrumentAvailability> for ReferenceInstrumentAvailability {
    fn from(value: RustInstrumentAvailability) -> Self {
        Self {
            source_id: value.source_id.to_string(),
            instrument: value.instrument.into(),
        }
    }
}

impl From<RustListing> for ReferenceListing {
    fn from(value: RustListing) -> Self {
        Self {
            listing_id: value.listing_id.to_string(),
            instrument_id: value.instrument_id.to_string(),
            exchange_id: value.exchange_id.to_string(),
            exchange_symbol: value.exchange_symbol.to_string(),
            status: value.status.to_string(),
            effective_from_unix_nanos: value.effective_from_unix_nanos.get(),
            effective_to_unix_nanos: value.effective_to_unix_nanos.map(|v| v.get()),
        }
    }
}

impl From<RustMarket> for ReferenceMarket {
    fn from(value: RustMarket) -> Self {
        Self {
            market_id: value.market_id.to_string(),
            instrument_id: value.instrument_id.to_string(),
            listing_id: value.listing_id.map(|v| v.to_string()),
            exchange_id: value.exchange_id.to_string(),
            instrument_kind: value.instrument_kind.to_string(),
            asset_type: value.asset_type.map(|v| v.to_string()),
            underlying_instrument_id: value.underlying_instrument_id.map(|v| v.to_string()),
            venue_symbol: value.venue_symbol.map(|v| v.to_string()),
            base_asset_id: value.base_asset_id.map(|v| v.to_string()),
            quote_asset_id: value.quote_asset_id.map(|v| v.to_string()),
            status: value.status.to_string(),
            price_tick: value
                .price_tick
                .map(|value| native_decimal(value.mantissa(), value.scale(), "price")),
            quantity_tick: value
                .quantity_tick
                .map(|value| native_decimal(value.mantissa(), value.scale(), "quantity")),
            price_precision: value.price_precision,
            quantity_precision: value.quantity_precision,
            minimum_quantity: value
                .minimum_quantity
                .map(|value| native_decimal(value.mantissa(), value.scale(), "quantity")),
            minimum_notional: value
                .minimum_notional
                .map(|value| native_decimal(value.mantissa(), value.scale(), "money")),
            contract_size: value
                .contract_size
                .map(|value| native_decimal(value.mantissa(), value.scale(), "rate")),
            effective_from_unix_nanos: value.effective_from_unix_nanos.get(),
            effective_to_unix_nanos: value.effective_to_unix_nanos.map(|v| v.get()),
        }
    }
}

impl From<RustEvent> for ReferenceLifecycleEvent {
    fn from(value: RustEvent) -> Self {
        Self {
            sequence: value.sequence.get(),
            event_id: value.event_id,
            event_type: value.event_type,
            event_time_unix_nanos: value.event_time_unix_nanos.get(),
            record_kind: value.record_kind,
            record_id: value.record_id,
            market_id: value.market_id.map(|v| v.to_string()),
            instrument_id: value.instrument_id.map(|v| v.to_string()),
            listing_id: value.listing_id.map(|v| v.to_string()),
            exchange_id: value.exchange_id.map(|v| v.to_string()),
            venue_symbol: value.venue_symbol.map(|v| v.to_string()),
            previous_status: value.previous_status.map(|v| v.to_string()),
            current_status: value.current_status.map(|v| v.to_string()),
            previous_symbol: value.previous_symbol,
            current_symbol: value.current_symbol,
            operation: value.operation,
            provenance: value.provenance,
            conflict_policy: value.conflict_policy,
            generation: value.generation.get(),
        }
    }
}

fn contract_error(error: ContractError) -> PyErr {
    match error {
        ContractError::Invalid(message) => ReferenceInvalidCatalogError::new_err(message),
        ContractError::Transport(message) => ReferenceCatalogUnavailableError::new_err(message),
    }
}

fn event_metadata(value: EventMetadataOwned) -> ReferenceEventMetadata {
    ReferenceEventMetadata {
        event_id: value.event_id.to_string(),
        stream_id: value.stream_id,
        sequence: value.sequence.get(),
        producer: value.producer_id.to_string(),
        producer_incarnation: value.producer_incarnation,
        workspace_id: value.workspace_id.to_string(),
        launch_id: value.launch_id.map(|item| item.to_string()),
        instance_id: value.instance_id.map(|item| item.to_string()),
        correlation_id: value.correlation_id,
        causation_id: value.causation_id,
        occurred_at_unix_nanos: value.occurred_at_unix_nanos.get(),
        published_at_unix_nanos: value.published_at_unix_nanos.get(),
    }
}

fn reference_event<T: PyClass<BaseType = PyAny>>(
    py: Python<'_>,
    metadata: kairos_protocol::generated::kairos::common::v_2::EventMetadata<'_>,
    kind: &str,
    catalog_revision: u64,
    data: T,
) -> PyResult<ReferenceEvent> {
    let metadata = decode_event_metadata(metadata)
        .map_err(|error| ReferenceInvalidEventError::new_err(error.to_string()))?;
    Ok(ReferenceEvent {
        metadata: event_metadata(metadata),
        kind: kind.to_owned(),
        catalog_revision,
        data: Py::new(py, data)?.into_any(),
    })
}

#[pyfunction]
fn decode_event(py: Python<'_>, payload: &[u8]) -> PyResult<ReferenceEvent> {
    let event = kairos_reference_contract::decode_event(payload)
        .map_err(|error| ReferenceInvalidEventError::new_err(error.to_string()))?;
    project_reference_event(py, event)
}

fn project_reference_event(
    py: Python<'_>,
    event: kairos_reference_contract::ReferenceEvent<'_>,
) -> PyResult<ReferenceEvent> {
    use kairos_reference_contract::ReferenceEvent as Event;
    let kind = event.kind().as_str();
    match event {
        Event::ExchangeUpserted(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_exchange(value.exchange()),
        ),
        Event::ExchangeUpdated(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_exchange(value.exchange()),
        ),
        Event::AssetUpserted(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_asset(value.asset()),
        ),
        Event::AssetUpdated(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_asset(value.asset()),
        ),
        Event::InstrumentUpserted(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_instrument(value.instrument()),
        ),
        Event::InstrumentUpdated(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_instrument(value.instrument()),
        ),
        Event::ListingUpserted(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_listing(value.listing()),
        ),
        Event::ListingUpdated(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_listing(value.listing()),
        ),
        Event::MarketUpserted(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_market(value.market()),
        ),
        Event::MarketUpdated(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_market(value.market()),
        ),
        Event::VenueUpserted(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_venue(value.venue()),
        ),
        Event::VenueUpdated(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_venue(value.venue()),
        ),
        Event::VenueListingUpserted(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_venue_listing(value.listing()),
        ),
        Event::VenueListingUpdated(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_venue_listing(value.listing()),
        ),
        Event::VenueMarketUpserted(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_venue_market(value.market()),
        ),
        Event::VenueMarketUpdated(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_venue_market(value.market()),
        ),
        Event::ProviderCatalogMembershipUpserted(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_provider_catalog_membership(value.membership()),
        ),
        Event::ProviderCatalogMembershipUpdated(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            project_provider_catalog_membership(value.membership()),
        ),
        Event::CoverageStateChanged(value) => reference_event(
            py,
            value.metadata(),
            kind,
            value.catalog_revision(),
            ReferenceCoverageStateChange {
                coverage: project_coverage(value.coverage()),
                previous_state: enum_name(value.previous_state().variant_name()),
            },
        ),
    }
}

fn enum_name(value: Option<&str>) -> String {
    value.unwrap_or("UNKNOWN").to_ascii_lowercase()
}

fn project_venue(value: fb3::Venue<'_>) -> ReferenceVenue {
    ReferenceVenue {
        venue_id: value.venue_id().to_owned(),
        name: value.name().to_owned(),
        venue_kind: enum_name(value.venue_kind().variant_name()),
        roles: value
            .roles()
            .iter()
            .map(|role| enum_name(role.variant_name()))
            .collect(),
        mic: value.mic().map(ToOwned::to_owned),
        operating_mic: value.operating_mic().map(ToOwned::to_owned),
        parent_venue_id: value.parent_venue_id().map(ToOwned::to_owned),
        jurisdiction: value.jurisdiction().map(ToOwned::to_owned),
        status: enum_name(value.status().variant_name()),
    }
}

fn project_venue_listing(value: fb3::Listing<'_>) -> ReferenceVenueListing {
    ReferenceVenueListing {
        listing_id: value.listing_id().to_owned(),
        instrument_id: value.instrument_id().to_owned(),
        listing_venue_id: value.listing_venue_id().to_owned(),
        market_segment_id: value.market_segment_id().map(ToOwned::to_owned),
        listing_symbol: value.listing_symbol().to_owned(),
        listing_role: enum_name(value.listing_role().variant_name()),
        status: enum_name(value.status().variant_name()),
        effective_from_unix_nanos: value.effective_from_unix_nanos(),
        effective_to_unix_nanos: optional_nanos(value.effective_to_unix_nanos()),
    }
}

fn project_venue_market(value: fb3::Market<'_>) -> ReferenceVenueMarket {
    let rules = value.trading_rules();
    ReferenceVenueMarket {
        market_id: value.market_id().to_owned(),
        instrument_id: value.instrument_id().to_owned(),
        execution_venue_id: value.execution_venue_id().to_owned(),
        origin_listing_id: value.origin_listing_id().map(ToOwned::to_owned),
        market_segment_id: value.market_segment_id().map(ToOwned::to_owned),
        venue_symbol: value.venue_symbol().map(ToOwned::to_owned),
        trading_calendar_id: value.trading_calendar_id().map(ToOwned::to_owned),
        trading_session_ids: value
            .trading_session_ids()
            .map(|values| values.iter().map(ToOwned::to_owned).collect())
            .unwrap_or_default(),
        base_asset_id: value.base_asset_id().map(ToOwned::to_owned),
        quote_asset_id: value.quote_asset_id().map(ToOwned::to_owned),
        status: enum_name(value.status().variant_name()),
        trading_rules: ReferenceTradingRules {
            price_increment: rules
                .price_tick()
                .map(|item| semantic_decimal(item, "price")),
            quantity_increment: rules
                .quantity_tick()
                .map(|item| semantic_decimal(item, "quantity")),
            minimum_quantity: rules
                .minimum_quantity()
                .map(|item| semantic_decimal(item, "quantity")),
            minimum_notional: rules
                .minimum_notional()
                .map(|item| semantic_decimal(item, "money")),
            contract_multiplier: rules
                .contract_size()
                .map(|item| semantic_decimal(item, "rate")),
        },
        effective_from_unix_nanos: value.effective_from_unix_nanos(),
        effective_to_unix_nanos: optional_nanos(value.effective_to_unix_nanos()),
    }
}

fn project_provider_catalog_membership(
    value: fb3::ProviderCatalogMembership<'_>,
) -> ReferenceProviderCatalogMembership {
    ReferenceProviderCatalogMembership {
        source_id: value.source_id().to_owned(),
        instrument_id: value.instrument_id().to_owned(),
        provider_symbol: value.provider_symbol().map(ToOwned::to_owned),
        provider_product: value.provider_product().map(ToOwned::to_owned),
        status: enum_name(value.status().variant_name()),
        effective_from_unix_nanos: value.effective_from_unix_nanos(),
        effective_to_unix_nanos: optional_nanos(value.effective_to_unix_nanos()),
    }
}

fn project_coverage(value: fb3::ReferenceCoverage<'_>) -> ReferenceCoverage {
    ReferenceCoverage {
        coverage_id: value.coverage_id().to_owned(),
        source_id: value.source_id().to_owned(),
        fact_kinds: value
            .fact_kinds()
            .iter()
            .map(|kind| enum_name(kind.variant_name()))
            .collect(),
        scope_kind: value.scope_kind().to_owned(),
        scope_binding: value.scope_binding().map(ToOwned::to_owned),
        scope_ids: value
            .scope_ids()
            .map(|values| values.iter().map(ToOwned::to_owned).collect())
            .unwrap_or_default(),
        scope_instrument_kind: value.scope_instrument_kind().map(ToOwned::to_owned),
        completeness: enum_name(value.completeness().variant_name()),
        state: enum_name(value.state().variant_name()),
        generation: value.generation(),
        event_sequence: value.event_sequence(),
        last_attempt_unix_nanos: optional_nanos(value.last_attempt_unix_nanos()),
        last_success_unix_nanos: optional_nanos(value.last_success_unix_nanos()),
        stale_after_unix_nanos: optional_nanos(value.stale_after_unix_nanos()),
        has_last_known_good: value.has_last_known_good(),
    }
}

fn project_exchange(value: fb::Exchange<'_>) -> ReferenceExchange {
    ReferenceExchange {
        exchange_id: value.exchange_id().to_owned(),
        name: value.name().to_owned(),
        status: lifecycle_status(value.status()),
    }
}

fn project_asset(value: fb::Asset<'_>) -> ReferenceAsset {
    ReferenceAsset {
        asset_id: value.asset_id().to_owned(),
        code: value.code().to_owned(),
        name: value.name().map(ToOwned::to_owned),
        asset_class: value.asset_class().to_owned(),
        status: lifecycle_status(value.status()),
    }
}

fn project_instrument(value: fb::Instrument<'_>) -> ReferenceInstrument {
    ReferenceInstrument {
        instrument_id: value.instrument_id().to_owned(),
        symbol: value.symbol().to_owned(),
        name: value.name().map(ToOwned::to_owned),
        instrument_type: value.instrument_type().to_owned(),
        product_family: value.product_family().map(ToOwned::to_owned),
        issuer_id: value.issuer_id().map(ToOwned::to_owned),
        share_class: value.share_class().map(ToOwned::to_owned),
        primary_currency_asset_id: value.primary_currency_asset_id().map(ToOwned::to_owned),
        settlement_asset_id: value.settlement_asset_id().map(ToOwned::to_owned),
        underlying_instrument_id: value.underlying_instrument_id().map(ToOwned::to_owned),
        expiry_unix_nanos: optional_nanos(value.expiry_unix_nanos()),
        strike: value.strike().map(|value| semantic_decimal(value, "price")),
        option_right: value.option_right().map(ToOwned::to_owned),
        status: lifecycle_status(value.status()),
    }
}

fn project_listing(value: fb::Listing<'_>) -> ReferenceListing {
    ReferenceListing {
        listing_id: value.listing_id().to_owned(),
        instrument_id: value.instrument_id().to_owned(),
        exchange_id: value.exchange_id().to_owned(),
        exchange_symbol: value.exchange_symbol().to_owned(),
        status: lifecycle_status(value.status()),
        effective_from_unix_nanos: value.effective_from_unix_nanos(),
        effective_to_unix_nanos: optional_nanos(value.effective_to_unix_nanos()),
    }
}

fn project_market(value: fb::Market<'_>) -> ReferenceMarket {
    ReferenceMarket {
        market_id: value.market_id().to_owned(),
        instrument_id: value.instrument_id().to_owned(),
        listing_id: value.listing_id().map(ToOwned::to_owned),
        exchange_id: value.exchange_id().to_owned(),
        instrument_kind: value.instrument_kind().to_owned(),
        asset_type: value.asset_type().map(ToOwned::to_owned),
        underlying_instrument_id: value.underlying_instrument_id().map(ToOwned::to_owned),
        venue_symbol: value.venue_symbol().map(ToOwned::to_owned),
        base_asset_id: value.base_asset_id().map(ToOwned::to_owned),
        quote_asset_id: value.quote_asset_id().map(ToOwned::to_owned),
        status: lifecycle_status(value.status()),
        price_tick: value
            .price_tick()
            .map(|value| semantic_decimal(value, "price")),
        quantity_tick: value
            .quantity_tick()
            .map(|value| semantic_decimal(value, "quantity")),
        price_precision: value.price_precision(),
        quantity_precision: value.quantity_precision(),
        minimum_quantity: value
            .minimum_quantity()
            .map(|value| semantic_decimal(value, "quantity")),
        minimum_notional: value
            .minimum_notional()
            .map(|value| semantic_decimal(value, "money")),
        contract_size: value
            .contract_size()
            .map(|value| semantic_decimal(value, "rate")),
        effective_from_unix_nanos: value.effective_from_unix_nanos(),
        effective_to_unix_nanos: optional_nanos(value.effective_to_unix_nanos()),
    }
}

fn lifecycle_status(value: fb::ReferenceLifecycleStatus) -> String {
    value
        .variant_name()
        .unwrap_or("UNSPECIFIED")
        .to_ascii_lowercase()
}

fn optional_nanos(value: u64) -> Option<u64> {
    (value != 0).then_some(value)
}

fn semantic_decimal(value: &Decimal64, semantic_type: &'static str) -> NativeDecimal {
    native_decimal(value.mantissa(), value.scale(), semantic_type)
}

fn native_decimal(mantissa: i64, scale: u8, semantic_type: &'static str) -> NativeDecimal {
    NativeDecimal {
        mantissa,
        scale,
        semantic_type,
    }
}

fn decimal_text(mantissa: i64, scale: u8) -> String {
    let scale = usize::from(scale);
    if scale == 0 {
        return mantissa.to_string();
    }
    let negative = mantissa < 0;
    let mut digits = mantissa.unsigned_abs().to_string();
    if digits.len() <= scale {
        digits = format!("{}{}", "0".repeat(scale + 1 - digits.len()), digits);
    }
    let split = digits.len() - scale;
    format!(
        "{}{}.{}",
        if negative { "-" } else { "" },
        &digits[..split],
        &digits[split..]
    )
}

#[pyfunction]
fn build_info() -> NativeBuildInfo {
    NativeBuildInfo {
        api_version: API_VERSION,
        owner: "Reference".into(),
        package_version: env!("CARGO_PKG_VERSION").into(),
    }
}

#[pymodule]
fn _native_reference_contract(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add(
        "REFERENCE_EVENT_STREAM_ID",
        kairos_reference_contract::REFERENCE_EVENTS_STREAM_ID,
    )?;
    module.add(
        "DEFAULT_AERON_CHANNEL",
        kairos_reference_contract::DEFAULT_AERON_CHANNEL,
    )?;
    module.add(
        "ReferenceInvalidCatalogError",
        module.py().get_type::<ReferenceInvalidCatalogError>(),
    )?;
    module.add(
        "ReferenceCatalogUnavailableError",
        module.py().get_type::<ReferenceCatalogUnavailableError>(),
    )?;
    module.add(
        "ReferenceInvalidEventError",
        module.py().get_type::<ReferenceInvalidEventError>(),
    )?;
    module.add_class::<NativeBuildInfo>()?;
    module.add_class::<ReferenceEventMetadata>()?;
    module.add_class::<ReferenceEvent>()?;
    module.add_class::<ReferenceLiveEventView>()?;
    module.add_class::<ReferenceLiveSubscription>()?;
    module.add_class::<ReferenceExchange>()?;
    module.add_class::<ReferenceAsset>()?;
    module.add_class::<ReferenceInstrumentRef>()?;
    module.add_class::<ReferenceInstrument>()?;
    module.add_class::<ReferenceInstrumentAvailability>()?;
    module.add_class::<ReferenceListing>()?;
    module.add_class::<ReferenceTradingRules>()?;
    module.add_class::<ReferenceMarket>()?;
    module.add_class::<ReferenceVenue>()?;
    module.add_class::<ReferenceVenueListing>()?;
    module.add_class::<ReferenceVenueMarket>()?;
    module.add_class::<ReferenceProviderCatalogMembership>()?;
    module.add_class::<ReferenceCoverage>()?;
    module.add_class::<ReferenceCoverageStateChange>()?;
    module.add_class::<ReferenceMarketResolutionRecord>()?;
    module.add_class::<ReferenceMarketResolutionResponse>()?;
    module.add_class::<ReferenceLifecycleEvent>()?;
    module.add_class::<ReferenceCatalogStatus>()?;
    module.add_class::<ReferenceCoverageEvidence>()?;
    module.add_class::<ReferenceQueryEvidence>()?;
    module.add_class::<ReferenceInstrumentSearchResponse>()?;
    module.add_class::<ReferenceVenueSearchResponse>()?;
    module.add_class::<ReferenceVenueListingSearchResponse>()?;
    module.add_class::<ReferenceVenueMarketSearchResponse>()?;
    module.add_class::<ReferenceCatalog>()?;
    module.add_class::<ReferenceReadSession>()?;
    module.add_function(wrap_pyfunction!(build_info, module)?)?;
    module.add_function(wrap_pyfunction!(decode_event, module)?)?;
    Ok(())
}
