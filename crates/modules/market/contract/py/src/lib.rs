use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use _native_transport::direct::DirectAeronSubscription;
use _native_transport::lease::{EventLease, EventLeaseError};
use kairos_market_contract::{
    ContractError, MarketBarCurrent as RustBar, MarketBarKind, MarketCommandEnvelope,
    MarketCommandOutcome, MarketCommandStatus as RustCommandStatus, MarketControlRpcClient,
    MarketCurrentEvidence as RustEvidence, MarketDataRoute as RustDataRoute, MarketDataRouteState,
    MarketDataRoutesQuery as RustDataRoutesQuery,
    MarketDataRoutesResponse as RustDataRoutesResponse, MarketEvent as RustMarketEvent,
    MarketFeedStatus, MarketFreshnessCurrent as RustFreshness, MarketFreshnessStatus,
    MarketFundingRateCurrent as RustFunding, MarketGreeksCurrent as RustGreeks,
    MarketHealthResponse as RustHealthResponse, MarketHealthStatus,
    MarketIndexPriceCurrent as RustIndex, MarketIndexedView as RustView,
    MarketMarkPriceCurrent as RustMark, MarketObservationScope as RustScope,
    MarketOpenInterestCurrent as RustOpenInterest, MarketOperation, MarketOperatorCommandEnvelope,
    MarketOrderBookCurrent as RustOrderBook, MarketQuoteCurrent as RustQuote,
    MarketRateCurrent as RustRate, MarketReleaseOwnerPayload,
    MarketSubscribePayload as RustSubscribePayload,
    MarketSubscriptionResponse as RustSubscriptionResponse,
    MarketSubscriptionSnapshot as RustSubscriptionSnapshot, MarketSubscriptionState,
    MarketSubscriptionsQuery as RustSubscriptionsQuery,
    MarketSubscriptionsResponse as RustSubscriptionsResponse, MarketTarget as RustTarget,
    MarketTicker24hCurrent as RustTicker, MarketUnsubscribePayload, MarketViewKey as RustViewKey,
    MarketViewKind, ObservationRequirement as RustObservationRequirement,
    ProviderPreference as RustProviderPreference, SubscriptionOwnerKey, SubscriptionPendingReason,
};
use kairos_primitives::decimal::{DecimalParts, Price, PriceDelta, Quantity};
use kairos_primitives::market::{ObservationKind, Provider as RustProvider};
use kairos_primitives::reference::{InstrumentId, MarketId};
use kairos_primitives::runtime::{
    IdempotencyKey, InstanceId, InstanceIdentity, LaunchId, RequestId, StrategyId,
};
use kairos_primitives::time::UnixNanos;
use kairos_protocol::generated::kairos::market::v_2 as fb;
use kairos_protocol::{EventMetadataOwned, decode_event_metadata};
use pyo3::create_exception;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule};

create_exception!(
    _native_market_contract,
    MarketInvalidCurrentViewError,
    PyValueError
);
create_exception!(
    _native_market_contract,
    MarketInvalidEventError,
    PyValueError
);
create_exception!(
    _native_market_contract,
    MarketCurrentViewUnavailableError,
    PyRuntimeError
);
create_exception!(
    _native_market_contract,
    MarketControlUnavailableError,
    PyRuntimeError
);
create_exception!(
    _native_market_contract,
    MarketControlRejectedError,
    PyRuntimeError
);
create_exception!(
    _native_market_contract,
    MarketInvalidInputError,
    PyValueError
);

const API_VERSION: u32 = 1;

#[pyclass(name = "MarketClient", module = "kairospy._native_market_contract")]
struct NativeMarketClient {
    control_socket: PathBuf,
    view_root: Option<PathBuf>,
    workspace_id: String,
    launch_id: Option<String>,
    instance_id: Option<String>,
    aeron_dir: Option<String>,
    channel: String,
    stream_id: i32,
    timeout: f64,
}

#[pymethods]
impl NativeMarketClient {
    #[new]
    #[pyo3(signature = (control_socket, *, workspace_id, view_root=None, launch_id=None, instance_id=None, aeron_dir=None, channel=None, stream_id=None, timeout=5.0))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        control_socket: PathBuf,
        workspace_id: String,
        view_root: Option<PathBuf>,
        launch_id: Option<String>,
        instance_id: Option<String>,
        aeron_dir: Option<String>,
        channel: Option<String>,
        stream_id: Option<i32>,
        timeout: f64,
    ) -> PyResult<Self> {
        identity(workspace_id.clone(), launch_id.clone(), instance_id.clone())?;
        validate_client_facts(stream_id, timeout)?;
        Ok(Self {
            control_socket,
            view_root,
            workspace_id,
            launch_id,
            instance_id,
            aeron_dir,
            channel: channel
                .unwrap_or_else(|| kairos_market_contract::DEFAULT_AERON_CHANNEL.to_owned()),
            stream_id: stream_id.unwrap_or(kairos_market_contract::MARKET_EVENTS_STREAM_ID),
            timeout,
        })
    }

    #[getter]
    fn control(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let module = PyModule::import(py, "kairospy._native_market_contract")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("timeout", self.timeout)?;
        Ok(module
            .getattr("MarketControlClient")?
            .call((self.control_socket.clone(),), Some(&kwargs))?
            .unbind())
    }

    #[getter]
    fn current(&self, py: Python<'_>) -> PyResult<Option<Py<PyAny>>> {
        let Some(root) = &self.view_root else {
            return Ok(None);
        };
        let module = PyModule::import(py, "kairospy._native_market_contract")?;
        Ok(Some(
            module
                .getattr("MarketCurrentView")?
                .call1((
                    root.clone(),
                    self.workspace_id.clone(),
                    self.launch_id.clone(),
                    self.instance_id.clone(),
                ))?
                .unbind(),
        ))
    }

    #[getter]
    fn events(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        Ok(Py::new(
            py,
            MarketLiveSubscription::new(
                self.aeron_dir.clone(),
                Some(self.channel.clone()),
                Some(self.stream_id),
                _native_transport::DEFAULT_MAX_PAYLOAD_LEN,
            )?,
        )?
        .into_any())
    }
}

fn validate_client_facts(stream_id: Option<i32>, timeout: f64) -> PyResult<()> {
    if !timeout.is_finite() || timeout <= 0.0 {
        return Err(MarketInvalidInputError::new_err(
            "Market control timeout must be finite and positive",
        ));
    }
    if stream_id.is_some_and(|value| value <= 0) {
        return Err(MarketInvalidInputError::new_err(
            "Market event stream_id must be positive",
        ));
    }
    Ok(())
}

#[pyclass(frozen, module = "kairospy._native_market_contract")]
struct NativeBuildInfo {
    #[pyo3(get)]
    api_version: u32,
    #[pyo3(get)]
    owner: String,
    #[pyo3(get)]
    package_version: String,
    #[pyo3(get)]
    contract_fingerprint: String,
}

#[pyclass(name = "Provider", frozen, module = "kairospy._native_market_contract")]
#[derive(Clone)]
struct NativeProvider {
    value: RustProvider,
}

#[pymethods]
impl NativeProvider {
    #[classattr]
    #[pyo3(name = "MASSIVE")]
    fn massive() -> PyResult<Self> {
        Self::new(RustProvider::MASSIVE.to_owned())
    }

    #[classattr]
    #[pyo3(name = "BINANCE")]
    fn binance() -> PyResult<Self> {
        Self::new(RustProvider::BINANCE.to_owned())
    }

    #[classattr]
    #[pyo3(name = "OKX")]
    fn okx() -> PyResult<Self> {
        Self::new(RustProvider::OKX.to_owned())
    }

    #[classattr]
    #[pyo3(name = "HYPERLIQUID")]
    fn hyperliquid() -> PyResult<Self> {
        Self::new(RustProvider::HYPERLIQUID.to_owned())
    }

    #[classattr]
    #[pyo3(name = "IBKR")]
    fn ibkr() -> PyResult<Self> {
        Self::new(RustProvider::IBKR.to_owned())
    }

    #[new]
    fn new(value: String) -> PyResult<Self> {
        Ok(Self {
            value: RustProvider::new(value)
                .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
        })
    }

    #[getter]
    fn value(&self) -> &str {
        self.value.as_str()
    }

    fn __str__(&self) -> &str {
        self.value.as_str()
    }

    fn __repr__(&self) -> String {
        format!("Provider({:?})", self.value.as_str())
    }

    fn __eq__(&self, other: &Self) -> bool {
        self.value == other.value
    }
}

#[pyclass(
    name = "ObservationRequirement",
    frozen,
    module = "kairospy._native_market_contract"
)]
#[derive(Clone)]
struct NativeObservationRequirement {
    inner: RustObservationRequirement,
}

#[pymethods]
impl NativeObservationRequirement {
    #[new]
    #[pyo3(signature = (kind, qualifier=None))]
    fn new(kind: String, qualifier: Option<String>) -> PyResult<Self> {
        let kind = ObservationKind::parse_selector(&kind)
            .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?;
        let qualifier = validated_optional_text(qualifier, "observation qualifier")?;
        Ok(Self {
            inner: RustObservationRequirement { kind, qualifier },
        })
    }

    #[staticmethod]
    fn from_selector(selector: String) -> PyResult<Self> {
        let (kind, qualifier) = selector
            .split_once(':')
            .map_or((selector.as_str(), None), |(kind, value)| {
                (kind, Some(value))
            });
        Self::new(kind.to_owned(), qualifier.map(str::to_owned))
    }

    #[staticmethod]
    fn bar(timeframe: String) -> PyResult<Self> {
        Self::new("bar".to_owned(), Some(timeframe))
    }

    #[getter]
    fn kind(&self) -> &'static str {
        self.inner.kind.as_str()
    }

    #[getter]
    fn qualifier(&self) -> Option<&str> {
        self.inner.qualifier.as_deref()
    }

    #[getter]
    fn selector(&self) -> String {
        self.inner.qualifier.as_ref().map_or_else(
            || self.inner.kind.as_str().to_owned(),
            |qualifier| format!("{}:{qualifier}", self.inner.kind.as_str()),
        )
    }

    fn __eq__(&self, other: &Self) -> bool {
        self.inner == other.inner
    }
}

#[pyclass(
    name = "ProviderPreference",
    frozen,
    module = "kairospy._native_market_contract"
)]
#[derive(Clone)]
struct NativeProviderPreference {
    inner: RustProviderPreference,
}

#[pymethods]
impl NativeProviderPreference {
    #[staticmethod]
    fn automatic() -> Self {
        Self {
            inner: RustProviderPreference::Automatic,
        }
    }

    #[staticmethod]
    #[pyo3(signature = (*providers))]
    fn prefer(providers: &Bound<'_, pyo3::types::PyTuple>) -> PyResult<Self> {
        Ok(Self {
            inner: RustProviderPreference::Prefer(validated_provider_args(providers)?),
        })
    }

    #[staticmethod]
    #[pyo3(signature = (*providers))]
    fn require(providers: &Bound<'_, pyo3::types::PyTuple>) -> PyResult<Self> {
        Ok(Self {
            inner: RustProviderPreference::Require(validated_provider_args(providers)?),
        })
    }

    #[staticmethod]
    fn all_eligible() -> Self {
        Self {
            inner: RustProviderPreference::AllEligible,
        }
    }

    #[getter]
    fn mode(&self) -> &'static str {
        match self.inner {
            RustProviderPreference::Automatic => "automatic",
            RustProviderPreference::Prefer(_) => "prefer",
            RustProviderPreference::Require(_) => "require",
            RustProviderPreference::AllEligible => "all_eligible",
        }
    }

    #[getter]
    fn providers(&self) -> Vec<String> {
        match &self.inner {
            RustProviderPreference::Prefer(values) | RustProviderPreference::Require(values) => {
                values.iter().map(ToString::to_string).collect()
            },
            RustProviderPreference::Automatic | RustProviderPreference::AllEligible => Vec::new(),
        }
    }

    fn __eq__(&self, other: &Self) -> bool {
        self.inner == other.inner
    }
}

#[pyclass(
    name = "OptionRight",
    frozen,
    module = "kairospy._native_market_contract"
)]
#[derive(Clone)]
struct NativeOptionRight {
    value: String,
}

#[pymethods]
impl NativeOptionRight {
    #[classattr]
    #[pyo3(name = "CALL")]
    fn call() -> Self {
        Self {
            value: "call".to_owned(),
        }
    }

    #[classattr]
    #[pyo3(name = "PUT")]
    fn put() -> Self {
        Self {
            value: "put".to_owned(),
        }
    }

    #[classattr]
    #[pyo3(name = "BOTH")]
    fn both() -> Self {
        Self {
            value: "both".to_owned(),
        }
    }

    #[new]
    fn new(value: String) -> PyResult<Self> {
        Ok(Self {
            value: validated_option_right(Some(value))?.expect("provided option right"),
        })
    }

    #[getter]
    fn value(&self) -> &str {
        &self.value
    }

    fn __str__(&self) -> &str {
        &self.value
    }

    fn __eq__(&self, other: &Self) -> bool {
        self.value == other.value
    }
}

#[derive(Clone)]
enum ExpirySpec {
    Absolute {
        from: Option<u64>,
        to: Option<u64>,
    },
    Relative {
        from_days: Option<u32>,
        to_days: Option<u32>,
    },
}

#[pyclass(
    name = "ExpiryRange",
    frozen,
    module = "kairospy._native_market_contract"
)]
#[derive(Clone)]
struct NativeExpiryRange {
    inner: ExpirySpec,
}

#[pymethods]
impl NativeExpiryRange {
    #[new]
    #[pyo3(signature = (*, from_unix_nanos=None, to_unix_nanos=None, from_days=None, to_days=None))]
    fn new(
        from_unix_nanos: Option<u64>,
        to_unix_nanos: Option<u64>,
        from_days: Option<u32>,
        to_days: Option<u32>,
    ) -> PyResult<Self> {
        let absolute = from_unix_nanos.is_some() || to_unix_nanos.is_some();
        let relative = from_days.is_some() || to_days.is_some();
        if absolute == relative {
            return Err(MarketInvalidInputError::new_err(
                "expiry range requires exactly one absolute or relative bound family",
            ));
        }
        if absolute {
            if from_unix_nanos
                .zip(to_unix_nanos)
                .is_some_and(|(from, to)| from > to)
            {
                return Err(MarketInvalidInputError::new_err(
                    "expiry lower bound must not exceed upper bound",
                ));
            }
            Ok(Self {
                inner: ExpirySpec::Absolute {
                    from: from_unix_nanos,
                    to: to_unix_nanos,
                },
            })
        } else {
            if from_days.zip(to_days).is_some_and(|(from, to)| from > to) {
                return Err(MarketInvalidInputError::new_err(
                    "expiry lower day bound must not exceed upper day bound",
                ));
            }
            Ok(Self {
                inner: ExpirySpec::Relative { from_days, to_days },
            })
        }
    }

