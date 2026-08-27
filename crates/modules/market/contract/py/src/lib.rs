use std::path::PathBuf;
use std::sync::Mutex;

use kairos_market_contract::{
    ContractError, MarketBarCurrent as RustBar, MarketBarKind,
    MarketCurrentEvidence as RustEvidence, MarketFreshnessCurrent as RustFreshness,
    MarketFreshnessStatus, MarketFundingRateCurrent as RustFunding,
    MarketGreeksCurrent as RustGreeks, MarketIndexPriceCurrent as RustIndex,
    MarketIndexedView as RustView, MarketMarkPriceCurrent as RustMark,
    MarketObservationScope as RustScope, MarketOpenInterestCurrent as RustOpenInterest,
    MarketOrderBookCurrent as RustOrderBook, MarketQuoteCurrent as RustQuote,
    MarketRateCurrent as RustRate, MarketTicker24hCurrent as RustTicker, MarketViewKey,
    MarketViewKind,
};
use kairos_primitives::decimal::{DecimalParts, Price, Quantity};
use kairos_primitives::runtime::InstanceIdentity;
use pyo3::create_exception;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

create_exception!(
    _native_market_contract,
    MarketInvalidCurrentViewError,
    PyValueError
);
create_exception!(
    _native_market_contract,
    MarketCurrentViewUnavailableError,
    PyRuntimeError
);

const API_VERSION: u32 = 1;

#[pyclass(frozen, module = "kairospy._native_market_contract")]
struct NativeBuildInfo {
    #[pyo3(get)]
    api_version: u32,
    #[pyo3(get)]
    owner: String,
    #[pyo3(get)]
    package_version: String,
}

#[pyclass(frozen, module = "kairospy._native_market_contract")]
#[derive(Clone)]
struct NativeDecimal {
    #[pyo3(get)]
    mantissa: i64,
    #[pyo3(get)]
    scale: u8,
}

#[pyclass(frozen, module = "kairospy._native_market_contract")]
#[derive(Clone)]
struct MarketCurrentEvidence {
    #[pyo3(get)]
    resource_epoch: u64,
    #[pyo3(get)]
    producer_incarnation: u64,
    #[pyo3(get)]
    applied_event_sequence: u64,
    #[pyo3(get)]
    committed_at_unix_nanos: u64,
    #[pyo3(get)]
    source_event_id: Option<String>,
    #[pyo3(get)]
    synchronized: bool,
}

#[pyclass(frozen, module = "kairospy._native_market_contract")]
#[derive(Clone)]
struct MarketObservationScope {
    #[pyo3(get)]
    kind: String,
    #[pyo3(get)]
    market_id: Option<String>,
    #[pyo3(get)]
    instrument_id: Option<String>,
    #[pyo3(get)]
    network_id: Option<String>,
}

#[pyclass(frozen, module = "kairospy._native_market_contract")]
struct MarketQuoteCurrent {
    #[pyo3(get)]
    evidence: MarketCurrentEvidence,
    #[pyo3(get)]
    quote_id: Option<String>,
    #[pyo3(get)]
    scope: MarketObservationScope,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    provider: String,
    #[pyo3(get)]
    bid_price: Option<NativeDecimal>,
    #[pyo3(get)]
    bid_quantity: Option<NativeDecimal>,
    #[pyo3(get)]
    ask_price: Option<NativeDecimal>,
    #[pyo3(get)]
    ask_quantity: Option<NativeDecimal>,
    #[pyo3(get)]
    bid_venue_code: Option<String>,
    #[pyo3(get)]
    ask_venue_code: Option<String>,
    #[pyo3(get)]
    tape: Option<u32>,
    #[pyo3(get)]
    source_observed_at_unix_nanos: u64,
    #[pyo3(get)]
    received_at_unix_nanos: u64,
}