    #[staticmethod]
    #[pyo3(signature = (days, *, from_days=0))]
    fn next_days(days: u32, from_days: u32) -> PyResult<Self> {
        Self::new(None, None, Some(from_days), Some(days))
    }

    #[staticmethod]
    fn between_unix_nanos(
        from_unix_nanos: Option<u64>,
        to_unix_nanos: Option<u64>,
    ) -> PyResult<Self> {
        Self::new(from_unix_nanos, to_unix_nanos, None, None)
    }

    #[getter]
    fn from_unix_nanos(&self) -> Option<u64> {
        match &self.inner {
            ExpirySpec::Absolute { from, .. } => *from,
            _ => None,
        }
    }

    #[getter]
    fn to_unix_nanos(&self) -> Option<u64> {
        match &self.inner {
            ExpirySpec::Absolute { to, .. } => *to,
            _ => None,
        }
    }

    #[getter]
    fn from_days(&self) -> Option<u32> {
        match &self.inner {
            ExpirySpec::Relative { from_days, .. } => *from_days,
            _ => None,
        }
    }

    #[getter]
    fn to_days(&self) -> Option<u32> {
        match &self.inner {
            ExpirySpec::Relative { to_days, .. } => *to_days,
            _ => None,
        }
    }
}

#[derive(Clone)]
enum StrikeSpec {
    AroundSpot(Price),
    Absolute {
        lower: Option<Price>,
        upper: Option<Price>,
    },
}

#[pyclass(
    name = "StrikeRange",
    frozen,
    module = "kairospy._native_market_contract"
)]
#[derive(Clone)]
struct NativeStrikeRange {
    inner: StrikeSpec,
}

#[pymethods]
impl NativeStrikeRange {
    #[staticmethod]
    fn around_spot(percent: String) -> PyResult<Self> {
        let percent = price(&percent)?;
        if percent.mantissa() <= 0 {
            return Err(MarketInvalidInputError::new_err(
                "around_spot strike range requires a positive percent",
            ));
        }
        Ok(Self {
            inner: StrikeSpec::AroundSpot(percent),
        })
    }

    #[staticmethod]
    #[pyo3(signature = (lower=None, upper=None))]
    fn between(lower: Option<String>, upper: Option<String>) -> PyResult<Self> {
        if lower.is_none() && upper.is_none() {
            return Err(MarketInvalidInputError::new_err(
                "absolute strike range requires a bound",
            ));
        }
        let lower = lower.map(|value| price(&value)).transpose()?;
        let upper = upper.map(|value| price(&value)).transpose()?;
        if lower
            .zip(upper)
            .is_some_and(|(left, right)| decimal_cmp(left, right).is_gt())
        {
            return Err(MarketInvalidInputError::new_err(
                "strike lower bound must not exceed upper bound",
            ));
        }
        Ok(Self {
            inner: StrikeSpec::Absolute { lower, upper },
        })
    }

    #[getter]
    fn mode(&self) -> &'static str {
        match &self.inner {
            StrikeSpec::AroundSpot(_) => "around_spot",
            StrikeSpec::Absolute { .. } => "absolute",
        }
    }

    #[getter]
    fn percent(&self) -> Option<String> {
        match &self.inner {
            StrikeSpec::AroundSpot(value) => Some(decimal_text(*value)),
            _ => None,
        }
    }

    #[getter]
    fn lower(&self) -> Option<String> {
        match &self.inner {
            StrikeSpec::Absolute { lower, .. } => lower.map(decimal_text),
            _ => None,
        }
    }

    #[getter]
    fn upper(&self) -> Option<String> {
        match &self.inner {
            StrikeSpec::Absolute { upper, .. } => upper.map(decimal_text),
            _ => None,
        }
    }
}

#[pyclass(
    name = "OptionFilter",
    frozen,
    module = "kairospy._native_market_contract"
)]
#[derive(Clone)]
struct NativeOptionFilter {
    expiry: Option<NativeExpiryRange>,
    strike: Option<NativeStrikeRange>,
    right: String,
    limit: Option<u32>,
}

#[pymethods]
impl NativeOptionFilter {
    #[new]
    #[pyo3(signature = (expiry=None, strike=None, right=None, limit=None))]
    fn new(
        expiry: Option<PyRef<'_, NativeExpiryRange>>,
        strike: Option<PyRef<'_, NativeStrikeRange>>,
        right: Option<&Bound<'_, PyAny>>,
        limit: Option<u32>,
    ) -> PyResult<Self> {
        if limit == Some(0) {
            return Err(MarketInvalidInputError::new_err(
                "option filter limit must be positive",
            ));
        }
        let right = option_right_arg(right)?.unwrap_or_else(|| "both".to_owned());
        Ok(Self {
            expiry: expiry.map(|value| value.clone()),
            strike: strike.map(|value| value.clone()),
            right,
            limit,
        })
    }

    #[getter]
    fn expiry(&self) -> Option<NativeExpiryRange> {
        self.expiry.clone()
    }
    #[getter]
    fn strike(&self) -> Option<NativeStrikeRange> {
        self.strike.clone()
    }
    #[getter]
    fn right(&self) -> &str {
        &self.right
    }
    #[getter]
    fn limit(&self) -> Option<u32> {
        self.limit
    }
}

#[pyclass(name = "Options", frozen, module = "kairospy._native_market_contract")]
#[derive(Clone)]
struct NativeOptions {
    underlying: String,
    filter: NativeOptionFilter,
}

#[pymethods]
impl NativeOptions {
    #[new]
    #[pyo3(signature = (underlying, filter=None))]
    fn new(
        underlying: &Bound<'_, PyAny>,
        filter: Option<PyRef<'_, NativeOptionFilter>>,
    ) -> PyResult<Self> {
        Ok(Self {
            underlying: canonical_underlying(underlying)?,
            filter: filter.map_or_else(
                || NativeOptionFilter {
                    expiry: None,
                    strike: None,
                    right: "both".to_owned(),
                    limit: None,
                },
                |value| value.clone(),
            ),
        })
    }

    #[staticmethod]
    fn on(underlying: &Bound<'_, PyAny>) -> PyResult<Self> {
        Self::new(underlying, None)
    }

    #[getter]
    fn underlying(&self) -> &str {
        &self.underlying
    }
    #[getter]
    fn filter(&self) -> NativeOptionFilter {
        self.filter.clone()
    }

    #[pyo3(signature = (*, filter=None, expiry=None, strike=None, right=None, limit=None))]
    fn r#where(
        &self,
        filter: Option<PyRef<'_, NativeOptionFilter>>,
        expiry: Option<PyRef<'_, NativeExpiryRange>>,
        strike: Option<PyRef<'_, NativeStrikeRange>>,
        right: Option<&Bound<'_, PyAny>>,
        limit: Option<u32>,
    ) -> PyResult<Self> {
        let has_fields = expiry.is_some() || strike.is_some() || right.is_some() || limit.is_some();
        if filter.is_some() && has_fields {
            return Err(MarketInvalidInputError::new_err(
                "pass either filter or filter fields, not both",
            ));
        }
        let filter = match filter {
            Some(value) => value.clone(),
            None => NativeOptionFilter::new(expiry, strike, right, limit)?,
        };
        Ok(Self {
            underlying: self.underlying.clone(),
            filter,
        })
    }

    #[pyo3(signature = (*, spot=None, now_unix_nanos=None))]
    fn to_target(
        &self,
        spot: Option<String>,
        now_unix_nanos: Option<u64>,
    ) -> PyResult<NativeMarketTarget> {
        let (expiry_from, expiry_to) = expiry_bounds(self.filter.expiry.as_ref(), now_unix_nanos)?;
        let (strike_lower, strike_upper) =
            strike_bounds(self.filter.strike.as_ref(), spot.as_deref())?;
        let (underlying_market_id, underlying_instrument_id) =
            if self.underlying.starts_with("market:") {
                (Some(self.underlying.clone()), None)
            } else {
                (None, Some(self.underlying.clone()))
            };
        NativeMarketTarget::options(
            underlying_market_id,
            underlying_instrument_id,
            expiry_from,
            expiry_to,
            strike_lower,
            strike_upper,
            Some(self.filter.right.clone()),
            self.filter.limit,
            false,
        )
    }
}

#[pyclass(
    name = "MarketTarget",
    frozen,
    module = "kairospy._native_market_contract"
)]
#[derive(Clone)]
struct NativeMarketTarget {
    inner: RustTarget,
}

#[pymethods]
impl NativeMarketTarget {
    #[staticmethod]
    fn market(market_id: String) -> PyResult<Self> {
        Ok(Self {
            inner: RustTarget::Market {
                market_id: MarketId::new(market_id)
                    .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
            },
        })
    }

    #[staticmethod]
    #[pyo3(signature = (instrument_id, network_id=None))]
    fn consolidated_instrument(
        instrument_id: String,
        network_id: Option<String>,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: RustTarget::ConsolidatedInstrument {
                instrument_id: InstrumentId::new(instrument_id)
                    .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
                network_id: validated_optional_text(network_id, "network_id")?,
            },
        })
    }

    #[staticmethod]
    #[pyo3(signature = (*, underlying_market_id=None, underlying_instrument_id=None, expiry_from_unix_nanos=None, expiry_to_unix_nanos=None, strike_lower=None, strike_upper=None, option_right=None, limit=None, progressive=false))]
    #[allow(clippy::too_many_arguments)]
    fn options(
        underlying_market_id: Option<String>,
        underlying_instrument_id: Option<String>,
        expiry_from_unix_nanos: Option<u64>,
        expiry_to_unix_nanos: Option<u64>,
        strike_lower: Option<String>,
        strike_upper: Option<String>,
        option_right: Option<String>,
        limit: Option<u32>,
        progressive: bool,
    ) -> PyResult<Self> {
        if underlying_market_id.is_some() == underlying_instrument_id.is_some() {
            return Err(MarketInvalidInputError::new_err(
                "options target requires exactly one underlying identity",
            ));
        }
        if expiry_from_unix_nanos
            .zip(expiry_to_unix_nanos)
            .is_some_and(|(lower, upper)| lower > upper)
        {
            return Err(MarketInvalidInputError::new_err(
                "expiry lower bound must not exceed upper bound",
            ));
        }
        if limit == Some(0) {
            return Err(MarketInvalidInputError::new_err(
                "option filter limit must be positive",
            ));
        }
        let strike_lower = strike_lower.map(|value| price(&value)).transpose()?;
        let strike_upper = strike_upper.map(|value| price(&value)).transpose()?;
        if strike_lower
            .zip(strike_upper)
            .is_some_and(|(lower, upper)| decimal_cmp(lower, upper).is_gt())
        {
            return Err(MarketInvalidInputError::new_err(
                "strike lower bound must not exceed upper bound",
            ));
        }
        Ok(Self {
            inner: RustTarget::Options {
                underlying_market_id: underlying_market_id
                    .map(MarketId::new)
                    .transpose()
                    .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
                underlying_instrument_id: underlying_instrument_id
                    .map(InstrumentId::new)
                    .transpose()
                    .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
                expiry_from_unix_nanos: expiry_from_unix_nanos.map(UnixNanos::new),
                expiry_to_unix_nanos: expiry_to_unix_nanos.map(UnixNanos::new),
                strike_lower,
                strike_upper,
                option_right: validated_option_right(option_right)?,
                limit,
                progressive,
            },
        })
    }

    #[getter]
    fn kind(&self) -> &'static str {
        match self.inner {
            RustTarget::Market { .. } => "market",
            RustTarget::ConsolidatedInstrument { .. } => "consolidated_instrument",
            RustTarget::Options { .. } => "options",
        }
    }

    #[getter]
    fn market_id(&self) -> Option<&str> {
        match &self.inner {
            RustTarget::Market { market_id } => Some(market_id.as_str()),
            _ => None,
        }
    }

    #[getter]
    fn instrument_id(&self) -> Option<&str> {
        match &self.inner {
            RustTarget::ConsolidatedInstrument { instrument_id, .. } => {
                Some(instrument_id.as_str())
            },
            _ => None,
        }
    }

    #[getter]
    fn network_id(&self) -> Option<&str> {
        match &self.inner {
            RustTarget::ConsolidatedInstrument { network_id, .. } => network_id.as_deref(),
            _ => None,
        }
    }

    #[getter]
    fn underlying_market_id(&self) -> Option<&str> {
        match &self.inner {
            RustTarget::Options {
                underlying_market_id,
                ..
            } => underlying_market_id.as_ref().map(|value| value.as_str()),
            _ => None,
        }
    }

    #[getter]
    fn underlying_instrument_id(&self) -> Option<&str> {
        match &self.inner {
            RustTarget::Options {
                underlying_instrument_id,
                ..
            } => underlying_instrument_id
                .as_ref()
                .map(|value| value.as_str()),
            _ => None,
        }
    }

    #[getter]
    fn expiry_from_unix_nanos(&self) -> Option<u64> {
        match &self.inner {
            RustTarget::Options {
                expiry_from_unix_nanos,
                ..
            } => expiry_from_unix_nanos.map(|value| value.get()),
            _ => None,
        }
    }

    #[getter]
    fn expiry_to_unix_nanos(&self) -> Option<u64> {
        match &self.inner {
            RustTarget::Options {
                expiry_to_unix_nanos,
                ..
            } => expiry_to_unix_nanos.map(|value| value.get()),
            _ => None,
        }
    }

    #[getter]
    fn strike_lower(&self) -> Option<String> {
        match &self.inner {
            RustTarget::Options { strike_lower, .. } => strike_lower.map(decimal_text),
            _ => None,
        }
    }

    #[getter]
    fn strike_upper(&self) -> Option<String> {
        match &self.inner {
            RustTarget::Options { strike_upper, .. } => strike_upper.map(decimal_text),
            _ => None,
        }
    }

    #[getter]
    fn option_right(&self) -> Option<&str> {
        match &self.inner {
            RustTarget::Options { option_right, .. } => option_right.as_deref(),
            _ => None,
        }
    }

    #[getter]
    fn limit(&self) -> Option<u32> {
        match &self.inner {
            RustTarget::Options { limit, .. } => *limit,
            _ => None,
        }
    }

    #[getter]
    fn progressive(&self) -> bool {
        match &self.inner {
            RustTarget::Options { progressive, .. } => *progressive,
            _ => false,
        }
    }

    fn __eq__(&self, other: &Self) -> bool {
        self.inner == other.inner
    }
}

#[pyclass(
    name = "MarketSubscriptionRequest",
    frozen,
    module = "kairospy._native_market_contract"
)]
#[derive(Clone)]
struct NativeMarketSubscriptionRequest {
    inner: RustSubscribePayload,
}

#[pymethods]
impl NativeMarketSubscriptionRequest {
    #[new]
    #[pyo3(signature = (target, observations, provider_preference=None))]
    fn new(
        target: PyRef<'_, NativeMarketTarget>,
        observations: Vec<PyRef<'_, NativeObservationRequirement>>,
        provider_preference: Option<PyRef<'_, NativeProviderPreference>>,
    ) -> PyResult<Self> {
        if observations.is_empty() {
            return Err(MarketInvalidInputError::new_err(
                "subscription observations are required",
            ));
        }
        Ok(Self {
            inner: RustSubscribePayload {
                target: target.inner.clone(),
                observations: observations
                    .into_iter()
                    .map(|value| value.inner.clone())
                    .collect(),
                provider_preference: provider_preference
                    .map_or(RustProviderPreference::Automatic, |value| {
                        value.inner.clone()
                    }),
            },
        })
    }

    #[getter]
    fn observation_selectors(&self) -> Vec<String> {
        self.inner
            .observations
            .iter()
            .map(|value| {
                value.qualifier.as_ref().map_or_else(
                    || value.kind.as_str().to_owned(),
                    |qualifier| format!("{}:{qualifier}", value.kind.as_str()),
                )
            })
            .collect()
    }

    #[getter]
    fn target(&self) -> NativeMarketTarget {
        NativeMarketTarget {
            inner: self.inner.target.clone(),
        }
    }

    #[getter]
    fn observations(&self) -> Vec<NativeObservationRequirement> {
        self.inner
            .observations
            .iter()
            .cloned()
            .map(|inner| NativeObservationRequirement { inner })
            .collect()
    }

    #[getter]
    fn provider_preference(&self) -> NativeProviderPreference {
        NativeProviderPreference {
            inner: self.inner.provider_preference.clone(),
        }
    }

    fn __eq__(&self, other: &Self) -> bool {
        self.inner == other.inner
    }
}

#[pyclass(
    name = "MarketHealthResponse",
    frozen,
    module = "kairospy._native_market_contract"
)]
struct NativeMarketHealthResponse {
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    actor_id: String,
    #[pyo3(get)]
    event_sequence: u64,
    #[pyo3(get)]
    feed_status: String,
    #[pyo3(get)]
    current_view_commit_count: u64,
    #[pyo3(get)]
    current_view_input_update_count: u64,
    #[pyo3(get)]
    current_view_encoded_update_count: u64,
    #[pyo3(get)]
    current_view_order_book_encode_count: u64,
    #[pyo3(get)]
    last_current_view_commit_latency_nanos: u64,
    #[pyo3(get)]
    notification_attempt_count: u64,
    #[pyo3(get)]
    notification_failure_count: u64,
}

#[pyclass(
    name = "MarketDataRoute",
    frozen,
    module = "kairospy._native_market_contract"
)]
#[derive(Clone)]
struct NativeMarketDataRoute {
    #[pyo3(get)]
    market_id: String,
    #[pyo3(get)]
    provider: String,
    #[pyo3(get)]
    observation_kinds: Vec<String>,
    #[pyo3(get)]
    state: String,
    #[pyo3(get)]
    selected: bool,
    #[pyo3(get)]
    pending_reason: Option<String>,
}

#[pyclass(
    name = "MarketDataRoutesResponse",
    frozen,
    module = "kairospy._native_market_contract"
)]
struct NativeMarketDataRoutesResponse {
    #[pyo3(get)]
    routes: Vec<NativeMarketDataRoute>,
}

#[pyclass(
    name = "MarketSubscriptionResponse",
    frozen,
    module = "kairospy._native_market_contract"
)]
struct NativeMarketSubscriptionResponse {
    #[pyo3(get)]
    subscription_id: String,
    #[pyo3(get)]
    owner_id: String,
    #[pyo3(get)]
    state: String,
    #[pyo3(get)]
    satisfied_selectors: Vec<String>,
    #[pyo3(get)]
    missing_selectors: Vec<String>,
    #[pyo3(get)]
    resolved_providers: Vec<String>,
    #[pyo3(get)]
    pending_reason: Option<String>,
}

#[pyclass(
    name = "MarketSubscriptionSnapshot",
    frozen,
    module = "kairospy._native_market_contract"
)]
#[derive(Clone)]
struct NativeMarketSubscriptionSnapshot {
    #[pyo3(get)]
    subscription_id: String,
    #[pyo3(get)]
    owner_id: String,
    #[pyo3(get)]
    state: String,
    #[pyo3(get)]
    market_ids: Vec<String>,
    #[pyo3(get)]
    observations: Vec<String>,
    #[pyo3(get)]
    selected_providers: Vec<String>,
    #[pyo3(get)]
    pending_reason: Option<String>,
}

#[pyclass(
    name = "MarketSubscriptionsResponse",
    frozen,
    module = "kairospy._native_market_contract"
)]
struct NativeMarketSubscriptionsResponse {
    #[pyo3(get)]
    subscriptions: Vec<NativeMarketSubscriptionSnapshot>,
}

#[pyclass(
    name = "MarketCommandStatus",
    frozen,
    module = "kairospy._native_market_contract"
)]
struct NativeMarketCommandStatus {
    #[pyo3(get)]
    status: String,
}

#[pyclass(
    name = "MarketReleaseOwnerResponse",
    frozen,
    module = "kairospy._native_market_contract"
)]
struct NativeMarketReleaseOwnerResponse {
    #[pyo3(get)]
    released_subscription_ids: Vec<String>,
}

#[pyclass(
    name = "MarketControlClient",
    module = "kairospy._native_market_contract"
)]
struct NativeMarketControlClient {
    client: kairos_protocol::ContractClient,
    timeout: Duration,
}

#[pymethods]
impl NativeMarketControlClient {
    #[getter]
    fn socket_path(&self) -> PathBuf {
        self.client.control_socket_path().to_path_buf()
    }

    #[new]
    #[pyo3(signature = (socket_path, *, timeout=5.0))]
    fn new(socket_path: PathBuf, timeout: f64) -> PyResult<Self> {
        if !timeout.is_finite() || timeout <= 0.0 {
            return Err(MarketInvalidInputError::new_err(
                "Market control timeout must be finite and positive",
            ));
        }
        Ok(Self {
            client: kairos_protocol::ContractClient::control_only(socket_path),
            timeout: Duration::from_secs_f64(timeout),
        })
    }

    fn health(&self, py: Python<'_>) -> PyResult<NativeMarketHealthResponse> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let value = py
            .detach(move || run_control(timeout, async move { client.control().health().await }))?;
        Ok(project_health(value))
    }

    #[pyo3(signature = (*, market_id=None, instrument_id=None, observation_kind=None, provider=None, configured_only=false, ready_only=false))]
    #[allow(clippy::too_many_arguments)]
    fn data_routes(
        &self,
        py: Python<'_>,
        market_id: Option<String>,
        instrument_id: Option<String>,
        observation_kind: Option<String>,
        provider: Option<String>,
        configured_only: bool,
        ready_only: bool,
    ) -> PyResult<NativeMarketDataRoutesResponse> {
        let query = RustDataRoutesQuery {
            market_id: market_id
                .map(MarketId::new)
                .transpose()
                .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
            instrument_id: instrument_id
                .map(InstrumentId::new)
                .transpose()
                .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
            observation_kind: observation_kind
                .map(|value| ObservationKind::parse_selector(&value))
                .transpose()
                .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
            provider: provider
                .map(RustProvider::new)
                .transpose()
                .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
            configured_only,
            ready_only,
        };
        let client = self.client.clone();
        let timeout = self.timeout;
        let value = py.detach(move || {
            run_control(
                timeout,
                async move { client.control().data_routes(query).await },
            )
        })?;
        Ok(project_routes(value))
    }

    #[pyo3(signature = (*, owner_id=None, market_id=None, state=None))]
    fn subscriptions(
        &self,
        py: Python<'_>,
        owner_id: Option<String>,
        market_id: Option<String>,
        state: Option<String>,
    ) -> PyResult<NativeMarketSubscriptionsResponse> {
        let query = RustSubscriptionsQuery {
            owner_id: owner_id
                .map(SubscriptionOwnerKey::new)
                .transpose()
                .map_err(MarketInvalidInputError::new_err)?,
            market_id: market_id
                .map(MarketId::new)
                .transpose()
                .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
            state: state
                .map(|value| parse_subscription_state(&value))
                .transpose()?,
        };
        let client = self.client.clone();
        let timeout = self.timeout;
        let value = py.detach(move || {
            run_control(timeout, async move {
                client.control().subscriptions(query).await
            })
        })?;
        Ok(project_subscriptions(value))
    }

    #[pyo3(signature = (request, *, strategy_id, instance_id, request_id, launch_id=None))]
    fn subscribe(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeMarketSubscriptionRequest>,
        strategy_id: String,
        instance_id: String,
        request_id: String,
        launch_id: Option<String>,
    ) -> PyResult<NativeMarketSubscriptionResponse> {
        let command = market_command(
            MarketOperation::Subscribe,
            request.inner.clone(),
            strategy_id,
            instance_id,
            request_id,
            launch_id,
        )?;
        let client = self.client.clone();
        let timeout = self.timeout;
        let value = py.detach(move || {
            run_control(
                timeout,
                async move { client.control().subscribe(command).await },
            )
        })?;
        Ok(project_subscription(value))
    }

    #[pyo3(signature = (request, *, owner_id, request_id))]
    fn operator_subscribe(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeMarketSubscriptionRequest>,
        owner_id: String,
        request_id: String,
    ) -> PyResult<NativeMarketSubscriptionResponse> {
        let command = operator_command(
            MarketOperation::Subscribe,
            request.inner.clone(),
            owner_id,
            request_id,
        )?;
        let client = self.client.clone();
        let timeout = self.timeout;
        let value = py.detach(move || {
            run_control(timeout, async move {
                client.control().operator_subscribe(command).await
            })
        })?;
        Ok(project_subscription(value))
    }

    #[pyo3(signature = (subscription_id, *, strategy_id, instance_id, request_id, launch_id=None))]
    fn unsubscribe(
        &self,
        py: Python<'_>,
        subscription_id: String,
        strategy_id: String,
        instance_id: String,
        request_id: String,
        launch_id: Option<String>,
    ) -> PyResult<NativeMarketCommandStatus> {
        let payload = MarketUnsubscribePayload {
            subscription_id: kairos_primitives::market::SubscriptionId::new(subscription_id)
                .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
        };
        let command = market_command(
            MarketOperation::Unsubscribe,
            payload,
            strategy_id,
            instance_id,
            request_id,
            launch_id,
        )?;
        let client = self.client.clone();
        let timeout = self.timeout;
        let value = py.detach(move || {
            run_control(timeout, async move {
                client.control().unsubscribe(command).await
            })
        })?;
        Ok(project_command_status(value))
    }

    #[pyo3(signature = (subscription_id, *, owner_id, request_id))]
    fn operator_unsubscribe(
        &self,
        py: Python<'_>,
        subscription_id: String,
        owner_id: String,
        request_id: String,
    ) -> PyResult<NativeMarketCommandStatus> {
        let payload = MarketUnsubscribePayload {
            subscription_id: kairos_primitives::market::SubscriptionId::new(subscription_id)
                .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
        };
        let command =
            operator_command(MarketOperation::Unsubscribe, payload, owner_id, request_id)?;
        let client = self.client.clone();
        let timeout = self.timeout;
        let value = py.detach(move || {
            run_control(timeout, async move {
                client.control().operator_unsubscribe(command).await
            })
        })?;
        Ok(project_command_status(value))
    }

    #[pyo3(signature = (*, strategy_id, instance_id, request_id, launch_id=None))]
    fn release_owner(
        &self,
        py: Python<'_>,
        strategy_id: String,
        instance_id: String,
        request_id: String,
        launch_id: Option<String>,
    ) -> PyResult<NativeMarketReleaseOwnerResponse> {
        let command = market_command(
            MarketOperation::ReleaseOwner,
            MarketReleaseOwnerPayload::default(),
            strategy_id,
            instance_id,
            request_id,
            launch_id,
        )?;
        let client = self.client.clone();
        let timeout = self.timeout;
        let value = py.detach(move || {
            run_control(timeout, async move {
                client.control().release_owner(command).await
            })
        })?;
        Ok(NativeMarketReleaseOwnerResponse {
            released_subscription_ids: value
                .released_subscriptions
                .into_iter()
                .map(|value| value.to_string())
                .collect(),
        })
    }

    #[pyo3(signature = (*, owner_id, request_id))]
    fn operator_release_owner(
        &self,
        py: Python<'_>,
        owner_id: String,
        request_id: String,
    ) -> PyResult<NativeMarketReleaseOwnerResponse> {
        let command = operator_command(
            MarketOperation::ReleaseOwner,
            MarketReleaseOwnerPayload::default(),
            owner_id,
            request_id,
        )?;
        let client = self.client.clone();
        let timeout = self.timeout;
        let value = py.detach(move || {
            run_control(timeout, async move {
                client.control().operator_release_owner(command).await
            })
        })?;
        Ok(NativeMarketReleaseOwnerResponse {
            released_subscription_ids: value
                .released_subscriptions
                .into_iter()
                .map(|value| value.to_string())
                .collect(),
        })
    }

    fn recover(&self, py: Python<'_>) -> PyResult<NativeMarketCommandStatus> {
        self.simple_command(py, "recover")
    }