#[pyclass(frozen, module = "kairospy._native_market_contract")]
struct MarketBarCurrent {
    #[pyo3(get)]
    evidence: MarketCurrentEvidence,
    #[pyo3(get)]
    scope: MarketObservationScope,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    provider: String,
    #[pyo3(get)]
    bar_spec_id: String,
    #[pyo3(get)]
    kind: String,
    #[pyo3(get)]
    window_start_unix_nanos: u64,
    #[pyo3(get)]
    window_end_unix_nanos: u64,
    #[pyo3(get)]
    open: NativeDecimal,
    #[pyo3(get)]
    high: NativeDecimal,
    #[pyo3(get)]
    low: NativeDecimal,
    #[pyo3(get)]
    close: NativeDecimal,
    #[pyo3(get)]
    volume: Option<NativeDecimal>,
    #[pyo3(get)]
    source_observed_at_unix_nanos: u64,
    #[pyo3(get)]
    received_at_unix_nanos: u64,
}

#[pyclass(frozen, module = "kairospy._native_market_contract")]
struct MarketGreeksCurrent {
    #[pyo3(get)]
    evidence: MarketCurrentEvidence,
    #[pyo3(get)]
    scope: MarketObservationScope,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    provider: String,
    #[pyo3(get)]
    expiry_unix_nanos: Option<u64>,
    #[pyo3(get)]
    strike: Option<NativeDecimal>,
    #[pyo3(get)]
    delta: Option<NativeDecimal>,
    #[pyo3(get)]
    gamma: Option<NativeDecimal>,
    #[pyo3(get)]
    vega: Option<NativeDecimal>,
    #[pyo3(get)]
    theta: Option<NativeDecimal>,
    #[pyo3(get)]
    implied_volatility: Option<NativeDecimal>,
    #[pyo3(get)]
    source_observed_at_unix_nanos: u64,
    #[pyo3(get)]
    received_at_unix_nanos: u64,
    #[pyo3(get)]
    derivation_id: Option<String>,
}