    fn pause_replay(&self, py: Python<'_>) -> PyResult<NativeMarketCommandStatus> {
        self.simple_command(py, "pause_replay")
    }

    fn resume_replay(&self, py: Python<'_>) -> PyResult<NativeMarketCommandStatus> {
        self.simple_command(py, "resume_replay")
    }
}

impl NativeMarketControlClient {
    fn simple_command(
        &self,
        py: Python<'_>,
        operation: &'static str,
    ) -> PyResult<NativeMarketCommandStatus> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let value = py.detach(move || {
            run_control(timeout, async move {
                match operation {
                    "recover" => client.control().recover().await,
                    "pause_replay" => client.control().pause_replay().await,
                    "resume_replay" => client.control().resume_replay().await,
                    _ => unreachable!("static Market operation"),
                }
            })
        })?;
        Ok(project_command_status(value))
    }
}

#[pyclass(frozen, module = "kairospy._native_market_contract")]
#[derive(Clone)]
struct MarketEventMetadata {
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

#[pyclass(frozen, module = "kairospy._native_market_contract")]
struct MarketTradeEventPayload {
    #[pyo3(get)]
    trade_id: Option<String>,
    #[pyo3(get)]
    scope: MarketObservationScope,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    provider: String,
    #[pyo3(get)]
    price: NativeDecimal,
    #[pyo3(get)]
    quantity: NativeDecimal,
    #[pyo3(get)]
    aggressor_side: Option<String>,
    #[pyo3(get)]
    venue_code: Option<String>,
    #[pyo3(get)]
    tape: Option<u32>,
    #[pyo3(get)]
    trf_id: Option<u32>,
    #[pyo3(get)]
    participant_timestamp_unix_nanos: Option<u64>,
    #[pyo3(get)]
    trf_timestamp_unix_nanos: Option<u64>,
    #[pyo3(get)]
    source_observed_at_unix_nanos: u64,
    #[pyo3(get)]
    received_at_unix_nanos: u64,
}

#[pyclass(frozen, module = "kairospy._native_market_contract")]
#[derive(Clone)]
struct MarketOrderBookChange {
    #[pyo3(get)]
    side: String,
    #[pyo3(get)]
    action: String,
    #[pyo3(get)]
    price: NativeDecimal,
    #[pyo3(get)]
    quantity: Option<NativeDecimal>,
    #[pyo3(get)]
    order_count: u32,
}

#[pyclass(frozen, module = "kairospy._native_market_contract")]
struct MarketOrderBookDeltaEventPayload {
    #[pyo3(get)]
    provider: String,
    #[pyo3(get)]
    market_id: String,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    first_sequence: u64,
    #[pyo3(get)]
    last_sequence: u64,
    #[pyo3(get)]
    source_observed_at_unix_nanos: u64,
    #[pyo3(get)]
    received_at_unix_nanos: u64,
    #[pyo3(get)]
    checksum: Option<String>,
    #[pyo3(get)]
    changes: Vec<MarketOrderBookChange>,
}

#[pyclass(frozen, module = "kairospy._native_market_contract")]
struct MarketOrderBookResyncEventPayload {
    #[pyo3(get)]
    provider: String,
    #[pyo3(get)]
    market_id: String,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    expected_sequence: u64,
    #[pyo3(get)]
    observed_sequence: u64,
    #[pyo3(get)]
    reason: String,
}

#[pyclass(frozen, module = "kairospy._native_market_contract")]
struct MarketEvent {
    #[pyo3(get)]
    metadata: MarketEventMetadata,
    #[pyo3(get)]
    kind: String,
    data: Py<PyAny>,
}

/// Callback-scoped Market event backed by the current Aeron fragment.
#[pyclass(
    name = "MarketLiveEventView",
    frozen,
    module = "kairospy._native_market_contract"
)]
struct MarketLiveEventView {
    lease: Arc<EventLease>,
}

#[pymethods]
impl MarketLiveEventView {
    #[getter]
    fn kind(&self) -> PyResult<String> {
        self.with_event(|event| event.kind().as_str().to_owned())
    }

    #[getter]
    fn metadata(&self) -> PyResult<MarketEventMetadata> {
        self.with_event(|event| match event {
            RustMarketEvent::QuoteUpdated(root) => market_event_metadata(root.metadata()),
            RustMarketEvent::TradeOccurred(root) => market_event_metadata(root.metadata()),
            RustMarketEvent::BarCompleted(root) => market_event_metadata(root.metadata()),
            RustMarketEvent::GreeksUpdated(root) => market_event_metadata(root.metadata()),
            RustMarketEvent::RateUpdated(root) => market_event_metadata(root.metadata()),
            RustMarketEvent::Ticker24hUpdated(root) => market_event_metadata(root.metadata()),
            RustMarketEvent::MarkPriceUpdated(root) => market_event_metadata(root.metadata()),
            RustMarketEvent::FundingRateUpdated(root) => market_event_metadata(root.metadata()),
            RustMarketEvent::OpenInterestUpdated(root) => market_event_metadata(root.metadata()),
            RustMarketEvent::IndexPriceUpdated(root) => market_event_metadata(root.metadata()),
            RustMarketEvent::OrderBookSnapshotReceived(root) => {
                market_event_metadata(root.metadata())
            },
            RustMarketEvent::OrderBookDeltaReceived(root) => market_event_metadata(root.metadata()),
            RustMarketEvent::OrderBookResyncRequired(root) => {
                market_event_metadata(root.metadata())
            },
        })?
    }

    #[getter]
    fn data(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.with_event(|event| project_market_event(py, event).map(|value| value.data))?
    }

    fn __repr__(&self) -> PyResult<String> {
        Ok(format!("MarketLiveEventView(kind={:?})", self.kind()?))
    }
}

impl MarketLiveEventView {
    fn with_event<R>(
        &self,
        read: impl for<'frame> FnOnce(RustMarketEvent<'frame>) -> R,
    ) -> PyResult<R> {
        self.lease
            .with_frame(|frame| {
                kairos_market_contract::decode_event(frame)
                    .map(read)
                    .map_err(|error| MarketInvalidEventError::new_err(error.to_string()))
            })
            .map_err(market_lease_error)?
    }
}

#[pyclass(
    name = "MarketLiveSubscription",
    module = "kairospy._native_market_contract",
    unsendable
)]
struct MarketLiveSubscription {
    inner: DirectAeronSubscription,
}

#[pymethods]
impl MarketLiveSubscription {
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
                    .unwrap_or(kairos_market_contract::DEFAULT_AERON_CHANNEL),
                stream_id.unwrap_or(kairos_market_contract::MARKET_EVENTS_STREAM_ID),
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
                .with_frame(|frame| kairos_market_contract::decode_event(frame).map(|_| ()))
                .map_err(market_lease_error)?
                .map_err(|error| MarketInvalidEventError::new_err(error.to_string()))?;
            let event = Py::new(py, MarketLiveEventView { lease })?;
            visitor.call1(py, (event,))?;
            Ok(())
        })
    }

    fn close(&self) -> PyResult<()> {
        self.inner.close()
    }
}

fn market_lease_error(error: EventLeaseError) -> PyErr {
    PyRuntimeError::new_err(error.to_string())
}

#[pyclass(
    name = "MarketViewKind",
    frozen,
    module = "kairospy._native_market_contract"
)]
#[derive(Clone)]
struct NativeMarketViewKind {
    value: String,
}

#[pymethods]
impl NativeMarketViewKind {
    #[classattr]
    #[pyo3(name = "QUOTE")]
    fn quote() -> Self {
        Self {
            value: "quote".to_owned(),
        }
    }
    #[classattr]
    #[pyo3(name = "BAR")]
    fn bar() -> Self {
        Self {
            value: "bar".to_owned(),
        }
    }
    #[classattr]
    #[pyo3(name = "GREEKS")]
    fn greeks() -> Self {
        Self {
            value: "greeks".to_owned(),
        }
    }
    #[classattr]
    #[pyo3(name = "RATE")]
    fn rate() -> Self {
        Self {
            value: "rate".to_owned(),
        }
    }
    #[classattr]
    #[pyo3(name = "TICKER_24H")]
    fn ticker_24h() -> Self {
        Self {
            value: "ticker-24h".to_owned(),
        }
    }
    #[classattr]
    #[pyo3(name = "MARK_PRICE")]
    fn mark_price() -> Self {
        Self {
            value: "mark-price".to_owned(),
        }
    }
    #[classattr]
    #[pyo3(name = "FUNDING_RATE")]
    fn funding_rate() -> Self {
        Self {
            value: "funding-rate".to_owned(),
        }
    }
    #[classattr]
    #[pyo3(name = "OPEN_INTEREST")]
    fn open_interest() -> Self {
        Self {
            value: "open-interest".to_owned(),
        }
    }
    #[classattr]
    #[pyo3(name = "INDEX_PRICE")]
    fn index_price() -> Self {
        Self {
            value: "index-price".to_owned(),
        }
    }
    #[classattr]
    #[pyo3(name = "ORDER_BOOK")]
    fn order_book() -> Self {
        Self {
            value: "order-book".to_owned(),
        }
    }
    #[classattr]
    #[pyo3(name = "FRESHNESS")]
    fn freshness() -> Self {
        Self {
            value: "freshness".to_owned(),
        }
    }

    #[new]
    fn new(value: String) -> PyResult<Self> {
        let value = market_view_kind(&value)?.as_str().to_owned();
        Ok(Self { value })
    }

    #[getter]
    fn value(&self) -> &str {
        &self.value
    }
    fn __str__(&self) -> &str {
        &self.value
    }
    fn __eq__(&self, other: &Self) -> bool {
        self.value == other.value
    }
}

#[pyclass(
    name = "MarketViewKey",
    frozen,
    module = "kairospy._native_market_contract"
)]
#[derive(Clone)]
struct NativeMarketViewKey {
    #[pyo3(get)]
    scope_key: String,
    #[pyo3(get)]
    provider: String,
    #[pyo3(get)]
    kind: String,
    #[pyo3(get)]
    qualifier: Option<String>,
}

#[pymethods]
impl NativeMarketViewKey {
    #[new]
    #[pyo3(signature = (scope_key, provider, kind, qualifier=None))]
    fn new(
        scope_key: String,
        provider: String,
        kind: &Bound<'_, PyAny>,
        qualifier: Option<String>,
    ) -> PyResult<Self> {
        let kind = if let Ok(value) = kind.extract::<PyRef<'_, NativeMarketViewKind>>() {
            value.value.clone()
        } else {
            kind.extract::<String>().map_err(|_| {
                MarketInvalidInputError::new_err(
                    "Market view kind must be a string or MarketViewKind",
                )
            })?
        };
        let parsed = market_view_kind(&kind)?;
        let key =
            RustViewKey::new(scope_key, provider, parsed, qualifier).map_err(contract_error)?;
        Ok(Self {
            scope_key: key.scope_key,
            provider: key.provider.to_string(),
            kind: key.kind.as_str().to_owned(),
            qualifier: key.qualifier,
        })
    }

    fn canonical_key(&self) -> String {
        format!(
            "scope={};provider={};view={};qualifier={}",
            self.scope_key,
            self.provider,
            self.kind,
            self.qualifier.as_deref().unwrap_or("")
        )
    }
}

#[pymethods]
impl MarketEvent {
    #[staticmethod]
    #[pyo3(signature = (*, sequence, market_id, instrument_id, provider, bar_spec_id, open, high, low, close, volume=None, occurred_at_unix_nanos, launch_id=None, instance_id=None, producer_incarnation=1))]
    #[allow(clippy::too_many_arguments)]
    fn simulation_bar(
        py: Python<'_>,
        sequence: u64,
        market_id: String,
        instrument_id: String,
        provider: String,
        bar_spec_id: String,
        open: &Bound<'_, PyAny>,
        high: &Bound<'_, PyAny>,
        low: &Bound<'_, PyAny>,
        close: &Bound<'_, PyAny>,
        volume: Option<Bound<'_, PyAny>>,
        occurred_at_unix_nanos: u64,
        launch_id: Option<String>,
        instance_id: Option<String>,
        producer_incarnation: u64,
    ) -> PyResult<Self> {
        let (metadata, scope, instrument_id, provider) = simulation_identity(
            sequence,
            market_id,
            instrument_id,
            provider,
            occurred_at_unix_nanos,
            launch_id,
            instance_id,
            producer_incarnation,
        )?;
        let bar_spec_id = validated_required_text(bar_spec_id, "bar_spec_id")?;
        let payload = MarketBarCurrent {
            evidence: simulation_evidence(sequence, occurred_at_unix_nanos),
            scope,
            instrument_id,
            provider,
            bar_spec_id,
            kind: "time".to_owned(),
            window_start_unix_nanos: occurred_at_unix_nanos,
            window_end_unix_nanos: occurred_at_unix_nanos,
            open: decimal_from_price(price_input(open)?),
            high: decimal_from_price(price_input(high)?),
            low: decimal_from_price(price_input(low)?),
            close: decimal_from_price(price_input(close)?),
            volume: volume
                .as_ref()
                .map(|value| quantity_input(value).map(decimal_from_quantity))
                .transpose()?,
            source_observed_at_unix_nanos: occurred_at_unix_nanos,
            received_at_unix_nanos: occurred_at_unix_nanos,
        };
        Ok(Self {
            metadata,
            kind: "bar_completed".to_owned(),
            data: Py::new(py, payload)?.into_any(),
        })
    }

    #[staticmethod]
    #[pyo3(signature = (*, sequence, market_id, instrument_id, provider, bid_price=None, bid_quantity=None, ask_price=None, ask_quantity=None, occurred_at_unix_nanos, launch_id=None, instance_id=None))]
    #[allow(clippy::too_many_arguments)]
    fn simulation_quote(
        py: Python<'_>,
        sequence: u64,
        market_id: String,
        instrument_id: String,
        provider: String,
        bid_price: Option<Bound<'_, PyAny>>,
        bid_quantity: Option<Bound<'_, PyAny>>,
        ask_price: Option<Bound<'_, PyAny>>,
        ask_quantity: Option<Bound<'_, PyAny>>,
        occurred_at_unix_nanos: u64,
        launch_id: Option<String>,
        instance_id: Option<String>,
    ) -> PyResult<Self> {
        let (metadata, scope, instrument_id, provider) = simulation_identity(
            sequence,
            market_id,
            instrument_id,
            provider,
            occurred_at_unix_nanos,
            launch_id,
            instance_id,
            1,
        )?;
        let payload = MarketQuoteCurrent {
            evidence: simulation_evidence(sequence, occurred_at_unix_nanos),
            quote_id: None,
            scope,
            instrument_id,
            provider,
            bid_price: bid_price
                .as_ref()
                .map(|value| price_input(value).map(decimal_from_price))
                .transpose()?,
            bid_quantity: bid_quantity
                .as_ref()
                .map(|value| quantity_input(value).map(decimal_from_quantity))
                .transpose()?,
            ask_price: ask_price
                .as_ref()
                .map(|value| price_input(value).map(decimal_from_price))
                .transpose()?,
            ask_quantity: ask_quantity
                .as_ref()
                .map(|value| quantity_input(value).map(decimal_from_quantity))
                .transpose()?,
            bid_venue_code: None,
            ask_venue_code: None,
            tape: None,
            source_observed_at_unix_nanos: occurred_at_unix_nanos,
            received_at_unix_nanos: occurred_at_unix_nanos,
        };
        Ok(Self {
            metadata,
            kind: "quote_updated".to_owned(),
            data: Py::new(py, payload)?.into_any(),
        })
    }

    #[getter]
    fn data(&self, py: Python<'_>) -> Py<PyAny> {
        self.data.clone_ref(py)
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
    fn causation_id(&self) -> Option<&str> {
        self.metadata.causation_id.as_deref()
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
    #[getter]
    fn schema_version(&self) -> u32 {
        2
    }
}

#[pyclass(
    name = "_NativeSemanticDecimal",
    frozen,
    module = "kairospy._native_market_contract"
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
        decimal_parts_text(self.mantissa, self.scale)
    }

    #[getter]
    fn semantic_type(&self) -> &'static str {
        self.semantic_type
    }

    #[getter]
    fn value(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let decimal = py.import("decimal")?.getattr("Decimal")?;
        Ok(decimal
            .call1((decimal_parts_text(self.mantissa, self.scale),))?
            .unbind())
    }
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
    root: PathBuf,
    identity: InstanceIdentity,
    path: PathBuf,
    reader: Mutex<Option<RustView>>,
    closed: Mutex<bool>,
}

#[pymethods]
impl MarketCurrentView {
    #[new]
    #[pyo3(signature = (root, workspace_id, launch_id=None, instance_id=None))]
    fn new(
        _py: Python<'_>,
        root: PathBuf,
        workspace_id: String,
        launch_id: Option<String>,
        instance_id: Option<String>,
    ) -> PyResult<Self> {
        let identity = identity(workspace_id, launch_id, instance_id)?;
        let path = kairos_market_contract::market_indexed_environment_path(&root, &identity)
            .map_err(contract_error)?;
        Ok(Self {
            creator_pid: std::process::id(),
            root,
            identity,
            path,
            reader: Mutex::new(None),
            closed: Mutex::new(false),
        })
    }

    #[getter]
    fn path(&self) -> PathBuf {
        self.path.clone()
    }

    fn get(
        &self,
        py: Python<'_>,
        key: PyRef<'_, NativeMarketViewKey>,
    ) -> PyResult<Option<Py<PyAny>>> {
        macro_rules! owned {
            ($value:expr) => {
                $value
                    .map(|value| Py::new(py, value).map(Py::into_any))
                    .transpose()
            };
        }
        match market_view_kind(&key.kind)? {
            MarketViewKind::Quote => {
                owned!(self.quote(py, key.scope_key.clone(), key.provider.clone())?)
            },
            MarketViewKind::Bar => {
                let qualifier = key.qualifier.clone().ok_or_else(|| {
                    MarketInvalidInputError::new_err("Market bar current view requires a qualifier")
                })?;
                owned!(self.bar(py, key.scope_key.clone(), key.provider.clone(), qualifier)?)
            },
            MarketViewKind::Greeks => {
                owned!(self.greeks(py, key.scope_key.clone(), key.provider.clone())?)
            },
            MarketViewKind::Rate => owned!(self.rate(
                py,
                key.scope_key.clone(),
                key.provider.clone(),
                key.qualifier.clone()
            )?),
            MarketViewKind::Ticker24h => owned!(self.ticker_24h(
                py,
                key.scope_key.clone(),
                key.provider.clone(),
                key.qualifier.clone()
            )?),
            MarketViewKind::MarkPrice => owned!(self.mark_price(
                py,
                key.scope_key.clone(),
                key.provider.clone(),
                key.qualifier.clone()
            )?),
            MarketViewKind::FundingRate => owned!(self.funding_rate(
                py,
                key.scope_key.clone(),
                key.provider.clone(),
                key.qualifier.clone()
            )?),
            MarketViewKind::OpenInterest => owned!(self.open_interest(
                py,
                key.scope_key.clone(),
                key.provider.clone(),
                key.qualifier.clone()
            )?),
            MarketViewKind::IndexPrice => owned!(self.index_price(
                py,
                key.scope_key.clone(),
                key.provider.clone(),
                key.qualifier.clone()
            )?),
            MarketViewKind::OrderBook => owned!(self.order_book(
                py,
                key.scope_key.clone(),
                key.provider.clone(),
                key.qualifier.clone()
            )?),
            MarketViewKind::Freshness => owned!(self.freshness(
                py,
                key.scope_key.clone(),
                key.provider.clone(),
                key.qualifier.clone()
            )?),
        }
    }

    #[pyo3(signature = (scope_key, provider))]
    fn quote(
        &self,
        py: Python<'_>,
        scope_key: String,
        provider: String,
    ) -> PyResult<Option<MarketQuoteCurrent>> {
        let key = RustViewKey::new(scope_key, provider, MarketViewKind::Quote, None::<String>)
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
        let key = RustViewKey::new(scope_key, provider, MarketViewKind::Bar, Some(qualifier))
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
        let key = RustViewKey::new(scope_key, provider, MarketViewKind::Greeks, None::<String>)
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
        *self.closed.lock().map_err(|_| {
            PyRuntimeError::new_err("Market current-view lifecycle lock is poisoned")
        })? = true;
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
            if *self.closed.lock().map_err(|_| {
                PyRuntimeError::new_err("Market current-view lifecycle lock is poisoned")
            })? {
                return Err(PyRuntimeError::new_err(
                    "Market current-view reader is closed",
                ));
            }
            let mut reader = self.reader.lock().map_err(|_| {
                PyRuntimeError::new_err("Market current-view reader lock is poisoned")
            })?;
            if reader.is_none() {
                *reader = Some(
                    RustView::open(self.root.clone(), &self.identity).map_err(contract_error)?,
                );
            }
            let reader = reader.as_ref().expect("reader initialized above");
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
                .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))
        },
        (None, None) => InstanceIdentity::unscoped(workspace_id)
            .map_err(|error| MarketInvalidInputError::new_err(error.to_string())),
        _ => Err(MarketInvalidInputError::new_err(
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
) -> PyResult<RustViewKey> {
    RustViewKey::new(scope, provider, kind, qualifier).map_err(contract_error)
}

fn market_view_kind(value: &str) -> PyResult<MarketViewKind> {
    match value {
        "quote" => Ok(MarketViewKind::Quote),
        "bar" => Ok(MarketViewKind::Bar),
        "greeks" => Ok(MarketViewKind::Greeks),
        "rate" => Ok(MarketViewKind::Rate),
        "ticker-24h" | "ticker_24h" => Ok(MarketViewKind::Ticker24h),
        "mark-price" | "mark_price" => Ok(MarketViewKind::MarkPrice),
        "funding-rate" | "funding_rate" => Ok(MarketViewKind::FundingRate),
        "open-interest" | "open_interest" => Ok(MarketViewKind::OpenInterest),
        "index-price" | "index_price" => Ok(MarketViewKind::IndexPrice),
        "order-book" | "order_book" => Ok(MarketViewKind::OrderBook),
        "freshness" => Ok(MarketViewKind::Freshness),
        other => Err(MarketInvalidInputError::new_err(format!(
            "unknown Market view kind {other}"
        ))),
    }
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
            implied_volatility: value.implied_volatility.map(decimal_from_rate),
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
            value: decimal_from_rate(v.value),
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
            volume_quote: v.volume_quote.map(decimal_from_money),
            price_change_abs: v.price_change_abs.map(decimal_from_price_delta),
            price_change_pct: v.price_change_pct.map(decimal_from_rate),
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
            funding_rate: v.funding_rate.map(decimal_from_rate),
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
            funding_rate: decimal_from_rate(v.funding_rate),
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
            quote_value: v.quote_value.map(decimal_from_money),
            change_24h: v.change_24h.map(decimal_from_signed_quantity),
            change_pct_24h: v.change_pct_24h.map(decimal_from_rate),
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
            funding_rate: v.funding_rate.map(decimal_from_rate),
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
        semantic_type: "price",
    }
}

fn decimal_from_price_delta(value: PriceDelta) -> NativeDecimal {
    NativeDecimal {
        mantissa: value.mantissa(),
        scale: value.scale(),
        semantic_type: "price_delta",
    }
}

fn decimal_from_quantity(value: Quantity) -> NativeDecimal {
    NativeDecimal {
        mantissa: value.mantissa(),
        scale: value.scale(),
        semantic_type: "quantity",
    }
}

fn decimal_from_parts(value: DecimalParts) -> NativeDecimal {
    NativeDecimal {
        mantissa: value.mantissa(),
        scale: value.scale(),
        semantic_type: "decimal",
    }
}

fn decimal_from_money(value: DecimalParts) -> NativeDecimal {
    semantic_decimal(value, "money")
}

fn decimal_from_rate(value: DecimalParts) -> NativeDecimal {
    semantic_decimal(value, "rate")
}

fn decimal_from_signed_quantity(value: DecimalParts) -> NativeDecimal {
    semantic_decimal(value, "signed_quantity")
}

fn semantic_decimal(value: DecimalParts, semantic_type: &'static str) -> NativeDecimal {
    NativeDecimal {
        mantissa: value.mantissa(),
        scale: value.scale(),
        semantic_type,
    }
}

fn decimal_parts_text(mantissa: i64, scale: u8) -> String {
    if scale == 0 {
        return mantissa.to_string();
    }
    let negative = mantissa.is_negative();
    let digits = mantissa.unsigned_abs().to_string();
    let scale = usize::from(scale);
    let body = if digits.len() <= scale {
        format!("0.{}{}", "0".repeat(scale - digits.len()), digits)
    } else {
        let split = digits.len() - scale;
        format!("{}.{}", &digits[..split], &digits[split..])
    };
    if negative { format!("-{body}") } else { body }
}

fn validated_required_text(value: String, name: &str) -> PyResult<String> {
    if value.is_empty() || value.trim() != value {
        Err(MarketInvalidInputError::new_err(format!(
            "{name} must be non-empty and trimmed"
        )))
    } else {
        Ok(value)
    }
}

#[allow(clippy::too_many_arguments)]
fn simulation_identity(
    sequence: u64,
    market_id: String,
    instrument_id: String,
    provider: String,
    occurred_at_unix_nanos: u64,
    launch_id: Option<String>,
    instance_id: Option<String>,
    producer_incarnation: u64,
) -> PyResult<(MarketEventMetadata, MarketObservationScope, String, String)> {
    if producer_incarnation == 0 {
        return Err(MarketInvalidInputError::new_err(
            "producer_incarnation must be positive",
        ));
    }
    let market_id = MarketId::new(market_id)
        .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?;
    let instrument_id = InstrumentId::new(instrument_id)
        .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?;
    let provider = RustProvider::new(provider)
        .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?;
    let launch_id = validated_optional_text(launch_id, "launch_id")?;
    let instance_id = validated_optional_text(instance_id, "instance_id")?;
    let metadata = MarketEventMetadata {
        event_id: format!("market.simulation:{sequence}"),
        stream_id: "market.events".to_owned(),
        sequence,
        producer: "market.simulation".to_owned(),
        producer_incarnation,
        workspace_id: "simulation".to_owned(),
        launch_id,
        instance_id,
        correlation_id: None,
        causation_id: None,
        occurred_at_unix_nanos,
        published_at_unix_nanos: occurred_at_unix_nanos,
    };
    let scope = MarketObservationScope {
        kind: "market".to_owned(),
        market_id: Some(market_id.to_string()),
        instrument_id: None,
        network_id: None,
    };
    Ok((
        metadata,
        scope,
        instrument_id.to_string(),
        provider.to_string(),
    ))
}

fn simulation_evidence(sequence: u64, occurred_at_unix_nanos: u64) -> MarketCurrentEvidence {
    MarketCurrentEvidence {
        resource_epoch: 0,
        producer_incarnation: 0,
        applied_event_sequence: sequence,
        committed_at_unix_nanos: occurred_at_unix_nanos,
        source_event_id: Some(format!("market.simulation:{sequence}")),
        synchronized: true,
    }
}

fn validated_optional_text(value: Option<String>, name: &str) -> PyResult<Option<String>> {
    value
        .map(|value| {
            if value.is_empty() || value.trim() != value {
                Err(MarketInvalidInputError::new_err(format!(
                    "{name} must be non-empty and trimmed"
                )))
            } else {
                Ok(value)
            }
        })
        .transpose()
}

fn validated_provider_args(
    values: &Bound<'_, pyo3::types::PyTuple>,
) -> PyResult<Vec<RustProvider>> {
    if values.is_empty() {
        return Err(MarketInvalidInputError::new_err(
            "provider preference requires at least one provider",
        ));
    }
    values
        .iter()
        .map(|value| {
            if let Ok(provider) = value.extract::<PyRef<'_, NativeProvider>>() {
                return Ok(provider.value.clone());
            }
            let value = value.extract::<String>().map_err(|_| {
                MarketInvalidInputError::new_err("provider must be a canonical string or Provider")
            })?;
            RustProvider::new(value)
                .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))
        })
        .collect()
}

fn validated_option_right(value: Option<String>) -> PyResult<Option<String>> {
    value
        .map(|value| match value.trim().to_ascii_lowercase().as_str() {
            "call" | "put" | "both" => Ok(value.trim().to_ascii_lowercase()),
            _ => Err(MarketInvalidInputError::new_err(
                "option right must be call, put, or both",
            )),
        })
        .transpose()
}