#[pyclass(frozen, module = "kairospy._native_market_contract")]
struct MarketRateCurrent {
    #[pyo3(get)]
    evidence: MarketCurrentEvidence,
    #[pyo3(get)]
    rate_id: String,
    #[pyo3(get)]
    scope: MarketObservationScope,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    provider: String,
    #[pyo3(get)]
    basis: String,
    #[pyo3(get)]
    value: NativeDecimal,
    #[pyo3(get)]
    mark_price: Option<NativeDecimal>,
    #[pyo3(get)]
    source_observed_at_unix_nanos: u64,
    #[pyo3(get)]
    received_at_unix_nanos: u64,
}
#[pyclass(frozen, module = "kairospy._native_market_contract")]
struct MarketTicker24hCurrent {
    #[pyo3(get)]
    evidence: MarketCurrentEvidence,
    #[pyo3(get)]
    scope: MarketObservationScope,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    provider: String,
    #[pyo3(get)]
    last_price: Option<NativeDecimal>,
    #[pyo3(get)]
    bid_price: Option<NativeDecimal>,
    #[pyo3(get)]
    bid_quantity: Option<NativeDecimal>,
    #[pyo3(get)]
    ask_price: Option<NativeDecimal>,
    #[pyo3(get)]
    ask_quantity: Option<NativeDecimal>,
    #[pyo3(get)]
    open_price: Option<NativeDecimal>,
    #[pyo3(get)]
    high_price: Option<NativeDecimal>,
    #[pyo3(get)]
    low_price: Option<NativeDecimal>,
    #[pyo3(get)]
    volume_base: Option<NativeDecimal>,
    #[pyo3(get)]
    volume_quote: Option<NativeDecimal>,
    #[pyo3(get)]
    price_change_abs: Option<NativeDecimal>,
    #[pyo3(get)]
    price_change_pct: Option<NativeDecimal>,
    #[pyo3(get)]
    vwap: Option<NativeDecimal>,
    #[pyo3(get)]
    mark_price: Option<NativeDecimal>,
    #[pyo3(get)]
    source_observed_at_unix_nanos: u64,
    #[pyo3(get)]
    received_at_unix_nanos: u64,
}
#[pyclass(frozen, module = "kairospy._native_market_contract")]
struct MarketMarkPriceCurrent {
    #[pyo3(get)]
    evidence: MarketCurrentEvidence,
    #[pyo3(get)]
    scope: MarketObservationScope,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    provider: String,
    #[pyo3(get)]
    mark_price: NativeDecimal,
    #[pyo3(get)]
    index_price: Option<NativeDecimal>,
    #[pyo3(get)]
    estimated_settlement_price: Option<NativeDecimal>,
    #[pyo3(get)]
    funding_rate: Option<NativeDecimal>,
    #[pyo3(get)]
    next_funding_time_unix_nanos: Option<u64>,
    #[pyo3(get)]
    source_observed_at_unix_nanos: u64,
    #[pyo3(get)]
    received_at_unix_nanos: u64,
}
#[pyclass(frozen, module = "kairospy._native_market_contract")]
struct MarketFundingRateCurrent {
    #[pyo3(get)]
    evidence: MarketCurrentEvidence,
    #[pyo3(get)]
    scope: MarketObservationScope,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    provider: String,
    #[pyo3(get)]
    funding_rate: NativeDecimal,
    #[pyo3(get)]
    funding_period_seconds: u64,
    #[pyo3(get)]
    next_funding_time_unix_nanos: Option<u64>,
    #[pyo3(get)]
    source_observed_at_unix_nanos: u64,
    #[pyo3(get)]
    received_at_unix_nanos: u64,
}
#[pyclass(frozen, module = "kairospy._native_market_contract")]
struct MarketOpenInterestCurrent {
    #[pyo3(get)]
    evidence: MarketCurrentEvidence,
    #[pyo3(get)]
    scope: MarketObservationScope,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    provider: String,
    #[pyo3(get)]
    contracts: NativeDecimal,
    #[pyo3(get)]
    quote_value: Option<NativeDecimal>,
    #[pyo3(get)]
    change_24h: Option<NativeDecimal>,
    #[pyo3(get)]
    change_pct_24h: Option<NativeDecimal>,
    #[pyo3(get)]
    source_observed_at_unix_nanos: u64,
    #[pyo3(get)]
    received_at_unix_nanos: u64,
}
#[pyclass(frozen, module = "kairospy._native_market_contract")]
struct MarketIndexPriceCurrent {
    #[pyo3(get)]
    evidence: MarketCurrentEvidence,
    #[pyo3(get)]
    scope: MarketObservationScope,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    provider: String,
    #[pyo3(get)]
    spot_index_price: Option<NativeDecimal>,
    #[pyo3(get)]
    contract_index_price: Option<NativeDecimal>,
    #[pyo3(get)]
    index_price: Option<NativeDecimal>,
    #[pyo3(get)]
    funding_rate: Option<NativeDecimal>,
    #[pyo3(get)]
    source_observed_at_unix_nanos: u64,
    #[pyo3(get)]
    received_at_unix_nanos: u64,
}
#[pyclass(frozen, module = "kairospy._native_market_contract")]
#[derive(Clone)]
struct MarketOrderBookLevel {
    #[pyo3(get)]
    price: NativeDecimal,
    #[pyo3(get)]
    quantity: NativeDecimal,
    #[pyo3(get)]
    order_count: u32,
}
#[pyclass(frozen, module = "kairospy._native_market_contract")]
struct MarketOrderBookCurrent {
    #[pyo3(get)]
    evidence: MarketCurrentEvidence,
    #[pyo3(get)]
    provider: String,
    #[pyo3(get)]
    market_id: String,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    sequence: u64,
    #[pyo3(get)]
    source_observed_at_unix_nanos: u64,
    #[pyo3(get)]
    received_at_unix_nanos: u64,
    #[pyo3(get)]
    checksum: Option<String>,
    #[pyo3(get)]
    depth_policy: String,
    #[pyo3(get)]
    bids: Vec<MarketOrderBookLevel>,
    #[pyo3(get)]
    asks: Vec<MarketOrderBookLevel>,
}
#[pyclass(frozen, module = "kairospy._native_market_contract")]
struct MarketFreshnessCurrent {
    #[pyo3(get)]
    evidence: MarketCurrentEvidence,
    #[pyo3(get)]
    provider: String,
    #[pyo3(get)]
    scope: MarketObservationScope,
    #[pyo3(get)]
    data_kind: String,
    #[pyo3(get)]
    last_event_time_unix_nanos: Option<u64>,
    #[pyo3(get)]
    last_received_time_unix_nanos: Option<u64>,
    #[pyo3(get)]
    age_nanos: u64,
    #[pyo3(get)]
    event_sequence: u64,
    #[pyo3(get)]
    status: String,
}

#[pyclass(module = "kairospy._native_market_contract")]
struct MarketCurrentView {
    creator_pid: u32,
    reader: Mutex<Option<RustView>>,
}