fn option_right_arg(value: Option<&Bound<'_, PyAny>>) -> PyResult<Option<String>> {
    value
        .map(|value| {
            if let Ok(value) = value.extract::<PyRef<'_, NativeOptionRight>>() {
                return Ok(value.value.clone());
            }
            validated_option_right(Some(value.extract::<String>().map_err(|_| {
                MarketInvalidInputError::new_err("option right must be a string or OptionRight")
            })?))
            .map(|value| value.expect("provided option right"))
        })
        .transpose()
}

fn canonical_underlying(value: &Bound<'_, PyAny>) -> PyResult<String> {
    let value = if let Ok(value) = value.extract::<String>() {
        value
    } else {
        value.str()?.to_str()?.to_owned()
    };
    if value.starts_with("market:") {
        MarketId::new(value.clone())
            .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?;
    } else if value.starts_with("instrument:") {
        InstrumentId::new(value.clone())
            .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?;
    } else {
        return Err(MarketInvalidInputError::new_err(
            "options underlying must be a canonical MarketId or InstrumentId",
        ));
    }
    Ok(value)
}

fn expiry_bounds(
    value: Option<&NativeExpiryRange>,
    now_unix_nanos: Option<u64>,
) -> PyResult<(Option<u64>, Option<u64>)> {
    const DAY_NANOS: u64 = 86_400_000_000_000;
    let Some(value) = value else {
        return Ok((None, None));
    };
    match &value.inner {
        ExpirySpec::Absolute { from, to } => Ok((*from, *to)),
        ExpirySpec::Relative { from_days, to_days } => {
            let now = now_unix_nanos.ok_or_else(|| {
                MarketInvalidInputError::new_err(
                    "relative expiry requires now_unix_nanos at the owner boundary",
                )
            })?;
            let day_start = now / DAY_NANOS * DAY_NANOS;
            let from = match *from_days {
                Some(days) => Some(
                    day_start
                        .checked_add(u64::from(days) * DAY_NANOS)
                        .ok_or_else(|| {
                            MarketInvalidInputError::new_err("relative expiry lower bound overflow")
                        })?,
                ),
                None => None,
            };
            let to = match *to_days {
                Some(days) => Some(
                    day_start
                        .checked_add((u64::from(days) + 1) * DAY_NANOS)
                        .and_then(|value| value.checked_sub(1))
                        .ok_or_else(|| {
                            MarketInvalidInputError::new_err("relative expiry upper bound overflow")
                        })?,
                ),
                None => None,
            };
            Ok((from, to))
        },
    }
}

fn strike_bounds(
    value: Option<&NativeStrikeRange>,
    spot: Option<&str>,
) -> PyResult<(Option<String>, Option<String>)> {
    let Some(value) = value else {
        return Ok((None, None));
    };
    match &value.inner {
        StrikeSpec::Absolute { lower, upper } => {
            Ok((lower.map(decimal_text), upper.map(decimal_text)))
        },
        StrikeSpec::AroundSpot(percent) => {
            let spot = price(spot.ok_or_else(|| {
                MarketInvalidInputError::new_err(
                    "around_spot strike range requires a current spot price",
                )
            })?)?;
            let lower = apply_percent(spot, *percent, false)?;
            let upper = apply_percent(spot, *percent, true)?;
            Ok((Some(decimal_text(lower)), Some(decimal_text(upper))))
        },
    }
}

fn apply_percent(value: Price, percent: Price, add: bool) -> PyResult<Price> {
    let factor_scale = percent.scale();
    let base = 10_i128.pow(u32::from(factor_scale));
    let delta = i128::from(percent.mantissa());
    let factor = if add { base + delta } else { base - delta };
    if factor <= 0 {
        return Err(MarketInvalidInputError::new_err(
            "around_spot percent must leave a positive lower strike",
        ));
    }
    let mantissa = i128::from(value.mantissa())
        .checked_mul(factor)
        .ok_or_else(|| MarketInvalidInputError::new_err("around_spot strike overflow"))?;
    let mantissa = i64::try_from(mantissa)
        .map_err(|_| MarketInvalidInputError::new_err("around_spot strike overflow"))?;
    let scale = value
        .scale()
        .checked_add(factor_scale)
        .ok_or_else(|| MarketInvalidInputError::new_err("around_spot strike scale overflow"))?;
    Price::new(mantissa, scale).map_err(|error| MarketInvalidInputError::new_err(error.to_string()))
}

fn price(value: &str) -> PyResult<Price> {
    let parts: DecimalParts =
        value
            .parse()
            .map_err(|error: kairos_primitives::DomainTypeError| {
                MarketInvalidInputError::new_err(error.to_string())
            })?;
    Price::new(parts.mantissa(), parts.scale())
        .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))
}

fn semantic_parts(value: &Bound<'_, PyAny>, expected: &str) -> PyResult<DecimalParts> {
    if let Ok(text) = value.extract::<String>() {
        return text
            .parse()
            .map_err(|error: kairos_primitives::DomainTypeError| {
                MarketInvalidInputError::new_err(error.to_string())
            });
    }
    let semantic_type = value
        .getattr("semantic_type")
        .and_then(|value| value.extract::<String>())
        .map_err(|_| {
            MarketInvalidInputError::new_err(format!(
                "Market {expected} requires exact text or a semantic decimal value"
            ))
        })?;
    if semantic_type != expected {
        return Err(MarketInvalidInputError::new_err(format!(
            "Market {expected} cannot be constructed from {semantic_type}"
        )));
    }
    DecimalParts::new(
        value.getattr("mantissa")?.extract::<i64>()?,
        value.getattr("scale")?.extract::<u8>()?,
    )
    .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))
}

fn price_input(value: &Bound<'_, PyAny>) -> PyResult<Price> {
    let parts = semantic_parts(value, "price")?;
    Price::new(parts.mantissa(), parts.scale())
        .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))
}

fn quantity_input(value: &Bound<'_, PyAny>) -> PyResult<Quantity> {
    let parts = semantic_parts(value, "quantity")?;
    Quantity::new(parts.mantissa(), parts.scale())
        .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))
}

fn decimal_cmp(left: Price, right: Price) -> std::cmp::Ordering {
    let scale = left.scale().max(right.scale());
    let left = i128::from(left.mantissa()) * 10_i128.pow(u32::from(scale - left.scale()));
    let right = i128::from(right.mantissa()) * 10_i128.pow(u32::from(scale - right.scale()));
    left.cmp(&right)
}

fn decimal_text(value: Price) -> String {
    DecimalParts::new(value.mantissa(), value.scale())
        .expect("validated Price has a valid decimal scale")
        .to_string()
}

fn market_command<T>(
    operation: MarketOperation,
    payload: T,
    strategy_id: String,
    instance_id: String,
    request_id: String,
    launch_id: Option<String>,
) -> PyResult<MarketCommandEnvelope<T>> {
    Ok(MarketCommandEnvelope {
        schema_version: 2,
        command_id: RequestId::new(request_id.clone())
            .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
        idempotency_key: IdempotencyKey::new(request_id)
            .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
        operation,
        strategy_id: StrategyId::new(strategy_id)
            .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
        launch_id: launch_id
            .map(LaunchId::new)
            .transpose()
            .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
        instance_id: InstanceId::new(instance_id)
            .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
        payload,
    })
}

fn operator_command<T>(
    operation: MarketOperation,
    payload: T,
    owner_id: String,
    request_id: String,
) -> PyResult<MarketOperatorCommandEnvelope<T>> {
    Ok(MarketOperatorCommandEnvelope {
        schema_version: 2,
        command_id: RequestId::new(request_id.clone())
            .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
        idempotency_key: IdempotencyKey::new(request_id)
            .map_err(|error| MarketInvalidInputError::new_err(error.to_string()))?,
        operation,
        owner_id: SubscriptionOwnerKey::new(owner_id).map_err(MarketInvalidInputError::new_err)?,
        payload,
    })
}

fn run_control<F, T, E>(timeout: Duration, future: F) -> PyResult<T>
where
    F: std::future::Future<Output = Result<T, E>>,
    E: kairos_protocol::contract::ControlCallError,
{
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_io()
        .enable_time()
        .build()
        .map_err(|error| MarketControlUnavailableError::new_err(error.to_string()))?;
    runtime
        .block_on(async move { tokio::time::timeout(timeout, future).await })
        .map_err(|_| MarketControlUnavailableError::new_err("Market control request timed out"))?
        .map_err(|error| {
            if kairos_protocol::contract::is_control_rejection(&error) {
                MarketControlRejectedError::new_err(error.to_string())
            } else {
                MarketControlUnavailableError::new_err(error.to_string())
            }
        })
}

fn project_health(value: RustHealthResponse) -> NativeMarketHealthResponse {
    NativeMarketHealthResponse {
        status: match value.status {
            MarketHealthStatus::Ready => "ready",
            MarketHealthStatus::Degraded => "degraded",
        }
        .to_owned(),
        actor_id: value.actor_id.to_string(),
        event_sequence: value.event_sequence.get(),
        feed_status: match value.feed_status {
            MarketFeedStatus::Disconnected => "disconnected",
            MarketFeedStatus::Ready => "ready",
            MarketFeedStatus::Reconnecting => "reconnecting",
            MarketFeedStatus::WarmingUp => "warming_up",
            MarketFeedStatus::Degraded => "degraded",
        }
        .to_owned(),
        current_view_commit_count: value.current_view_commit_count,
        current_view_input_update_count: value.current_view_input_update_count,
        current_view_encoded_update_count: value.current_view_encoded_update_count,
        current_view_order_book_encode_count: value.current_view_order_book_encode_count,
        last_current_view_commit_latency_nanos: value.last_current_view_commit_latency_nanos,
        notification_attempt_count: value.notification_attempt_count,
        notification_failure_count: value.notification_failure_count,
    }
}

fn project_routes(value: RustDataRoutesResponse) -> NativeMarketDataRoutesResponse {
    NativeMarketDataRoutesResponse {
        routes: value.routes.into_iter().map(project_route).collect(),
    }
}

fn project_route(value: RustDataRoute) -> NativeMarketDataRoute {
    NativeMarketDataRoute {
        market_id: value.market_id.to_string(),
        provider: value.provider.to_string(),
        observation_kinds: value
            .observation_kinds
            .into_iter()
            .map(|value| value.as_str().to_owned())
            .collect(),
        state: match value.state {
            MarketDataRouteState::Supported => "supported",
            MarketDataRouteState::Configured => "configured",
            MarketDataRouteState::Ready => "ready",
            MarketDataRouteState::Degraded => "degraded",
            MarketDataRouteState::Stopped => "stopped",
        }
        .to_owned(),
        selected: value.selected,
        pending_reason: value.pending_reason,
    }
}

fn project_subscription(value: RustSubscriptionResponse) -> NativeMarketSubscriptionResponse {
    NativeMarketSubscriptionResponse {
        subscription_id: value.subscription_id.to_string(),
        owner_id: value.owner_id.to_string(),
        state: subscription_state(value.state).to_owned(),
        satisfied_selectors: value.satisfied.iter().map(observation_selector).collect(),
        missing_selectors: value.missing.iter().map(observation_selector).collect(),
        resolved_providers: value
            .resolved_providers
            .into_iter()
            .map(|value| value.to_string())
            .collect(),
        pending_reason: value.pending_reason.as_ref().map(pending_reason),
    }
}

fn project_subscriptions(value: RustSubscriptionsResponse) -> NativeMarketSubscriptionsResponse {
    NativeMarketSubscriptionsResponse {
        subscriptions: value
            .subscriptions
            .into_iter()
            .map(project_subscription_snapshot)
            .collect(),
    }
}

fn project_subscription_snapshot(
    value: RustSubscriptionSnapshot,
) -> NativeMarketSubscriptionSnapshot {
    NativeMarketSubscriptionSnapshot {
        subscription_id: value.subscription_id.to_string(),
        owner_id: value.owner_id.to_string(),
        state: subscription_state(value.state).to_owned(),
        market_ids: value
            .market_ids
            .into_iter()
            .map(|value| value.to_string())
            .collect(),
        observations: value
            .observations
            .iter()
            .map(observation_selector)
            .collect(),
        selected_providers: value
            .selected_providers
            .into_iter()
            .map(|value| value.to_string())
            .collect(),
        pending_reason: value.pending_reason.as_ref().map(pending_reason),
    }
}

fn subscription_state(value: MarketSubscriptionState) -> &'static str {
    match value {
        MarketSubscriptionState::Resolving => "resolving",
        MarketSubscriptionState::Active => "active",
        MarketSubscriptionState::PartiallyActive => "partially_active",
        MarketSubscriptionState::WaitingForProvider => "waiting_for_provider",
        MarketSubscriptionState::WaitingForMarket => "waiting_for_market",
        MarketSubscriptionState::Degraded => "degraded",
        MarketSubscriptionState::Failed => "failed",
        MarketSubscriptionState::Released => "released",
    }
}

fn parse_subscription_state(value: &str) -> PyResult<MarketSubscriptionState> {
    match value {
        "resolving" => Ok(MarketSubscriptionState::Resolving),
        "active" => Ok(MarketSubscriptionState::Active),
        "partially_active" => Ok(MarketSubscriptionState::PartiallyActive),
        "waiting_for_provider" => Ok(MarketSubscriptionState::WaitingForProvider),
        "waiting_for_market" => Ok(MarketSubscriptionState::WaitingForMarket),
        "degraded" => Ok(MarketSubscriptionState::Degraded),
        "failed" => Ok(MarketSubscriptionState::Failed),
        "released" => Ok(MarketSubscriptionState::Released),
        _ => Err(MarketInvalidInputError::new_err(format!(
            "unknown Market subscription state: {value}"
        ))),
    }
}

fn observation_selector(value: &RustObservationRequirement) -> String {
    value.qualifier.as_ref().map_or_else(
        || value.kind.as_str().to_owned(),
        |qualifier| format!("{}:{qualifier}", value.kind.as_str()),
    )
}

fn pending_reason(value: &SubscriptionPendingReason) -> String {
    match value {
        SubscriptionPendingReason::ResolvingUnderlying => "resolving_underlying",
        SubscriptionPendingReason::WaitingForSpot => "waiting_for_spot",
        SubscriptionPendingReason::SelectingContracts => "selecting_contracts",
        SubscriptionPendingReason::SubscribingMembers { .. } => "subscribing_members",
        SubscriptionPendingReason::MarketUnavailable => "market_unavailable",
        SubscriptionPendingReason::ProviderUnavailable { .. } => "provider_unavailable",
        SubscriptionPendingReason::MissingObservations { .. } => "missing_observations",
    }
    .to_owned()
}

fn project_command_status(value: RustCommandStatus) -> NativeMarketCommandStatus {
    NativeMarketCommandStatus {
        status: match value.status {
            MarketCommandOutcome::Applied => "applied",
            MarketCommandOutcome::Accepted => "accepted",
            MarketCommandOutcome::Recovered => "recovered",
            MarketCommandOutcome::Paused => "paused",
            MarketCommandOutcome::Running => "running",
        }
        .to_owned(),
    }
}

fn market_event_metadata(
    value: kairos_protocol::generated::kairos::common::v_2::EventMetadata<'_>,
) -> PyResult<MarketEventMetadata> {
    let EventMetadataOwned {
        event_id,
        stream_id,
        sequence,
        producer_id,
        producer_incarnation,
        workspace_id,
        launch_id,
        instance_id,
        correlation_id,
        causation_id,
        occurred_at_unix_nanos,
        published_at_unix_nanos,
    } = decode_event_metadata(value)
        .map_err(|error| MarketInvalidEventError::new_err(error.to_string()))?;
    Ok(MarketEventMetadata {
        event_id: event_id.to_string(),
        stream_id: stream_id.to_string(),
        sequence: sequence.get(),
        producer: producer_id.to_string(),
        producer_incarnation,
        workspace_id: workspace_id.to_string(),
        launch_id: launch_id.map(|value| value.to_string()),
        instance_id: instance_id.map(|value| value.to_string()),
        correlation_id: correlation_id.map(|value| value.to_string()),
        causation_id: causation_id.map(|value| value.to_string()),
        occurred_at_unix_nanos: occurred_at_unix_nanos.get(),
        published_at_unix_nanos: published_at_unix_nanos.get(),
    })
}

fn event_evidence(metadata: &MarketEventMetadata) -> MarketCurrentEvidence {
    MarketCurrentEvidence {
        resource_epoch: 0,
        producer_incarnation: 0,
        applied_event_sequence: metadata.sequence,
        committed_at_unix_nanos: metadata.published_at_unix_nanos,
        source_event_id: Some(metadata.event_id.clone()),
        synchronized: true,
    }
}

fn event_scope(value: fb::ObservationScope<'_>) -> PyResult<MarketObservationScope> {
    match value.kind().0 {
        1 => Ok(MarketObservationScope {
            kind: "market".to_owned(),
            market_id: Some(required_event_text(value.market_id(), "scope.market_id")?),
            instrument_id: None,
            network_id: None,
        }),
        2 => Ok(MarketObservationScope {
            kind: "consolidated".to_owned(),
            market_id: None,
            instrument_id: Some(required_event_text(
                value.instrument_id(),
                "scope.instrument_id",
            )?),
            network_id: optional_event_text(value.network_id()),
        }),
        other => Err(MarketInvalidEventError::new_err(format!(
            "unknown Market observation scope kind {other}"
        ))),
    }
}

fn event_decimal(
    value: &kairos_protocol::generated::kairos::common::v_2::Decimal64,
    semantic_type: &'static str,
) -> PyResult<NativeDecimal> {
    DecimalParts::new(value.mantissa(), value.scale())
        .map(|value| semantic_decimal(value, semantic_type))
        .map_err(|error| MarketInvalidEventError::new_err(error.to_string()))
}

fn event_exact_decimal(
    value: &kairos_protocol::generated::kairos::common::v_2::Decimal64,
) -> PyResult<NativeDecimal> {
    event_decimal(value, "decimal")
}

fn event_money(
    value: &kairos_protocol::generated::kairos::common::v_2::Decimal64,
) -> PyResult<NativeDecimal> {
    event_decimal(value, "money")
}

fn event_price(
    value: &kairos_protocol::generated::kairos::common::v_2::Decimal64,
) -> PyResult<NativeDecimal> {
    event_decimal(value, "price")
}

fn event_price_delta(
    value: &kairos_protocol::generated::kairos::common::v_2::Decimal64,
) -> PyResult<NativeDecimal> {
    PriceDelta::new(value.mantissa(), value.scale())
        .map(|value| decimal_from_price_delta(value))
        .map_err(|error| MarketInvalidEventError::new_err(error.to_string()))
}

fn event_quantity(
    value: &kairos_protocol::generated::kairos::common::v_2::Decimal64,
) -> PyResult<NativeDecimal> {
    event_decimal(value, "quantity")
}

fn event_rate(
    value: &kairos_protocol::generated::kairos::common::v_2::Decimal64,
) -> PyResult<NativeDecimal> {
    event_decimal(value, "rate")
}

fn event_signed_quantity(
    value: &kairos_protocol::generated::kairos::common::v_2::Decimal64,
) -> PyResult<NativeDecimal> {
    event_decimal(value, "signed_quantity")
}

fn required_event_text(value: Option<&str>, name: &str) -> PyResult<String> {
    let value = value
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| {
            MarketInvalidEventError::new_err(format!("Market event {name} is required"))
        })?;
    Ok(value.to_owned())
}

fn required_event_value(value: &str, name: &str) -> PyResult<String> {
    required_event_text(Some(value), name)
}

fn optional_event_text(value: Option<&str>) -> Option<String> {
    value
        .filter(|value| !value.trim().is_empty())
        .map(str::to_owned)
}

fn project_quote(
    metadata: &MarketEventMetadata,
    value: fb::Quote<'_>,
) -> PyResult<MarketQuoteCurrent> {
    Ok(MarketQuoteCurrent {
        evidence: event_evidence(metadata),
        quote_id: optional_event_text(value.quote_id()),
        scope: event_scope(value.scope())?,
        instrument_id: required_event_value(value.instrument_id(), "instrument_id")?,
        provider: required_event_value(value.provider(), "provider")?,
        bid_price: value.bid_price().map(event_price).transpose()?,
        bid_quantity: value.bid_quantity().map(event_quantity).transpose()?,
        ask_price: value.ask_price().map(event_price).transpose()?,
        ask_quantity: value.ask_quantity().map(event_quantity).transpose()?,
        bid_venue_code: optional_event_text(value.bid_venue_code()),
        ask_venue_code: optional_event_text(value.ask_venue_code()),
        tape: (value.tape() != 0).then_some(value.tape()),
        source_observed_at_unix_nanos: value.source_observed_at_unix_nanos(),
        received_at_unix_nanos: value.received_at_unix_nanos(),
    })
}

fn project_bar(metadata: &MarketEventMetadata, value: fb::Bar<'_>) -> PyResult<MarketBarCurrent> {
    let kind = match value.kind().0 {
        1 => "trades",
        2 => "quotes",
        3 => "midpoint",
        other => {
            return Err(MarketInvalidEventError::new_err(format!(
                "unknown Market bar kind {other}"
            )));
        },
    };
    Ok(MarketBarCurrent {
        evidence: event_evidence(metadata),
        scope: event_scope(value.scope())?,
        instrument_id: required_event_value(value.instrument_id(), "instrument_id")?,
        provider: required_event_value(value.provider(), "provider")?,
        bar_spec_id: required_event_value(value.bar_spec_id(), "bar_spec_id")?,
        kind: kind.to_owned(),
        window_start_unix_nanos: value.window_start_unix_nanos(),
        window_end_unix_nanos: value.window_end_unix_nanos(),
        open: event_price(value.open())?,
        high: event_price(value.high())?,
        low: event_price(value.low())?,
        close: event_price(value.close())?,
        volume: value.volume().map(event_quantity).transpose()?,
        source_observed_at_unix_nanos: value.source_observed_at_unix_nanos(),
        received_at_unix_nanos: value.received_at_unix_nanos(),
    })
}

fn project_greeks(
    metadata: &MarketEventMetadata,
    value: fb::Greeks<'_>,
) -> PyResult<MarketGreeksCurrent> {
    Ok(MarketGreeksCurrent {
        evidence: event_evidence(metadata),
        scope: event_scope(value.scope())?,
        instrument_id: required_event_value(value.instrument_id(), "instrument_id")?,
        provider: required_event_value(value.provider(), "provider")?,
        expiry_unix_nanos: value.expiry_unix_nanos(),
        strike: value.strike().map(event_price).transpose()?,
        delta: value.delta().map(event_exact_decimal).transpose()?,
        gamma: value.gamma().map(event_exact_decimal).transpose()?,
        vega: value.vega().map(event_exact_decimal).transpose()?,
        theta: value.theta().map(event_exact_decimal).transpose()?,
        implied_volatility: value.implied_volatility().map(event_rate).transpose()?,
        source_observed_at_unix_nanos: value.source_observed_at_unix_nanos(),
        received_at_unix_nanos: value.received_at_unix_nanos(),
        derivation_id: optional_event_text(value.derivation_id()),
    })
}

fn project_trade(value: fb::Trade<'_>) -> PyResult<MarketTradeEventPayload> {
    let aggressor_side = match value.aggressor_side().0 {
        0 => None,
        1 => Some("buy".to_owned()),
        2 => Some("sell".to_owned()),
        other => {
            return Err(MarketInvalidEventError::new_err(format!(
                "unknown Market trade side {other}"
            )));
        },
    };
    Ok(MarketTradeEventPayload {
        trade_id: optional_event_text(value.trade_id()),
        scope: event_scope(value.scope())?,
        instrument_id: required_event_value(value.instrument_id(), "instrument_id")?,
        provider: required_event_value(value.provider(), "provider")?,
        price: event_price(value.price())?,
        quantity: event_quantity(value.quantity())?,
        aggressor_side,
        venue_code: optional_event_text(value.venue_code()),
        tape: (value.tape() != 0).then_some(value.tape()),
        trf_id: (value.trf_id() != 0).then_some(value.trf_id()),
        participant_timestamp_unix_nanos: (value.participant_timestamp_unix_nanos() != 0)
            .then_some(value.participant_timestamp_unix_nanos()),
        trf_timestamp_unix_nanos: (value.trf_timestamp_unix_nanos() != 0)
            .then_some(value.trf_timestamp_unix_nanos()),
        source_observed_at_unix_nanos: value.source_observed_at_unix_nanos(),
        received_at_unix_nanos: value.received_at_unix_nanos(),
    })
}

fn project_rate(
    metadata: &MarketEventMetadata,
    value: fb::Rate<'_>,
) -> PyResult<MarketRateCurrent> {
    Ok(MarketRateCurrent {
        evidence: event_evidence(metadata),
        scope: event_scope(value.scope())?,
        rate_id: required_event_value(value.rate_id(), "rate_id")?,
        instrument_id: required_event_value(value.instrument_id(), "instrument_id")?,
        provider: required_event_value(value.provider(), "provider")?,
        basis: required_event_value(value.basis(), "basis")?,
        value: event_rate(value.value())?,
        mark_price: value.mark_price().map(event_price).transpose()?,
        source_observed_at_unix_nanos: value.source_observed_at_unix_nanos(),
        received_at_unix_nanos: value.received_at_unix_nanos(),
    })
}

fn project_ticker(
    metadata: &MarketEventMetadata,
    value: fb::Ticker24h<'_>,
) -> PyResult<MarketTicker24hCurrent> {
    Ok(MarketTicker24hCurrent {
        evidence: event_evidence(metadata),
        scope: event_scope(value.scope())?,
        instrument_id: required_event_value(value.instrument_id(), "instrument_id")?,
        provider: required_event_value(value.provider(), "provider")?,
        last_price: value.last_price().map(event_price).transpose()?,
        bid_price: value.bid_price().map(event_price).transpose()?,
        bid_quantity: value.bid_quantity().map(event_quantity).transpose()?,
        ask_price: value.ask_price().map(event_price).transpose()?,
        ask_quantity: value.ask_quantity().map(event_quantity).transpose()?,
        open_price: value.open_price().map(event_price).transpose()?,
        high_price: value.high_price().map(event_price).transpose()?,
        low_price: value.low_price().map(event_price).transpose()?,
        volume_base: value.volume_base().map(event_quantity).transpose()?,
        volume_quote: value.volume_quote().map(event_money).transpose()?,
        price_change_abs: value
            .price_change_abs()
            .map(event_price_delta)
            .transpose()?,
        price_change_pct: value.price_change_pct().map(event_rate).transpose()?,
        vwap: value.vwap().map(event_price).transpose()?,
        mark_price: value.mark_price().map(event_price).transpose()?,
        source_observed_at_unix_nanos: value.source_observed_at_unix_nanos(),
        received_at_unix_nanos: value.received_at_unix_nanos(),
    })
}

fn project_mark(
    metadata: &MarketEventMetadata,
    value: fb::MarkPrice<'_>,
) -> PyResult<MarketMarkPriceCurrent> {
    Ok(MarketMarkPriceCurrent {
        evidence: event_evidence(metadata),
        scope: event_scope(value.scope())?,
        instrument_id: required_event_value(value.instrument_id(), "instrument_id")?,
        provider: required_event_value(value.provider(), "provider")?,
        mark_price: event_price(value.mark_price())?,
        index_price: value.index_price().map(event_price).transpose()?,
        estimated_settlement_price: value
            .estimated_settlement_price()
            .map(event_price)
            .transpose()?,
        funding_rate: value.funding_rate().map(event_rate).transpose()?,
        next_funding_time_unix_nanos: (value.next_funding_time_unix_nanos() != 0)
            .then_some(value.next_funding_time_unix_nanos()),
        source_observed_at_unix_nanos: value.source_observed_at_unix_nanos(),
        received_at_unix_nanos: value.received_at_unix_nanos(),
    })
}

fn project_funding(
    metadata: &MarketEventMetadata,
    value: fb::FundingRate<'_>,
) -> PyResult<MarketFundingRateCurrent> {
    Ok(MarketFundingRateCurrent {
        evidence: event_evidence(metadata),
        scope: event_scope(value.scope())?,
        instrument_id: required_event_value(value.instrument_id(), "instrument_id")?,
        provider: required_event_value(value.provider(), "provider")?,
        funding_rate: event_rate(value.funding_rate())?,
        funding_period_seconds: value.funding_period_seconds(),
        next_funding_time_unix_nanos: (value.next_funding_time_unix_nanos() != 0)
            .then_some(value.next_funding_time_unix_nanos()),
        source_observed_at_unix_nanos: value.source_observed_at_unix_nanos(),
        received_at_unix_nanos: value.received_at_unix_nanos(),
    })
}