#[pymethods]
impl MarketCurrentView {
    #[new]
    #[pyo3(signature = (root, workspace_id, launch_id=None, instance_id=None))]
    fn new(
        py: Python<'_>,
        root: PathBuf,
        workspace_id: String,
        launch_id: Option<String>,
        instance_id: Option<String>,
    ) -> PyResult<Self> {
        let identity = identity(workspace_id, launch_id, instance_id)?;
        let reader = py
            .detach(move || RustView::open(root, &identity))
            .map_err(contract_error)?;
        Ok(Self {
            creator_pid: std::process::id(),
            reader: Mutex::new(Some(reader)),
        })
    }

    #[pyo3(signature = (scope_key, provider))]
    fn quote(
        &self,
        py: Python<'_>,
        scope_key: String,
        provider: String,
    ) -> PyResult<Option<MarketQuoteCurrent>> {
        let key = MarketViewKey::new(scope_key, provider, MarketViewKind::Quote, None::<String>)
            .map_err(contract_error)?;
        self.read(py, move |reader| {
            reader.quote(&key).map(|value| value.map(Into::into))
        })
    }

    fn bar(
        &self,
        py: Python<'_>,
        scope_key: String,
        provider: String,
        qualifier: String,
    ) -> PyResult<Option<MarketBarCurrent>> {
        let key = MarketViewKey::new(scope_key, provider, MarketViewKind::Bar, Some(qualifier))
            .map_err(contract_error)?;
        self.read(py, move |reader| {
            reader.bar(&key).map(|value| value.map(Into::into))
        })
    }

    #[pyo3(signature = (scope_key, provider))]
    fn greeks(
        &self,
        py: Python<'_>,
        scope_key: String,
        provider: String,
    ) -> PyResult<Option<MarketGreeksCurrent>> {
        let key = MarketViewKey::new(scope_key, provider, MarketViewKind::Greeks, None::<String>)
            .map_err(contract_error)?;
        self.read(py, move |reader| {
            reader.greeks(&key).map(|value| value.map(Into::into))
        })
    }

    #[pyo3(signature=(scope_key,provider,qualifier=None))]
    fn rate(
        &self,
        py: Python<'_>,
        scope_key: String,
        provider: String,
        qualifier: Option<String>,
    ) -> PyResult<Option<MarketRateCurrent>> {
        let key = view_key(scope_key, provider, MarketViewKind::Rate, qualifier)?;
        self.read(py, move |r| r.rate(&key).map(|v| v.map(Into::into)))
    }
    #[pyo3(signature=(scope_key,provider,qualifier=None))]
    fn ticker_24h(
        &self,
        py: Python<'_>,
        scope_key: String,
        provider: String,
        qualifier: Option<String>,
    ) -> PyResult<Option<MarketTicker24hCurrent>> {
        let key = view_key(scope_key, provider, MarketViewKind::Ticker24h, qualifier)?;
        self.read(py, move |r| r.ticker_24h(&key).map(|v| v.map(Into::into)))
    }
    #[pyo3(signature=(scope_key,provider,qualifier=None))]
    fn mark_price(
        &self,
        py: Python<'_>,
        scope_key: String,
        provider: String,
        qualifier: Option<String>,
    ) -> PyResult<Option<MarketMarkPriceCurrent>> {
        let key = view_key(scope_key, provider, MarketViewKind::MarkPrice, qualifier)?;
        self.read(py, move |r| r.mark_price(&key).map(|v| v.map(Into::into)))
    }
    #[pyo3(signature=(scope_key,provider,qualifier=None))]
    fn funding_rate(
        &self,
        py: Python<'_>,
        scope_key: String,
        provider: String,
        qualifier: Option<String>,
    ) -> PyResult<Option<MarketFundingRateCurrent>> {
        let key = view_key(scope_key, provider, MarketViewKind::FundingRate, qualifier)?;
        self.read(py, move |r| r.funding_rate(&key).map(|v| v.map(Into::into)))
    }
    #[pyo3(signature=(scope_key,provider,qualifier=None))]
    fn open_interest(
        &self,
        py: Python<'_>,
        scope_key: String,
        provider: String,
        qualifier: Option<String>,
    ) -> PyResult<Option<MarketOpenInterestCurrent>> {
        let key = view_key(scope_key, provider, MarketViewKind::OpenInterest, qualifier)?;
        self.read(py, move |r| {
            r.open_interest(&key).map(|v| v.map(Into::into))
        })
    }
    #[pyo3(signature=(scope_key,provider,qualifier=None))]
    fn index_price(
        &self,
        py: Python<'_>,
        scope_key: String,
        provider: String,
        qualifier: Option<String>,
    ) -> PyResult<Option<MarketIndexPriceCurrent>> {
        let key = view_key(scope_key, provider, MarketViewKind::IndexPrice, qualifier)?;
        self.read(py, move |r| r.index_price(&key).map(|v| v.map(Into::into)))
    }
    #[pyo3(signature=(scope_key,provider,qualifier=None))]
    fn order_book(
        &self,
        py: Python<'_>,
        scope_key: String,
        provider: String,
        qualifier: Option<String>,
    ) -> PyResult<Option<MarketOrderBookCurrent>> {
        let key = view_key(scope_key, provider, MarketViewKind::OrderBook, qualifier)?;
        self.read(py, move |r| r.order_book(&key).map(|v| v.map(Into::into)))
    }
    #[pyo3(signature=(scope_key,provider,qualifier=None))]
    fn freshness(
        &self,
        py: Python<'_>,
        scope_key: String,
        provider: String,
        qualifier: Option<String>,
    ) -> PyResult<Option<MarketFreshnessCurrent>> {
        let key = view_key(scope_key, provider, MarketViewKind::Freshness, qualifier)?;
        self.read(py, move |r| r.freshness(&key).map(|v| v.map(Into::into)))
    }