fn project_open_interest(
    metadata: &MarketEventMetadata,
    value: fb::OpenInterest<'_>,
) -> PyResult<MarketOpenInterestCurrent> {
    Ok(MarketOpenInterestCurrent {
        evidence: event_evidence(metadata),
        scope: event_scope(value.scope())?,
        instrument_id: required_event_value(value.instrument_id(), "instrument_id")?,
        provider: required_event_value(value.provider(), "provider")?,
        contracts: event_quantity(value.contracts())?,
        quote_value: value.quote_value().map(event_money).transpose()?,
        change_24h: value.change_24h().map(event_signed_quantity).transpose()?,
        change_pct_24h: value.change_pct_24h().map(event_rate).transpose()?,
        source_observed_at_unix_nanos: value.source_observed_at_unix_nanos(),
        received_at_unix_nanos: value.received_at_unix_nanos(),
    })
}

fn project_index(
    metadata: &MarketEventMetadata,
    value: fb::IndexPrice<'_>,
) -> PyResult<MarketIndexPriceCurrent> {
    Ok(MarketIndexPriceCurrent {
        evidence: event_evidence(metadata),
        scope: event_scope(value.scope())?,
        instrument_id: required_event_value(value.instrument_id(), "instrument_id")?,
        provider: required_event_value(value.provider(), "provider")?,
        spot_index_price: value.spot_index_price().map(event_price).transpose()?,
        contract_index_price: value.contract_index_price().map(event_price).transpose()?,
        index_price: value.index_price().map(event_price).transpose()?,
        funding_rate: value.funding_rate().map(event_rate).transpose()?,
        source_observed_at_unix_nanos: value.source_observed_at_unix_nanos(),
        received_at_unix_nanos: value.received_at_unix_nanos(),
    })
}

fn order_book_level(value: fb::OrderBookLevel<'_>) -> PyResult<MarketOrderBookLevel> {
    Ok(MarketOrderBookLevel {
        price: event_price(value.price())?,
        quantity: event_quantity(value.quantity())?,
        order_count: value.order_count(),
    })
}

fn project_order_book(
    metadata: &MarketEventMetadata,
    value: fb::OrderBookSnapshotValue<'_>,
) -> PyResult<MarketOrderBookCurrent> {
    let identity = value.identity();
    Ok(MarketOrderBookCurrent {
        evidence: event_evidence(metadata),
        provider: required_event_value(identity.provider(), "order_book.provider")?,
        market_id: required_event_value(identity.market_id(), "order_book.market_id")?,
        instrument_id: required_event_value(identity.instrument_id(), "order_book.instrument_id")?,
        sequence: value.sequence(),
        source_observed_at_unix_nanos: value.source_observed_at_unix_nanos(),
        received_at_unix_nanos: value.received_at_unix_nanos(),
        checksum: optional_event_text(value.checksum()),
        depth_policy: required_event_value(value.depth_policy(), "order_book.depth_policy")?,
        bids: value
            .bids()
            .iter()
            .map(order_book_level)
            .collect::<PyResult<_>>()?,
        asks: value
            .asks()
            .iter()
            .map(order_book_level)
            .collect::<PyResult<_>>()?,
    })
}

fn project_delta(value: fb::OrderBookDeltaValue<'_>) -> PyResult<MarketOrderBookDeltaEventPayload> {
    let identity = value.identity();
    let changes = value
        .changes()
        .iter()
        .map(|change| {
            let side = match change.side().0 {
                1 => "bid",
                2 => "ask",
                other => {
                    return Err(MarketInvalidEventError::new_err(format!(
                        "unknown Market order-book side {other}"
                    )));
                },
            };
            let action = match change.action().0 {
                1 => "upsert",
                2 => "delete",
                other => {
                    return Err(MarketInvalidEventError::new_err(format!(
                        "unknown Market order-book action {other}"
                    )));
                },
            };
            Ok(MarketOrderBookChange {
                side: side.to_owned(),
                action: action.to_owned(),
                price: event_price(change.price())?,
                quantity: change.quantity().map(event_quantity).transpose()?,
                order_count: change.order_count(),
            })
        })
        .collect::<PyResult<Vec<_>>>()?;
    Ok(MarketOrderBookDeltaEventPayload {
        provider: required_event_value(identity.provider(), "order_book.provider")?,
        market_id: required_event_value(identity.market_id(), "order_book.market_id")?,
        instrument_id: required_event_value(identity.instrument_id(), "order_book.instrument_id")?,
        first_sequence: value.first_sequence(),
        last_sequence: value.last_sequence(),
        source_observed_at_unix_nanos: value.source_observed_at_unix_nanos(),
        received_at_unix_nanos: value.received_at_unix_nanos(),
        checksum: optional_event_text(value.checksum()),
        changes,
    })
}

fn project_resync(
    root: fb::OrderBookResyncRequired<'_>,
) -> PyResult<MarketOrderBookResyncEventPayload> {
    let identity = root.identity();
    Ok(MarketOrderBookResyncEventPayload {
        provider: required_event_value(identity.provider(), "order_book.provider")?,
        market_id: required_event_value(identity.market_id(), "order_book.market_id")?,
        instrument_id: required_event_value(identity.instrument_id(), "order_book.instrument_id")?,
        expected_sequence: root.expected_sequence(),
        observed_sequence: root.observed_sequence(),
        reason: required_event_value(root.reason(), "order_book.reason")?,
    })
}

fn project_market_event(py: Python<'_>, value: RustMarketEvent<'_>) -> PyResult<MarketEvent> {
    macro_rules! project {
        ($root:expr, $payload:expr) => {{
            let metadata = market_event_metadata($root.metadata())?;
            let payload = Py::new(py, $payload)?.into_any();
            (metadata, payload)
        }};
    }
    let kind = value.kind().as_str().to_owned();
    let (metadata, data) = match value {
        RustMarketEvent::QuoteUpdated(root) => {
            let metadata = market_event_metadata(root.metadata())?;
            let payload = Py::new(py, project_quote(&metadata, root.quote())?)?.into_any();
            (metadata, payload)
        },
        RustMarketEvent::TradeOccurred(root) => {
            project!(root, project_trade(root.trade())?)
        },
        RustMarketEvent::BarCompleted(root) => {
            let metadata = market_event_metadata(root.metadata())?;
            let payload = Py::new(py, project_bar(&metadata, root.bar())?)?.into_any();
            (metadata, payload)
        },
        RustMarketEvent::GreeksUpdated(root) => {
            let metadata = market_event_metadata(root.metadata())?;
            let payload = Py::new(py, project_greeks(&metadata, root.greeks())?)?.into_any();
            (metadata, payload)
        },
        RustMarketEvent::RateUpdated(root) => {
            let metadata = market_event_metadata(root.metadata())?;
            let payload = Py::new(py, project_rate(&metadata, root.rate())?)?.into_any();
            (metadata, payload)
        },
        RustMarketEvent::Ticker24hUpdated(root) => {
            let metadata = market_event_metadata(root.metadata())?;
            let payload = Py::new(py, project_ticker(&metadata, root.ticker())?)?.into_any();
            (metadata, payload)
        },
        RustMarketEvent::MarkPriceUpdated(root) => {
            let metadata = market_event_metadata(root.metadata())?;
            let payload = Py::new(py, project_mark(&metadata, root.mark_price())?)?.into_any();
            (metadata, payload)
        },
        RustMarketEvent::FundingRateUpdated(root) => {
            let metadata = market_event_metadata(root.metadata())?;
            let payload = Py::new(py, project_funding(&metadata, root.funding_rate())?)?.into_any();
            (metadata, payload)
        },
        RustMarketEvent::OpenInterestUpdated(root) => {
            let metadata = market_event_metadata(root.metadata())?;
            let payload =
                Py::new(py, project_open_interest(&metadata, root.open_interest())?)?.into_any();
            (metadata, payload)
        },
        RustMarketEvent::IndexPriceUpdated(root) => {
            let metadata = market_event_metadata(root.metadata())?;
            let payload = Py::new(py, project_index(&metadata, root.index_price())?)?.into_any();
            (metadata, payload)
        },
        RustMarketEvent::OrderBookSnapshotReceived(root) => {
            let metadata = market_event_metadata(root.metadata())?;
            let payload = Py::new(py, project_order_book(&metadata, root.snapshot())?)?.into_any();
            (metadata, payload)
        },
        RustMarketEvent::OrderBookDeltaReceived(root) => {
            project!(root, project_delta(root.delta())?)
        },
        RustMarketEvent::OrderBookResyncRequired(root) => {
            project!(root, project_resync(root)?)
        },
    };
    Ok(MarketEvent {
        metadata,
        kind,
        data,
    })
}

#[pyfunction]
fn decode_event(py: Python<'_>, payload: &[u8]) -> PyResult<MarketEvent> {
    let value = kairos_market_contract::decode_event(payload)
        .map_err(|error| MarketInvalidEventError::new_err(error.to_string()))?;
    project_market_event(py, value)
}

#[pyfunction]
fn decode_events(
    py: Python<'_>,
    payloads: Vec<Bound<'_, pyo3::types::PyBytes>>,
) -> PyResult<Vec<MarketEvent>> {
    let mut events = Vec::with_capacity(payloads.len());
    for payload in payloads {
        let value = kairos_market_contract::decode_event(payload.as_bytes())
            .map_err(|error| MarketInvalidEventError::new_err(error.to_string()))?;
        events.push(project_market_event(py, value)?);
    }
    Ok(events)
}

#[pyfunction]
#[pyo3(signature = (root, workspace_id, launch_id=None, instance_id=None))]
fn indexed_environment_path(
    root: PathBuf,
    workspace_id: String,
    launch_id: Option<String>,
    instance_id: Option<String>,
) -> PyResult<PathBuf> {
    let identity = identity(workspace_id, launch_id, instance_id)?;
    kairos_market_contract::market_indexed_environment_path(root, &identity).map_err(contract_error)
}

#[pyfunction]
fn build_info() -> NativeBuildInfo {
    NativeBuildInfo {
        api_version: API_VERSION,
        owner: "Market".to_owned(),
        package_version: env!("CARGO_PKG_VERSION").to_owned(),
        contract_fingerprint: kairos_market_contract::CONTRACT_FINGERPRINT.to_owned(),
    }
}

#[pymodule]
fn _native_market_contract(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module
        .py()
        .get_type::<MarketInvalidInputError>()
        .setattr("code", "invalid_input")?;
    module
        .py()
        .get_type::<MarketInvalidCurrentViewError>()
        .setattr("code", "invalid_wire_data")?;
    module
        .py()
        .get_type::<MarketCurrentViewUnavailableError>()
        .setattr("code", "current_view_unavailable")?;
    module
        .py()
        .get_type::<MarketInvalidEventError>()
        .setattr("code", "invalid_wire_data")?;
    module
        .py()
        .get_type::<MarketControlUnavailableError>()
        .setattr("code", "transport_unavailable")?;
    module
        .py()
        .get_type::<MarketControlRejectedError>()
        .setattr("code", "operation_rejected")?;
    module.add(
        "MARKET_EVENT_STREAM_ID",
        kairos_market_contract::MARKET_EVENTS_STREAM_ID,
    )?;
    module.add(
        "DEFAULT_AERON_CHANNEL",
        kairos_market_contract::DEFAULT_AERON_CHANNEL,
    )?;
    module.add(
        "MarketInvalidInputError",
        module.py().get_type::<MarketInvalidInputError>(),
    )?;
    module.add(
        "MarketInvalidCurrentViewError",
        module.py().get_type::<MarketInvalidCurrentViewError>(),
    )?;
    module.add(
        "MarketCurrentViewUnavailableError",
        module.py().get_type::<MarketCurrentViewUnavailableError>(),
    )?;
    module.add(
        "MarketInvalidEventError",
        module.py().get_type::<MarketInvalidEventError>(),
    )?;
    module.add(
        "MarketControlUnavailableError",
        module.py().get_type::<MarketControlUnavailableError>(),
    )?;
    module.add(
        "MarketControlRejectedError",
        module.py().get_type::<MarketControlRejectedError>(),
    )?;
    module.add_class::<NativeBuildInfo>()?;
    module.add_class::<NativeMarketClient>()?;
    module.add_class::<NativeProvider>()?;
    module.add_class::<NativeObservationRequirement>()?;
    module.add_class::<NativeProviderPreference>()?;
    module.add_class::<NativeOptionRight>()?;
    module.add_class::<NativeExpiryRange>()?;
    module.add_class::<NativeStrikeRange>()?;
    module.add_class::<NativeOptionFilter>()?;
    module.add_class::<NativeOptions>()?;
    module.add_class::<NativeMarketTarget>()?;
    module.add_class::<NativeMarketSubscriptionRequest>()?;
    module.add_class::<NativeMarketHealthResponse>()?;
    module.add_class::<NativeMarketDataRoute>()?;
    module.add_class::<NativeMarketDataRoutesResponse>()?;
    module.add_class::<NativeMarketSubscriptionResponse>()?;
    module.add_class::<NativeMarketSubscriptionSnapshot>()?;
    module.add_class::<NativeMarketSubscriptionsResponse>()?;
    module.add_class::<NativeMarketCommandStatus>()?;
    module.add_class::<NativeMarketReleaseOwnerResponse>()?;
    module.add_class::<NativeMarketControlClient>()?;
    module.add_class::<MarketEventMetadata>()?;
    module.add_class::<MarketTradeEventPayload>()?;
    module.add_class::<MarketOrderBookChange>()?;
    module.add_class::<MarketOrderBookDeltaEventPayload>()?;
    module.add_class::<MarketOrderBookResyncEventPayload>()?;
    module.add_class::<MarketEvent>()?;
    module.add_class::<MarketLiveEventView>()?;
    module.add_class::<MarketLiveSubscription>()?;
    module.add_class::<NativeMarketViewKind>()?;
    module.add_class::<NativeMarketViewKey>()?;
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
    module.add_function(wrap_pyfunction!(decode_event, module)?)?;
    module.add_function(wrap_pyfunction!(decode_events, module)?)?;
    module.add_function(wrap_pyfunction!(indexed_environment_path, module)?)?;
    Ok(())
}