    fn close(&self, py: Python<'_>) -> PyResult<()> {
        self.ensure_process()?;
        let mut reader = self
            .reader
            .lock()
            .map_err(|_| PyRuntimeError::new_err("Market current-view reader lock is poisoned"))?;
        reader.take();
        drop(reader);
        py.check_signals()?;
        Ok(())
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __exit__(
        &self,
        py: Python<'_>,
        _exception_type: &Bound<'_, PyAny>,
        _exception: &Bound<'_, PyAny>,
        _traceback: &Bound<'_, PyAny>,
    ) -> PyResult<bool> {
        self.close(py)?;
        Ok(false)
    }
}

impl MarketCurrentView {
    fn read<T: Send>(
        &self,
        py: Python<'_>,
        operation: impl FnOnce(&RustView) -> Result<T, ContractError> + Send,
    ) -> PyResult<T> {
        self.ensure_process()?;
        py.detach(|| {
            let reader = self.reader.lock().map_err(|_| {
                PyRuntimeError::new_err("Market current-view reader lock is poisoned")
            })?;
            let reader = reader
                .as_ref()
                .ok_or_else(|| PyRuntimeError::new_err("Market current-view reader is closed"))?;
            operation(reader).map_err(contract_error)
        })
    }

    fn ensure_process(&self) -> PyResult<()> {
        if std::process::id() != self.creator_pid {
            return Err(PyRuntimeError::new_err(
                "Market current-view reader cannot be used after fork",
            ));
        }
        Ok(())
    }
}

fn identity(
    workspace_id: String,
    launch_id: Option<String>,
    instance_id: Option<String>,
) -> PyResult<InstanceIdentity> {
    match (launch_id, instance_id) {
        (Some(launch_id), Some(instance_id)) => {
            InstanceIdentity::new(workspace_id, launch_id, instance_id)
                .map_err(|error| PyValueError::new_err(error.to_string()))
        },
        (None, None) => InstanceIdentity::unscoped(workspace_id)
            .map_err(|error| PyValueError::new_err(error.to_string())),
        _ => Err(PyValueError::new_err(
            "launch_id and instance_id must both be present or both be absent",
        )),
    }
}

fn contract_error(error: ContractError) -> PyErr {
    match error {
        ContractError::Invalid(message) => MarketInvalidCurrentViewError::new_err(message),
        ContractError::Transport(message) | ContractError::Unsupported(message) => {
            MarketCurrentViewUnavailableError::new_err(message)
        },
    }
}

fn view_key(
    scope: String,
    provider: String,
    kind: MarketViewKind,
    qualifier: Option<String>,
) -> PyResult<MarketViewKey> {
    MarketViewKey::new(scope, provider, kind, qualifier).map_err(contract_error)
}

impl From<RustEvidence> for MarketCurrentEvidence {
    fn from(value: RustEvidence) -> Self {
        Self {
            resource_epoch: value.resource_epoch,
            producer_incarnation: value.producer_incarnation,
            applied_event_sequence: value.applied_event_sequence.get(),
            committed_at_unix_nanos: value.committed_at.get(),
            source_event_id: value.source_event_id,
            synchronized: value.synchronized,
        }
    }
}

impl From<RustScope> for MarketObservationScope {
    fn from(value: RustScope) -> Self {
        match value {
            RustScope::Market(market_id) => Self {
                kind: "market".to_owned(),
                market_id: Some(market_id.to_string()),
                instrument_id: None,
                network_id: None,
            },
            RustScope::Consolidated {
                instrument_id,
                network_id,
            } => Self {
                kind: "consolidated".to_owned(),
                market_id: None,
                instrument_id: Some(instrument_id.to_string()),
                network_id,
            },
        }
    }
}

impl From<RustQuote> for MarketQuoteCurrent {
    fn from(value: RustQuote) -> Self {
        Self {
            evidence: value.evidence.into(),
            quote_id: value.quote_id,
            scope: value.scope.into(),
            instrument_id: value.instrument_id.to_string(),
            provider: value.provider.to_string(),
            bid_price: value.bid_price.map(decimal_from_price),
            bid_quantity: value.bid_quantity.map(decimal_from_quantity),
            ask_price: value.ask_price.map(decimal_from_price),
            ask_quantity: value.ask_quantity.map(decimal_from_quantity),
            bid_venue_code: value.bid_venue_code,
            ask_venue_code: value.ask_venue_code,
            tape: value.tape,
            source_observed_at_unix_nanos: value.source_observed_at.get(),
            received_at_unix_nanos: value.received_at.get(),
        }
    }
}

impl From<RustBar> for MarketBarCurrent {
    fn from(value: RustBar) -> Self {
        Self {
            evidence: value.evidence.into(),
            scope: value.scope.into(),
            instrument_id: value.instrument_id.to_string(),
            provider: value.provider.to_string(),
            bar_spec_id: value.bar_spec_id,
            kind: match value.kind {
                MarketBarKind::Trades => "trades",
                MarketBarKind::Quotes => "quotes",
                MarketBarKind::Midpoint => "midpoint",
            }
            .to_owned(),
            window_start_unix_nanos: value.window_start.get(),
            window_end_unix_nanos: value.window_end.get(),
            open: decimal_from_price(value.open),
            high: decimal_from_price(value.high),
            low: decimal_from_price(value.low),
            close: decimal_from_price(value.close),
            volume: value.volume.map(decimal_from_quantity),
            source_observed_at_unix_nanos: value.source_observed_at.get(),
            received_at_unix_nanos: value.received_at.get(),
        }
    }
}

impl From<RustGreeks> for MarketGreeksCurrent {
    fn from(value: RustGreeks) -> Self {
        Self {
            evidence: value.evidence.into(),
            scope: value.scope.into(),
            instrument_id: value.instrument_id.to_string(),
            provider: value.provider.to_string(),
            expiry_unix_nanos: value.expiry.map(|value| value.get()),
            strike: value.strike.map(decimal_from_price),
            delta: value.delta.map(decimal_from_parts),
            gamma: value.gamma.map(decimal_from_parts),
            vega: value.vega.map(decimal_from_parts),
            theta: value.theta.map(decimal_from_parts),
            implied_volatility: value.implied_volatility.map(decimal_from_parts),
            source_observed_at_unix_nanos: value.source_observed_at.get(),
            received_at_unix_nanos: value.received_at.get(),
            derivation_id: value.derivation_id,
        }
    }
}

impl From<RustRate> for MarketRateCurrent {
    fn from(v: RustRate) -> Self {
        Self {
            evidence: v.evidence.into(),
            rate_id: v.rate_id,
            scope: v.scope.into(),
            instrument_id: v.instrument_id.to_string(),
            provider: v.provider.to_string(),
            basis: v.basis,
            value: decimal_from_parts(v.value),
            mark_price: v.mark_price.map(decimal_from_price),
            source_observed_at_unix_nanos: v.source_observed_at.get(),
            received_at_unix_nanos: v.received_at.get(),
        }
    }
}
impl From<RustTicker> for MarketTicker24hCurrent {
    fn from(v: RustTicker) -> Self {
        Self {
            evidence: v.evidence.into(),
            scope: v.scope.into(),
            instrument_id: v.instrument_id.to_string(),
            provider: v.provider.to_string(),
            last_price: v.last_price.map(decimal_from_price),
            bid_price: v.bid_price.map(decimal_from_price),
            bid_quantity: v.bid_quantity.map(decimal_from_quantity),
            ask_price: v.ask_price.map(decimal_from_price),
            ask_quantity: v.ask_quantity.map(decimal_from_quantity),
            open_price: v.open_price.map(decimal_from_price),
            high_price: v.high_price.map(decimal_from_price),
            low_price: v.low_price.map(decimal_from_price),
            volume_base: v.volume_base.map(decimal_from_quantity),
            volume_quote: v.volume_quote.map(decimal_from_parts),
            price_change_abs: v.price_change_abs.map(decimal_from_price),
            price_change_pct: v.price_change_pct.map(decimal_from_parts),
            vwap: v.vwap.map(decimal_from_price),
            mark_price: v.mark_price.map(decimal_from_price),
            source_observed_at_unix_nanos: v.source_observed_at.get(),
            received_at_unix_nanos: v.received_at.get(),
        }
    }
}
impl From<RustMark> for MarketMarkPriceCurrent {
    fn from(v: RustMark) -> Self {
        Self {
            evidence: v.evidence.into(),
            scope: v.scope.into(),
            instrument_id: v.instrument_id.to_string(),
            provider: v.provider.to_string(),
            mark_price: decimal_from_price(v.mark_price),
            index_price: v.index_price.map(decimal_from_price),
            estimated_settlement_price: v.estimated_settlement_price.map(decimal_from_price),
            funding_rate: v.funding_rate.map(decimal_from_parts),
            next_funding_time_unix_nanos: v.next_funding_time.map(|v| v.get()),
            source_observed_at_unix_nanos: v.source_observed_at.get(),
            received_at_unix_nanos: v.received_at.get(),
        }
    }
}
impl From<RustFunding> for MarketFundingRateCurrent {
    fn from(v: RustFunding) -> Self {
        Self {
            evidence: v.evidence.into(),
            scope: v.scope.into(),
            instrument_id: v.instrument_id.to_string(),
            provider: v.provider.to_string(),
            funding_rate: decimal_from_parts(v.funding_rate),
            funding_period_seconds: v.funding_period_seconds,
            next_funding_time_unix_nanos: v.next_funding_time.map(|v| v.get()),
            source_observed_at_unix_nanos: v.source_observed_at.get(),
            received_at_unix_nanos: v.received_at.get(),
        }
    }
}
impl From<RustOpenInterest> for MarketOpenInterestCurrent {
    fn from(v: RustOpenInterest) -> Self {
        Self {
            evidence: v.evidence.into(),
            scope: v.scope.into(),
            instrument_id: v.instrument_id.to_string(),
            provider: v.provider.to_string(),
            contracts: decimal_from_quantity(v.contracts),
            quote_value: v.quote_value.map(decimal_from_parts),
            change_24h: v.change_24h.map(decimal_from_parts),
            change_pct_24h: v.change_pct_24h.map(decimal_from_parts),
            source_observed_at_unix_nanos: v.source_observed_at.get(),
            received_at_unix_nanos: v.received_at.get(),
        }
    }
}
impl From<RustIndex> for MarketIndexPriceCurrent {
    fn from(v: RustIndex) -> Self {
        Self {
            evidence: v.evidence.into(),
            scope: v.scope.into(),
            instrument_id: v.instrument_id.to_string(),
            provider: v.provider.to_string(),
            spot_index_price: v.spot_index_price.map(decimal_from_price),
            contract_index_price: v.contract_index_price.map(decimal_from_price),
            index_price: v.index_price.map(decimal_from_price),
            funding_rate: v.funding_rate.map(decimal_from_parts),
            source_observed_at_unix_nanos: v.source_observed_at.get(),
            received_at_unix_nanos: v.received_at.get(),
        }
    }
}
impl From<RustOrderBook> for MarketOrderBookCurrent {
    fn from(v: RustOrderBook) -> Self {
        Self {
            evidence: v.evidence.into(),
            provider: v.provider.to_string(),
            market_id: v.market_id.to_string(),
            instrument_id: v.instrument_id.to_string(),
            sequence: v.sequence.get(),
            source_observed_at_unix_nanos: v.source_observed_at.get(),
            received_at_unix_nanos: v.received_at.get(),
            checksum: v.checksum,
            depth_policy: v.depth_policy,
            bids: v
                .bids
                .into_iter()
                .map(|l| MarketOrderBookLevel {
                    price: decimal_from_price(l.price),
                    quantity: decimal_from_quantity(l.quantity),
                    order_count: l.order_count,
                })
                .collect(),
            asks: v
                .asks
                .into_iter()
                .map(|l| MarketOrderBookLevel {
                    price: decimal_from_price(l.price),
                    quantity: decimal_from_quantity(l.quantity),
                    order_count: l.order_count,
                })
                .collect(),
        }
    }
}
impl From<RustFreshness> for MarketFreshnessCurrent {
    fn from(v: RustFreshness) -> Self {
        Self {
            evidence: v.evidence.into(),
            provider: v.provider.to_string(),
            scope: v.scope.into(),
            data_kind: v.data_kind,
            last_event_time_unix_nanos: v.last_event_time.map(|v| v.get()),
            last_received_time_unix_nanos: v.last_received_time.map(|v| v.get()),
            age_nanos: v.age_nanos,
            event_sequence: v.event_sequence.get(),
            status: match v.status {
                MarketFreshnessStatus::Unknown => "unknown",
                MarketFreshnessStatus::WarmingUp => "warming_up",
                MarketFreshnessStatus::Current => "current",
                MarketFreshnessStatus::Stale => "stale",
                MarketFreshnessStatus::Degraded => "degraded",
                MarketFreshnessStatus::Disconnected => "disconnected",
                MarketFreshnessStatus::ResyncRequired => "resync_required",
            }
            .to_owned(),
        }
    }
}

fn decimal_from_price(value: Price) -> NativeDecimal {
    NativeDecimal {
        mantissa: value.mantissa(),
        scale: value.scale(),
    }
}

fn decimal_from_quantity(value: Quantity) -> NativeDecimal {
    NativeDecimal {
        mantissa: value.mantissa(),
        scale: value.scale(),
    }
}

fn decimal_from_parts(value: DecimalParts) -> NativeDecimal {
    NativeDecimal {
        mantissa: value.mantissa(),
        scale: value.scale(),
    }
}

#[pyfunction]
fn build_info() -> NativeBuildInfo {
    NativeBuildInfo {
        api_version: API_VERSION,
        owner: "Market".to_owned(),
        package_version: env!("CARGO_PKG_VERSION").to_owned(),
    }
}

#[pymodule]
fn _native_market_contract(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add(
        "MarketInvalidCurrentViewError",
        module.py().get_type::<MarketInvalidCurrentViewError>(),
    )?;
    module.add(
        "MarketCurrentViewUnavailableError",
        module.py().get_type::<MarketCurrentViewUnavailableError>(),
    )?;
    module.add_class::<NativeBuildInfo>()?;
    module.add_class::<NativeDecimal>()?;
    module.add_class::<MarketCurrentEvidence>()?;
    module.add_class::<MarketObservationScope>()?;
    module.add_class::<MarketQuoteCurrent>()?;
    module.add_class::<MarketBarCurrent>()?;
    module.add_class::<MarketGreeksCurrent>()?;
    module.add_class::<MarketRateCurrent>()?;
    module.add_class::<MarketTicker24hCurrent>()?;
    module.add_class::<MarketMarkPriceCurrent>()?;
    module.add_class::<MarketFundingRateCurrent>()?;
    module.add_class::<MarketOpenInterestCurrent>()?;
    module.add_class::<MarketIndexPriceCurrent>()?;
    module.add_class::<MarketOrderBookLevel>()?;
    module.add_class::<MarketOrderBookCurrent>()?;
    module.add_class::<MarketFreshnessCurrent>()?;
    module.add_class::<MarketCurrentView>()?;
    module.add_function(wrap_pyfunction!(build_info, module)?)?;
    Ok(())
}
