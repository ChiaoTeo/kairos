use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use kairos_primitives::account::{AccountId, SegmentKey};
use kairos_primitives::decimal::DecimalParts;
use kairos_primitives::reference::{Currency, ExchangeId, InstrumentId};
use kairos_primitives::risk::{MarginRuleCode, PolicyId, ReservationId};
use kairos_primitives::runtime::{
    ActorId, IdempotencyKey, InstanceId, InstanceIdentity, LaunchId, RequestId, StrategyId,
};
use kairos_primitives::time::{BasisPoints, DurationNanos, Generation, Sequence, UnixNanos};
use kairos_protocol::generated::kairos::common::v_2::Decimal64;
use kairos_protocol::{EventMetadataOwned, decode_event_metadata};
use kairos_risk_contract::{
    AdvanceRiskTimeRequest, Allocation, AuthorizeRequest, CircuitScope, CircuitState,
    CloseCircuitRequest, ConsumeReservationRequest, ContractError, DecodedRiskEvent,
    EnforcementMode, Health, Metric, OpenCircuitRequest, PolicyScope, PublishPolicyRequest,
    ReasonCode, ReleaseReservationRequest, Reservation, ReservationStatus,
    ResizeReservationRequest, RiskCommandStatus, RiskContext, RiskControlRpcClient, RiskDecision,
    RiskIndexedView as RustView, RiskPolicy as RustRiskPolicy, TradeRiskProposal,
};
use pyo3::create_exception;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule};

create_exception!(
    _native_risk_contract,
    RiskInvalidCurrentViewError,
    PyValueError
);
create_exception!(
    _native_risk_contract,
    RiskControlUnavailableError,
    PyRuntimeError
);
create_exception!(
    _native_risk_contract,
    RiskControlRejectedError,
    PyRuntimeError
);
create_exception!(_native_risk_contract, RiskInvalidEventError, PyValueError);
create_exception!(_native_risk_contract, RiskInvalidInputError, PyValueError);
create_exception!(
    _native_risk_contract,
    RiskCurrentViewUnavailableError,
    PyRuntimeError
);

const API_VERSION: u32 = 1;

#[pyclass(name = "RiskClient", module = "kairospy._native_risk_contract")]
struct NativeRiskClient {
    control_socket: PathBuf,
    view_root: Option<PathBuf>,
    actor_id: String,
    workspace_id: String,
    launch_id: Option<String>,
    instance_id: Option<String>,
    aeron_dir: Option<String>,
    channel: String,
    stream_id: i32,
    timeout: f64,
}

#[pymethods]
impl NativeRiskClient {
    #[new]
    #[pyo3(signature = (control_socket, *, actor_id, workspace_id, view_root=None, launch_id=None, instance_id=None, aeron_dir=None, channel=None, stream_id=None, timeout=5.0))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        control_socket: PathBuf,
        actor_id: String,
        workspace_id: String,
        view_root: Option<PathBuf>,
        launch_id: Option<String>,
        instance_id: Option<String>,
        aeron_dir: Option<String>,
        channel: Option<String>,
        stream_id: Option<i32>,
        timeout: f64,
    ) -> PyResult<Self> {
        ActorId::new(actor_id.clone())
            .map_err(|error| RiskInvalidInputError::new_err(error.to_string()))?;
        identity(workspace_id.clone(), launch_id.clone(), instance_id.clone())?;
        validate_client_facts(stream_id, timeout)?;
        Ok(Self {
            control_socket,
            view_root,
            actor_id,
            workspace_id,
            launch_id,
            instance_id,
            aeron_dir,
            channel: channel
                .unwrap_or_else(|| kairos_risk_contract::DEFAULT_AERON_CHANNEL.to_owned()),
            stream_id: stream_id.unwrap_or(kairos_risk_contract::RISK_EVENTS_STREAM_ID),
            timeout,
        })
    }

    #[getter]
    fn control(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let module = PyModule::import(py, "kairospy._native_risk_contract")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("timeout", self.timeout)?;
        Ok(module
            .getattr("RiskControlClient")?
            .call((self.control_socket.clone(),), Some(&kwargs))?
            .unbind())
    }

    #[getter]
    fn current(&self, py: Python<'_>) -> PyResult<Option<Py<PyAny>>> {
        let Some(root) = &self.view_root else {
            return Ok(None);
        };
        let module = PyModule::import(py, "kairospy._native_risk_contract")?;
        Ok(Some(
            module
                .getattr("RiskCurrentView")?
                .call1((
                    root.clone(),
                    self.actor_id.clone(),
                    self.workspace_id.clone(),
                    self.launch_id.clone(),
                    self.instance_id.clone(),
                ))?
                .unbind(),
        ))
    }

    #[getter]
    fn events(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let platform = PyModule::import(py, "kairospy.infrastructure.transport.native_event")?;
        let owner = PyModule::import(py, "kairospy._native_risk_contract")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("decoder", owner.getattr("decode_event")?)?;
        kwargs.set_item("aeron_dir", self.aeron_dir.clone())?;
        kwargs.set_item("channel", self.channel.clone())?;
        kwargs.set_item("stream_id", self.stream_id)?;
        Ok(platform
            .getattr("NativeEventSource")?
            .call((), Some(&kwargs))?
            .unbind())
    }
}

fn validate_client_facts(stream_id: Option<i32>, timeout: f64) -> PyResult<()> {
    if !timeout.is_finite() || timeout <= 0.0 {
        return Err(RiskInvalidInputError::new_err(
            "Risk control timeout must be finite and positive",
        ));
    }
    if stream_id.is_some_and(|value| value <= 0) {
        return Err(RiskInvalidInputError::new_err(
            "Risk event stream_id must be positive",
        ));
    }
    Ok(())
}
#[pyclass(frozen, module = "kairospy._native_risk_contract")]
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
#[pyclass(frozen, module = "kairospy._native_risk_contract")]
#[derive(Clone)]
struct RiskEventMetadata {
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
#[pyclass(frozen, module = "kairospy._native_risk_contract")]
struct RiskDecisionEventPayload {
    #[pyo3(get)]
    decision_id: String,
    #[pyo3(get)]
    request_id: String,
    #[pyo3(get)]
    allowed: bool,
    #[pyo3(get)]
    degraded: bool,
    #[pyo3(get)]
    reason_codes: Vec<String>,
    #[pyo3(get)]
    violations: Vec<String>,
}
#[pyclass(frozen, module = "kairospy._native_risk_contract")]
struct RiskReservationEventPayload {
    #[pyo3(get)]
    reservation_id: String,
    #[pyo3(get)]
    request_id: String,
    #[pyo3(get)]
    status: String,
}
#[pyclass(frozen, module = "kairospy._native_risk_contract")]
struct RiskCircuitEventPayload {
    #[pyo3(get)]
    exchange_id: Option<String>,
    #[pyo3(get)]
    state: String,
    #[pyo3(get)]
    reason: Option<String>,
    #[pyo3(get)]
    opened_at_unix_nanos: Option<u64>,
    #[pyo3(get)]
    reset_at_unix_nanos: Option<u64>,
}
#[pymethods]
impl RiskCircuitEventPayload {
    #[getter]
    fn open(&self) -> bool {
        self.state == "open"
    }
}
#[pyclass(frozen, module = "kairospy._native_risk_contract")]
struct RiskEvent {
    #[pyo3(get)]
    metadata: RiskEventMetadata,
    #[pyo3(get)]
    kind: String,
    #[pyo3(get)]
    account_id: Option<String>,
    #[pyo3(get)]
    strategy_id: Option<String>,
    payload: Py<PyAny>,
}
#[pymethods]
impl RiskEvent {
    #[staticmethod]
    #[pyo3(signature = (sequence, account_id, strategy_id, *, reservation_id=None, request_id=None, status="reserved", launch_id=None, instance_id=None))]
    #[allow(clippy::too_many_arguments)]
    fn reservation_changed(
        py: Python<'_>,
        sequence: u64,
        account_id: String,
        strategy_id: String,
        reservation_id: Option<String>,
        request_id: Option<String>,
        status: &str,
        launch_id: Option<String>,
        instance_id: Option<String>,
    ) -> PyResult<Self> {
        AccountId::new(account_id.clone()).map_err(value_error)?;
        StrategyId::new(strategy_id.clone()).map_err(value_error)?;
        let reservation_id = reservation_id.unwrap_or_else(|| format!("reservation-{sequence}"));
        let request_id = request_id.unwrap_or_else(|| format!("request-{sequence}"));
        ReservationId::new(reservation_id.clone()).map_err(value_error)?;
        RequestId::new(request_id.clone()).map_err(value_error)?;
        if !matches!(status, "reserved" | "consumed" | "released" | "expired") {
            return Err(RiskInvalidInputError::new_err(
                "unsupported Risk reservation status",
            ));
        }
        Ok(Self {
            metadata: synthetic_risk_metadata(sequence, launch_id, instance_id, 1)?,
            kind: "reservation_changed".to_owned(),
            account_id: Some(account_id),
            strategy_id: Some(strategy_id),
            payload: Py::new(
                py,
                RiskReservationEventPayload {
                    reservation_id,
                    request_id,
                    status: status.to_owned(),
                },
            )?
            .into_any(),
        })
    }

    #[staticmethod]
    #[pyo3(signature = (sequence, account_id, strategy_id, decision_id, request_id, allowed, *, degraded=false, reason_codes=Vec::new(), violations=Vec::new(), launch_id=None, instance_id=None))]
    #[allow(clippy::too_many_arguments)]
    fn decision_evaluated(
        py: Python<'_>,
        sequence: u64,
        account_id: String,
        strategy_id: String,
        decision_id: String,
        request_id: String,
        allowed: bool,
        degraded: bool,
        reason_codes: Vec<String>,
        violations: Vec<String>,
        launch_id: Option<String>,
        instance_id: Option<String>,
    ) -> PyResult<Self> {
        AccountId::new(account_id.clone()).map_err(value_error)?;
        StrategyId::new(strategy_id.clone()).map_err(value_error)?;
        RequestId::new(request_id.clone()).map_err(value_error)?;
        Ok(Self {
            metadata: synthetic_risk_metadata(sequence, launch_id, instance_id, 1)?,
            kind: "decision_evaluated".to_owned(),
            account_id: Some(account_id),
            strategy_id: Some(strategy_id),
            payload: Py::new(
                py,
                RiskDecisionEventPayload {
                    decision_id,
                    request_id,
                    allowed,
                    degraded,
                    reason_codes,
                    violations,
                },
            )?
            .into_any(),
        })
    }

    #[staticmethod]
    #[pyo3(signature = (sequence, state, *, account_id=None, strategy_id=None, exchange_id=None, reason=None, opened_at_unix_nanos=None, reset_at_unix_nanos=None, launch_id=None, instance_id=None))]
    #[allow(clippy::too_many_arguments)]
    fn circuit_changed(
        py: Python<'_>,
        sequence: u64,
        state: &str,
        account_id: Option<String>,
        strategy_id: Option<String>,
        exchange_id: Option<String>,
        reason: Option<String>,
        opened_at_unix_nanos: Option<u64>,
        reset_at_unix_nanos: Option<u64>,
        launch_id: Option<String>,
        instance_id: Option<String>,
    ) -> PyResult<Self> {
        if !matches!(state, "open" | "closed") {
            return Err(RiskInvalidInputError::new_err(
                "unsupported Risk circuit state",
            ));
        }
        if let Some(value) = &account_id {
            AccountId::new(value.clone()).map_err(value_error)?;
        }
        if let Some(value) = &strategy_id {
            StrategyId::new(value.clone()).map_err(value_error)?;
        }
        if let Some(value) = &exchange_id {
            ExchangeId::new(value.clone()).map_err(value_error)?;
        }
        Ok(Self {
            metadata: synthetic_risk_metadata(sequence, launch_id, instance_id, 1)?,
            kind: "circuit_changed".to_owned(),
            account_id,
            strategy_id,
            payload: Py::new(
                py,
                RiskCircuitEventPayload {
                    exchange_id,
                    state: state.to_owned(),
                    reason,
                    opened_at_unix_nanos,
                    reset_at_unix_nanos,
                },
            )?
            .into_any(),
        })
    }

    #[staticmethod]
    #[pyo3(signature = (sequence, *, launch_id=None, instance_id=None, producer_incarnation=1))]
    fn policy_activated(
        py: Python<'_>,
        sequence: u64,
        launch_id: Option<String>,
        instance_id: Option<String>,
        producer_incarnation: u64,
    ) -> PyResult<Self> {
        Ok(Self {
            metadata: synthetic_risk_metadata(
                sequence,
                launch_id,
                instance_id,
                producer_incarnation,
            )?,
            kind: "policy_activated".to_owned(),
            account_id: None,
            strategy_id: None,
            payload: py.None(),
        })
    }

    #[getter]
    fn payload(&self, py: Python<'_>) -> Py<PyAny> {
        self.payload.clone_ref(py)
    }
    #[getter]
    fn data(&self, py: Python<'_>) -> Py<PyAny> {
        self.payload.clone_ref(py)
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
    fn occurred_at_unix_nanos(&self) -> u64 {
        self.metadata.occurred_at_unix_nanos
    }
    #[getter]
    fn launch_id(&self) -> Option<&str> {
        self.metadata.launch_id.as_deref()
    }
    #[getter]
    fn instance_id(&self) -> Option<&str> {
        self.metadata.instance_id.as_deref()
    }
}
#[pyclass(frozen, module = "kairospy._native_risk_contract")]
#[derive(Clone)]
struct NativeDecimal {
    #[pyo3(get)]
    mantissa: i64,
    #[pyo3(get)]
    scale: u8,
}
#[pymethods]
impl NativeDecimal {
    #[getter]
    fn value(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let decimal = PyModule::import(py, "decimal")?.getattr("Decimal")?;
        let text = if self.scale == 0 {
            self.mantissa.to_string()
        } else {
            let sign = if self.mantissa < 0 { "-" } else { "" };
            let scale = usize::from(self.scale);
            let mut digits = self.mantissa.unsigned_abs().to_string();
            if digits.len() <= scale {
                digits.insert_str(0, &"0".repeat(scale + 1 - digits.len()));
            }
            let split = digits.len() - scale;
            format!("{sign}{}.{}", &digits[..split], &digits[split..])
        };
        Ok(decimal.call1((text,))?.unbind())
    }
}
#[pyclass(frozen, module = "kairospy._native_risk_contract")]
#[derive(Clone)]
struct RiskScope {
    #[pyo3(get)]
    account_id: Option<String>,
    #[pyo3(get)]
    strategy_id: Option<String>,
    #[pyo3(get)]
    instrument_id: Option<String>,
    #[pyo3(get)]
    exchange_id: Option<String>,
}

#[pymethods]
impl RiskScope {
    #[new]
    #[pyo3(signature = (*, account_id=None, strategy_id=None, instrument_id=None, exchange_id=None))]
    fn new(
        account_id: Option<String>,
        strategy_id: Option<String>,
        instrument_id: Option<String>,
        exchange_id: Option<String>,
    ) -> PyResult<Self> {
        let value = Self {
            account_id,
            strategy_id,
            instrument_id,
            exchange_id,
        };
        value.policy_scope()?;
        Ok(value)
    }
}

impl RiskScope {
    fn policy_scope(&self) -> PyResult<PolicyScope> {
        Ok(PolicyScope {
            account_id: self
                .account_id
                .clone()
                .map(AccountId::new)
                .transpose()
                .map_err(value_error)?,
            strategy_id: self
                .strategy_id
                .clone()
                .map(StrategyId::new)
                .transpose()
                .map_err(value_error)?,
            instrument_id: self
                .instrument_id
                .clone()
                .map(InstrumentId::new)
                .transpose()
                .map_err(value_error)?,
            exchange_id: self
                .exchange_id
                .clone()
                .map(ExchangeId::new)
                .transpose()
                .map_err(value_error)?,
        })
    }

    fn circuit_scope(&self) -> PyResult<CircuitScope> {
        if self.instrument_id.is_some() {
            return Err(RiskInvalidInputError::new_err(
                "Risk circuit scope does not accept instrument_id",
            ));
        }
        Ok(CircuitScope {
            account_id: self
                .account_id
                .clone()
                .map(AccountId::new)
                .transpose()
                .map_err(value_error)?,
            strategy_id: self
                .strategy_id
                .clone()
                .map(StrategyId::new)
                .transpose()
                .map_err(value_error)?,
            exchange_id: self
                .exchange_id
                .clone()
                .map(ExchangeId::new)
                .transpose()
                .map_err(value_error)?,
        })
    }
}

#[pyclass(
    name = "TradeRiskProposal",
    frozen,
    module = "kairospy._native_risk_contract"
)]
#[derive(Clone)]
struct NativeTradeRiskProposal {
    inner: TradeRiskProposal,
}

#[pymethods]
impl NativeTradeRiskProposal {
    #[new]
    #[pyo3(signature = (notional, initial_margin_rate_bps, account_segment, collateral_asset, margin_rule_id, *, reduce_only=false))]
    fn new(
        notional: String,
        initial_margin_rate_bps: u64,
        account_segment: String,
        collateral_asset: String,
        margin_rule_id: String,
        reduce_only: bool,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: TradeRiskProposal {
                notional: amount(&notional)?,
                initial_margin_rate_bps: basis_points(initial_margin_rate_bps)?,
                account_segment: SegmentKey::new(account_segment).map_err(value_error)?,
                collateral_asset: Currency::new(collateral_asset).map_err(value_error)?,
                reduce_only,
                margin_rule_id: MarginRuleCode::new(margin_rule_id).map_err(value_error)?,
            },
        })
    }
}

#[pyclass(
    name = "RiskContext",
    frozen,
    module = "kairospy._native_risk_contract"
)]
#[derive(Clone)]
struct NativeRiskContext {
    inner: RiskContext,
}

#[pymethods]
impl NativeRiskContext {
    #[new]
    #[allow(clippy::too_many_arguments)]
    fn new(
        account_snapshot_watermark: u64,
        market_freshness_watermark: u64,
        portfolio_version: u64,
        current_exposure: String,
        current_margin: String,
        available_margin: String,
        current_pnl: String,
        current_drawdown: String,
        market_is_fresh: bool,
        leverage_bps: u64,
        price_deviation_bps: u64,
        stress_loss: String,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: RiskContext {
                account_snapshot_watermark: UnixNanos::new(account_snapshot_watermark),
                market_freshness_watermark: UnixNanos::new(market_freshness_watermark),
                portfolio_version: Generation::new(portfolio_version),
                current_exposure: amount(&current_exposure)?,
                current_margin: amount(&current_margin)?,
                available_margin: amount(&available_margin)?,
                current_pnl: amount(&current_pnl)?,
                current_drawdown: amount(&current_drawdown)?,
                market_is_fresh,
                leverage_bps: basis_points(leverage_bps)?,
                price_deviation_bps: basis_points(price_deviation_bps)?,
                stress_loss: amount(&stress_loss)?,
            },
        })
    }
}

#[pyclass(
    name = "PublishPolicyRequest",
    frozen,
    module = "kairospy._native_risk_contract"
)]
#[derive(Clone)]
struct NativePublishPolicyRequest {
    inner: PublishPolicyRequest,
}

#[pymethods]
impl NativePublishPolicyRequest {
    #[new]
    #[pyo3(signature = (policy_id, version, scope, metric, limit, enforcement, valid_from_unix_nanos, *, valid_until_unix_nanos=None, window_nanos=None))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        policy_id: String,
        version: u64,
        scope: PyRef<'_, RiskScope>,
        metric: String,
        limit: String,
        enforcement: String,
        valid_from_unix_nanos: u64,
        valid_until_unix_nanos: Option<u64>,
        window_nanos: Option<u64>,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: PublishPolicyRequest {
                policy: RustRiskPolicy {
                    policy_id: PolicyId::new(policy_id).map_err(value_error)?,
                    version: Generation::new(version),
                    scope: scope.policy_scope()?,
                    metric: metric_value(&metric)?,
                    limit: amount(&limit)?,
                    enforcement: enforcement_value(&enforcement)?,
                    valid_from_unix_nanos: UnixNanos::new(valid_from_unix_nanos),
                    valid_until_unix_nanos: valid_until_unix_nanos.map(UnixNanos::new),
                    window_nanos: window_nanos.map(DurationNanos::new),
                },
            },
        })
    }
}

#[pyclass(
    name = "AuthorizeRequest",
    frozen,
    module = "kairospy._native_risk_contract"
)]
#[derive(Clone)]
struct NativeAuthorizeRequest {
    inner: AuthorizeRequest,
}

#[pymethods]
impl NativeAuthorizeRequest {
    #[new]
    #[pyo3(signature = (request_id, idempotency_key, reservation_id, account_id, strategy_id, instrument_id, exchange_id, proposal, at_unix_nanos, reservation_ttl_nanos, dependency_generation, dependency_event_sequence, *, context=None))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        request_id: String,
        idempotency_key: String,
        reservation_id: String,
        account_id: String,
        strategy_id: String,
        instrument_id: String,
        exchange_id: String,
        proposal: PyRef<'_, NativeTradeRiskProposal>,
        at_unix_nanos: u64,
        reservation_ttl_nanos: u64,
        dependency_generation: u64,
        dependency_event_sequence: u64,
        context: Option<PyRef<'_, NativeRiskContext>>,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: AuthorizeRequest {
                request_id: RequestId::new(request_id).map_err(value_error)?,
                idempotency_key: IdempotencyKey::new(idempotency_key).map_err(value_error)?,
                reservation_id: ReservationId::new(reservation_id).map_err(value_error)?,
                account_id: AccountId::new(account_id).map_err(value_error)?,
                strategy_id: StrategyId::new(strategy_id).map_err(value_error)?,
                instrument_id: InstrumentId::new(instrument_id).map_err(value_error)?,
                exchange_id: ExchangeId::new(exchange_id).map_err(value_error)?,
                proposal: proposal.inner.clone(),
                at_unix_nanos: UnixNanos::new(at_unix_nanos),
                reservation_ttl_nanos: DurationNanos::new(reservation_ttl_nanos),
                dependency_generation: Generation::new(dependency_generation),
                dependency_event_sequence: Sequence::new(dependency_event_sequence),
                context: context.map(|value| value.inner.clone()),
            },
        })
    }
}

#[pyclass(
    name = "OpenCircuitRequest",
    frozen,
    module = "kairospy._native_risk_contract"
)]
#[derive(Clone)]
struct NativeOpenCircuitRequest {
    inner: OpenCircuitRequest,
}
#[pymethods]
impl NativeOpenCircuitRequest {
    #[new]
    #[pyo3(signature = (scope, at_unix_nanos, reason, *, reset_at_unix_nanos=None))]
    fn new(
        scope: PyRef<'_, RiskScope>,
        at_unix_nanos: u64,
        reason: String,
        reset_at_unix_nanos: Option<u64>,
    ) -> PyResult<Self> {
        if reason.trim().is_empty() {
            return Err(RiskInvalidInputError::new_err(
                "Risk circuit reason is required",
            ));
        }
        Ok(Self {
            inner: OpenCircuitRequest {
                scope: scope.circuit_scope()?,
                at_unix_nanos: UnixNanos::new(at_unix_nanos),
                reset_at_unix_nanos: reset_at_unix_nanos.map(UnixNanos::new),
                reason,
            },
        })
    }
}

#[pyclass(
    name = "CloseCircuitRequest",
    frozen,
    module = "kairospy._native_risk_contract"
)]
#[derive(Clone)]
struct NativeCloseCircuitRequest {
    inner: CloseCircuitRequest,
}
#[pymethods]
impl NativeCloseCircuitRequest {
    #[new]
    fn new(scope: PyRef<'_, RiskScope>, at_unix_nanos: u64) -> PyResult<Self> {
        Ok(Self {
            inner: CloseCircuitRequest {
                scope: scope.circuit_scope()?,
                at_unix_nanos: UnixNanos::new(at_unix_nanos),
            },
        })
    }
}

macro_rules! reservation_request {
    ($name:ident, $pyname:literal, $inner:ty) => {
        #[pyclass(name = $pyname, frozen, module = "kairospy._native_risk_contract")]
        #[derive(Clone)]
        struct $name {
            inner: $inner,
        }
    };
}
reservation_request!(
    NativeReleaseReservationRequest,
    "ReleaseReservationRequest",
    ReleaseReservationRequest
);
reservation_request!(
    NativeConsumeReservationRequest,
    "ConsumeReservationRequest",
    ConsumeReservationRequest
);
reservation_request!(
    NativeResizeReservationRequest,
    "ResizeReservationRequest",
    ResizeReservationRequest
);

#[pymethods]
impl NativeReleaseReservationRequest {
    #[new]
    fn new(reservation_id: String, at_unix_nanos: u64) -> PyResult<Self> {
        Ok(Self {
            inner: ReleaseReservationRequest {
                reservation_id: ReservationId::new(reservation_id).map_err(value_error)?,
                at_unix_nanos: UnixNanos::new(at_unix_nanos),
            },
        })
    }
}
#[pymethods]
impl NativeConsumeReservationRequest {
    #[new]
    fn new(reservation_id: String, at_unix_nanos: u64) -> PyResult<Self> {
        Ok(Self {
            inner: ConsumeReservationRequest {
                reservation_id: ReservationId::new(reservation_id).map_err(value_error)?,
                at_unix_nanos: UnixNanos::new(at_unix_nanos),
            },
        })
    }
}
#[pymethods]
impl NativeResizeReservationRequest {
    #[new]
    fn new(reservation_id: String, amount_value: String, at_unix_nanos: u64) -> PyResult<Self> {
        Ok(Self {
            inner: ResizeReservationRequest {
                reservation_id: ReservationId::new(reservation_id).map_err(value_error)?,
                amount: amount(&amount_value)?,
                at_unix_nanos: UnixNanos::new(at_unix_nanos),
            },
        })
    }
}

#[pyclass(
    name = "AdvanceRiskTimeRequest",
    frozen,
    module = "kairospy._native_risk_contract"
)]
#[derive(Clone, Copy)]
struct NativeAdvanceRiskTimeRequest {
    inner: AdvanceRiskTimeRequest,
}
#[pymethods]
impl NativeAdvanceRiskTimeRequest {
    #[new]
    fn new(event_time_unix_nanos: u64) -> Self {
        Self {
            inner: AdvanceRiskTimeRequest {
                event_time_unix_nanos: UnixNanos::new(event_time_unix_nanos),
            },
        }
    }
}
#[pyclass(frozen, module = "kairospy._native_risk_contract")]
#[derive(Clone)]
struct RiskPolicy {
    #[pyo3(get)]
    policy_id: String,
    #[pyo3(get)]
    version: u64,
    #[pyo3(get)]
    scope: RiskScope,
    #[pyo3(get)]
    metric: String,
    #[pyo3(get)]
    limit: NativeDecimal,
    #[pyo3(get)]
    enforcement: String,
    #[pyo3(get)]
    valid_from_unix_nanos: u64,
    #[pyo3(get)]
    valid_until_unix_nanos: Option<u64>,
    #[pyo3(get)]
    window_nanos: Option<u64>,
}
#[pyclass(frozen, module = "kairospy._native_risk_contract")]
#[derive(Clone)]
struct RiskLimitUsage {
    #[pyo3(get)]
    policy: RiskPolicy,
    #[pyo3(get)]
    used: NativeDecimal,
    #[pyo3(get)]
    reserved: NativeDecimal,
    #[pyo3(get)]
    available: NativeDecimal,
}
#[pyclass(frozen, module = "kairospy._native_risk_contract")]
#[derive(Clone)]
struct RiskAllocation {
    #[pyo3(get)]
    policy_id: String,
    #[pyo3(get)]
    metric: String,
    #[pyo3(get)]
    amount: NativeDecimal,
}
#[pyclass(frozen, module = "kairospy._native_risk_contract")]
#[derive(Clone)]
struct RiskReservation {
    #[pyo3(get)]
    reservation_id: String,
    #[pyo3(get)]
    request_id: String,
    #[pyo3(get)]
    account_id: Option<String>,
    #[pyo3(get)]
    strategy_id: Option<String>,
    #[pyo3(get)]
    idempotency_key: String,
    #[pyo3(get)]
    allocations: Vec<RiskAllocation>,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    created_at_unix_nanos: u64,
    #[pyo3(get)]
    updated_at_unix_nanos: u64,
    #[pyo3(get)]
    expires_at_unix_nanos: u64,
    #[pyo3(get)]
    policy_version: u64,
}
#[pyclass(frozen, module = "kairospy._native_risk_contract")]
#[derive(Clone)]
struct RiskCircuit {
    #[pyo3(get)]
    circuit_id: String,
    #[pyo3(get)]
    scope: RiskScope,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    opened_at_unix_nanos: Option<u64>,
    #[pyo3(get)]
    reset_at_unix_nanos: Option<u64>,
    #[pyo3(get)]
    reason: Option<String>,
}

#[pyclass(name = "RiskHealth", frozen, module = "kairospy._native_risk_contract")]
struct NativeRiskHealth {
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    generation: u64,
    #[pyo3(get)]
    event_sequence: u64,
    #[pyo3(get)]
    policy_version: u64,
    #[pyo3(get)]
    reservation_count: u64,
    #[pyo3(get)]
    open_circuit_count: u64,
}

#[pyclass(
    name = "RiskCommandStatus",
    frozen,
    module = "kairospy._native_risk_contract"
)]
struct NativeRiskCommandStatus {
    #[pyo3(get)]
    status: String,
}

#[pyclass(
    name = "RiskDecision",
    frozen,
    module = "kairospy._native_risk_contract"
)]
struct NativeRiskDecision {
    #[pyo3(get)]
    decision_id: String,
    #[pyo3(get)]
    request_id: String,
    #[pyo3(get)]
    account_id: String,
    #[pyo3(get)]
    strategy_id: String,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    allowed: bool,
    #[pyo3(get)]
    degraded: bool,
    #[pyo3(get)]
    reason_codes: Vec<String>,
    #[pyo3(get)]
    violations: Vec<String>,
    #[pyo3(get)]
    allocations: Vec<RiskAllocation>,
    #[pyo3(get)]
    reservation: Option<RiskReservation>,
    #[pyo3(get)]
    policy_version: u64,
    #[pyo3(get)]
    dependency_generation: u64,
    #[pyo3(get)]
    dependency_event_sequence: u64,
    #[pyo3(get)]
    evaluated_at_unix_nanos: u64,
}

#[pyclass(
    name = "RiskCircuitState",
    frozen,
    module = "kairospy._native_risk_contract"
)]
struct NativeRiskCircuitState {
    #[pyo3(get)]
    scope: RiskScope,
    #[pyo3(get)]
    open: bool,
    #[pyo3(get)]
    opened_at_unix_nanos: Option<u64>,
    #[pyo3(get)]
    reset_at_unix_nanos: Option<u64>,
    #[pyo3(get)]
    reason: String,
}

#[pyclass(
    name = "AdvanceRiskTimeResponse",
    frozen,
    module = "kairospy._native_risk_contract"
)]
struct NativeAdvanceRiskTimeResponse {
    #[pyo3(get)]
    event_time_unix_nanos: u64,
    #[pyo3(get)]
    expired: u64,
}

#[pyclass(name = "RiskControlClient", module = "kairospy._native_risk_contract")]
struct NativeRiskControlClient {
    client: kairos_protocol::ContractClient,
    timeout: Duration,
}

#[pymethods]
impl NativeRiskControlClient {
    #[getter]
    fn socket_path(&self) -> PathBuf {
        self.client.control_socket_path().to_path_buf()
    }

    #[new]
    #[pyo3(signature = (socket_path, *, timeout=5.0))]
    fn new(socket_path: PathBuf, timeout: f64) -> PyResult<Self> {
        if !timeout.is_finite() || timeout <= 0.0 {
            return Err(RiskInvalidInputError::new_err(
                "Risk control timeout must be finite and positive",
            ));
        }
        Ok(Self {
            client: kairos_protocol::ContractClient::control_only(socket_path),
            timeout: Duration::from_secs_f64(timeout),
        })
    }

    fn health(&self, py: Python<'_>) -> PyResult<NativeRiskHealth> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let value: Health = py
            .detach(move || run_control(timeout, async move { client.control().health().await }))?;
        Ok(NativeRiskHealth {
            status: value.status,
            generation: value.generation.get(),
            event_sequence: value.event_sequence.get(),
            policy_version: value.policy_version.get(),
            reservation_count: value.reservation_count,
            open_circuit_count: value.open_circuit_count,
        })
    }

    fn publish_policy(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativePublishPolicyRequest>,
    ) -> PyResult<NativeRiskCommandStatus> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let request = request.inner.clone();
        let value: RiskCommandStatus = py.detach(move || {
            run_control(timeout, async move {
                client.control().publish_policy(request).await
            })
        })?;
        Ok(NativeRiskCommandStatus {
            status: value.status,
        })
    }

    fn authorize_and_reserve(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeAuthorizeRequest>,
    ) -> PyResult<NativeRiskDecision> {
        self.authorize(py, request.inner.clone(), false, false)
    }
    fn pre_trade_check(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeAuthorizeRequest>,
    ) -> PyResult<NativeRiskDecision> {
        self.authorize(py, request.inner.clone(), true, false)
    }
    fn post_trade_check(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeAuthorizeRequest>,
    ) -> PyResult<NativeRiskDecision> {
        self.authorize(py, request.inner.clone(), false, true)
    }

    fn open_circuit(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeOpenCircuitRequest>,
    ) -> PyResult<NativeRiskCircuitState> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let request = request.inner.clone();
        let value: CircuitState = py.detach(move || {
            run_control(timeout, async move {
                client.control().open_circuit(request).await
            })
        })?;
        Ok(project_circuit_state(value))
    }
    fn close_circuit(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeCloseCircuitRequest>,
    ) -> PyResult<NativeRiskCircuitState> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let request = request.inner.clone();
        let value: CircuitState = py.detach(move || {
            run_control(timeout, async move {
                client.control().close_circuit(request).await
            })
        })?;
        Ok(project_circuit_state(value))
    }
    fn resize_reservation(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeResizeReservationRequest>,
    ) -> PyResult<RiskReservation> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let request = request.inner.clone();
        let value: Reservation = py.detach(move || {
            run_control(timeout, async move {
                client.control().resize_reservation(request).await
            })
        })?;
        Ok(project_reservation(value))
    }
    fn release_reservation(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeReleaseReservationRequest>,
    ) -> PyResult<RiskReservation> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let request = request.inner.clone();
        let value: Reservation = py.detach(move || {
            run_control(timeout, async move {
                client.control().release_reservation(request).await
            })
        })?;
        Ok(project_reservation(value))
    }
    fn consume_reservation(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeConsumeReservationRequest>,
    ) -> PyResult<RiskReservation> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let request = request.inner.clone();
        let value: Reservation = py.detach(move || {
            run_control(timeout, async move {
                client.control().consume_reservation(request).await
            })
        })?;
        Ok(project_reservation(value))
    }
    fn advance_time(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeAdvanceRiskTimeRequest>,
    ) -> PyResult<NativeAdvanceRiskTimeResponse> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let request = request.inner;
        let value = py.detach(move || {
            run_control(timeout, async move {
                client.control().advance_time(request).await
            })
        })?;
        Ok(NativeAdvanceRiskTimeResponse {
            event_time_unix_nanos: value.event_time_unix_nanos.get(),
            expired: value.expired,
        })
    }
}

impl NativeRiskControlClient {
    fn authorize(
        &self,
        py: Python<'_>,
        request: AuthorizeRequest,
        pre: bool,
        post: bool,
    ) -> PyResult<NativeRiskDecision> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let value: RiskDecision = py.detach(move || {
            run_control(timeout, async move {
                if pre {
                    client.control().pre_trade_check(request).await
                } else if post {
                    client.control().post_trade_check(request).await
                } else {
                    client.control().authorize_and_reserve(request).await
                }
            })
        })?;
        Ok(project_decision(value))
    }
}
#[pyclass(frozen, module = "kairospy._native_risk_contract")]
struct RiskCurrentSnapshot {
    #[pyo3(get)]
    actor_id: String,
    #[pyo3(get)]
    generation: u64,
    #[pyo3(get)]
    policy_version: u64,
    #[pyo3(get)]
    applied_event_sequence: u64,
    #[pyo3(get)]
    limits: Vec<RiskLimitUsage>,
    #[pyo3(get)]
    reservations: Vec<RiskReservation>,
    #[pyo3(get)]
    circuits: Vec<RiskCircuit>,
}

#[pyclass(module = "kairospy._native_risk_contract")]
struct RiskCurrentView {
    creator_pid: u32,
    root: PathBuf,
    identity: InstanceIdentity,
    actor_id: ActorId,
    path: PathBuf,
    reader: Mutex<Option<RustView>>,
    closed: Mutex<bool>,
}
#[pymethods]
impl RiskCurrentView {
    #[new]
    #[pyo3(signature = (root, actor_id, workspace_id, launch_id=None, instance_id=None))]
    fn new(
        _py: Python<'_>,
        root: PathBuf,
        actor_id: String,
        workspace_id: String,
        launch_id: Option<String>,
        instance_id: Option<String>,
    ) -> PyResult<Self> {
        let identity = identity(workspace_id, launch_id, instance_id)?;
        let actor = ActorId::new(actor_id)
            .map_err(|error| RiskInvalidInputError::new_err(error.to_string()))?;
        let path = kairos_risk_contract::risk_indexed_environment_path(&root, &identity, &actor)
            .map_err(contract_error)?;
        Ok(Self {
            creator_pid: std::process::id(),
            root,
            identity,
            actor_id: actor,
            path,
            reader: Mutex::new(None),
            closed: Mutex::new(false),
        })
    }

    #[getter]
    fn path(&self) -> PathBuf {
        self.path.clone()
    }

    fn snapshot(&self, py: Python<'_>) -> PyResult<RiskCurrentSnapshot> {
        self.read(py, |reader| {
            let snapshot = reader.snapshot().map_err(contract_error)?;
            project(snapshot).map_err(contract_error)
        })
    }
    fn close(&self) -> PyResult<()> {
        self.ensure_process()?;
        *self.closed.lock().map_err(|_| lock_error())? = true;
        self.reader.lock().map_err(|_| lock_error())?.take();
        Ok(())
    }
    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }
    fn __exit__(
        &self,
        _ty: &Bound<'_, PyAny>,
        _value: &Bound<'_, PyAny>,
        _traceback: &Bound<'_, PyAny>,
    ) -> PyResult<bool> {
        self.close()?;
        Ok(false)
    }
}
impl RiskCurrentView {
    fn read<T: Send>(
        &self,
        py: Python<'_>,
        operation: impl FnOnce(&RustView) -> PyResult<T> + Send,
    ) -> PyResult<T> {
        self.ensure_process()?;
        py.detach(|| {
            if *self.closed.lock().map_err(|_| lock_error())? {
                return Err(PyRuntimeError::new_err(
                    "Risk current-view reader is closed",
                ));
            }
            let mut reader = self.reader.lock().map_err(|_| lock_error())?;
            if reader.is_none() {
                *reader = Some(
                    RustView::open(self.root.clone(), &self.identity, self.actor_id.clone())
                        .map_err(contract_error)?,
                );
            }
            operation(reader.as_ref().expect("reader initialized above"))
        })
    }

    fn ensure_process(&self) -> PyResult<()> {
        if std::process::id() != self.creator_pid {
            Err(PyRuntimeError::new_err(
                "Risk current-view reader cannot be used after fork",
            ))
        } else {
            Ok(())
        }
    }
}

fn project(
    snapshot: kairos_risk_contract::RiskIndexedSnapshot,
) -> Result<RiskCurrentSnapshot, ContractError> {
    let applied_event_sequence = snapshot.metadata().applied_event_sequence;
    let state = snapshot.state()?;
    let actor_id = state.actor_id().to_owned();
    let generation = state.generation();
    let policy_version = state.policy_version();
    let mut policies = BTreeMap::new();
    for value in snapshot.policies() {
        let root = value.policy()?;
        let raw = root.policy();
        policies.insert(raw.policy_id().to_owned(), policy(raw));
    }
    let mut limits = Vec::new();
    for value in snapshot.limit_usage() {
        let root = value.limit_usage()?;
        let policy = policies.get(root.policy_id()).cloned().ok_or_else(|| {
            ContractError::Invalid(format!(
                "Risk usage references missing policy {}",
                root.policy_id()
            ))
        })?;
        limits.push(RiskLimitUsage {
            policy,
            used: decimal(root.used()),
            reserved: decimal(root.reserved()),
            available: decimal(root.available()),
        });
    }
    let mut allocations: BTreeMap<String, Vec<RiskAllocation>> = BTreeMap::new();
    for value in snapshot.allocations() {
        let root = value.allocation()?;
        let raw = root.allocation();
        allocations
            .entry(root.reservation_id().to_owned())
            .or_default()
            .push(RiskAllocation {
                policy_id: raw.policy_id().to_owned(),
                metric: metric(raw.metric().0),
                amount: decimal(raw.amount()),
            });
    }
    let mut reservations = Vec::new();
    for value in snapshot.reservations() {
        let root = value.reservation()?;
        let raw = root.reservation();
        reservations.push(RiskReservation {
            reservation_id: raw.reservation_id().to_owned(),
            request_id: raw.request_id().to_owned(),
            account_id: optional(raw.account_id()),
            strategy_id: optional(raw.strategy_id()),
            idempotency_key: raw.idempotency_key().to_owned(),
            allocations: allocations.remove(raw.reservation_id()).unwrap_or_default(),
            status: match raw.status().0 {
                1 => "reserved",
                2 => "consumed",
                3 => "released",
                4 => "expired",
                other => {
                    return Err(ContractError::Invalid(format!(
                        "unknown Risk reservation status {other}"
                    )));
                },
            }
            .to_owned(),
            created_at_unix_nanos: raw.created_at_unix_nanos(),
            updated_at_unix_nanos: raw.updated_at_unix_nanos(),
            expires_at_unix_nanos: raw.expires_at_unix_nanos(),
            policy_version: raw.policy_version(),
        });
    }
    let mut circuits = Vec::new();
    for value in snapshot.circuits() {
        let root = value.circuit()?;
        let raw = root.circuit();
        let scope = raw.scope();
        circuits.push(RiskCircuit {
            circuit_id: raw.circuit_id().to_owned(),
            scope: RiskScope {
                account_id: optional(scope.account_id()),
                strategy_id: optional(scope.strategy_id()),
                instrument_id: None,
                exchange_id: optional(scope.exchange_id()),
            },
            status: match raw.status().0 {
                1 => "closed",
                2 => "open",
                other => {
                    return Err(ContractError::Invalid(format!(
                        "unknown Risk circuit status {other}"
                    )));
                },
            }
            .to_owned(),
            opened_at_unix_nanos: raw.opened_at_unix_nanos(),
            reset_at_unix_nanos: raw.reset_at_unix_nanos(),
            reason: optional(raw.reason()),
        });
    }
    Ok(RiskCurrentSnapshot {
        actor_id,
        generation,
        policy_version,
        applied_event_sequence,
        limits,
        reservations,
        circuits,
    })
}
fn policy(raw: kairos_protocol::generated::kairos::risk::v_2::RiskPolicy<'_>) -> RiskPolicy {
    let scope = raw.scope();
    RiskPolicy {
        policy_id: raw.policy_id().to_owned(),
        version: raw.version(),
        scope: RiskScope {
            account_id: optional(scope.account_id()),
            strategy_id: optional(scope.strategy_id()),
            instrument_id: optional(scope.instrument_id()),
            exchange_id: optional(scope.exchange_id()),
        },
        metric: metric(raw.metric().0),
        limit: decimal(raw.limit()),
        enforcement: match raw.enforcement().0 {
            1 => "reject",
            2 => "warn",
            3 => "observe",
            _ => "unspecified",
        }
        .to_owned(),
        valid_from_unix_nanos: raw.valid_from_unix_nanos(),
        valid_until_unix_nanos: raw.valid_until_unix_nanos(),
        window_nanos: raw.window_nanos(),
    }
}
fn metric(value: u8) -> String {
    match value {
        1 => "notional",
        2 => "margin",
        3 => "gross_exposure",
        4 => "net_exposure",
        5 => "turnover",
        6 => "order_rate",
        7 => "daily_loss",
        8 => "drawdown",
        9 => "leverage",
        10 => "price_deviation",
        11 => "stress_loss",
        _ => "unspecified",
    }
    .to_owned()
}
fn decimal(value: &Decimal64) -> NativeDecimal {
    NativeDecimal {
        mantissa: value.mantissa(),
        scale: value.scale(),
    }
}
fn risk_event_metadata(
    value: kairos_protocol::generated::kairos::common::v_2::EventMetadata<'_>,
) -> PyResult<RiskEventMetadata> {
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
        .map_err(|error| RiskInvalidEventError::new_err(error.to_string()))?;
    Ok(RiskEventMetadata {
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
fn reservation_status(value: u8) -> PyResult<String> {
    match value {
        1 => Ok("reserved"),
        2 => Ok("consumed"),
        3 => Ok("released"),
        4 => Ok("expired"),
        other => Err(RiskInvalidEventError::new_err(format!(
            "unknown Risk reservation status {other}"
        ))),
    }
    .map(str::to_owned)
}
fn reason_code(value: u8) -> PyResult<String> {
    let value = match value {
        1 => "no_matching_policy",
        2 => "limit_exceeded",
        3 => "stale_dependency",
        4 => "duplicate_request",
        5 => "reservation_not_found",
        6 => "reservation_not_active",
        7 => "invalid_request",
        8 => "persistence_failure",
        9 => "circuit_open",
        10 => "stale_market",
        11 => "insufficient_margin",
        12 => "leverage_exceeded",
        13 => "loss_limit_exceeded",
        other => {
            return Err(RiskInvalidEventError::new_err(format!(
                "unknown Risk reason code {other}"
            )));
        },
    };
    Ok(value.to_owned())
}
fn project_event(py: Python<'_>, value: DecodedRiskEvent<'_>) -> PyResult<RiskEvent> {
    macro_rules! payload {
        ($value:expr) => {
            Py::new(py, $value)?.into_any()
        };
    }
    let (metadata, kind, account_id, strategy_id, payload) = match value {
        DecodedRiskEvent::DecisionMade(root) => {
            let value = root.decision();
            let reasons = value.reasons();
            let reason_codes = reasons
                .iter()
                .map(|reason| reason_code(reason.code().0))
                .collect::<PyResult<Vec<_>>>()?;
            let violations = reasons
                .iter()
                .filter_map(|reason| optional(reason.detail()))
                .collect();
            (
                risk_event_metadata(root.metadata())?,
                "decision_evaluated",
                Some(value.account_id().to_owned()),
                Some(value.strategy_id().to_owned()),
                payload!(RiskDecisionEventPayload {
                    decision_id: value.decision_id().to_owned(),
                    request_id: value.request_id().to_owned(),
                    allowed: matches!(value.outcome().0, 1 | 2),
                    degraded: value.outcome().0 == 2,
                    reason_codes,
                    violations,
                }),
            )
        },
        DecodedRiskEvent::ReservationReserved(root) => {
            reservation_event(py, root.metadata(), root.reservation())?
        },
        DecodedRiskEvent::ReservationConsumed(root) => {
            reservation_event(py, root.metadata(), root.reservation())?
        },
        DecodedRiskEvent::ReservationReleased(root) => {
            reservation_event(py, root.metadata(), root.reservation())?
        },
        DecodedRiskEvent::ReservationExpired(root) => {
            reservation_event(py, root.metadata(), root.reservation())?
        },
        DecodedRiskEvent::CircuitOpened(root) => {
            circuit_event(py, root.metadata(), root.circuit(), "open")?
        },
        DecodedRiskEvent::CircuitClosed(root) => {
            circuit_event(py, root.metadata(), root.circuit(), "closed")?
        },
    };
    Ok(RiskEvent {
        metadata,
        kind: kind.to_owned(),
        account_id,
        strategy_id,
        payload,
    })
}
fn reservation_event(
    py: Python<'_>,
    metadata: kairos_protocol::generated::kairos::common::v_2::EventMetadata<'_>,
    value: kairos_protocol::generated::kairos::risk::v_2::Reservation<'_>,
) -> PyResult<(
    RiskEventMetadata,
    &'static str,
    Option<String>,
    Option<String>,
    Py<PyAny>,
)> {
    let payload = RiskReservationEventPayload {
        reservation_id: value.reservation_id().to_owned(),
        request_id: value.request_id().to_owned(),
        status: reservation_status(value.status().0)?,
    };
    Ok((
        risk_event_metadata(metadata)?,
        "reservation_changed",
        Some(value.account_id().to_owned()),
        Some(value.strategy_id().to_owned()),
        Py::new(py, payload)?.into_any(),
    ))
}
fn circuit_event(
    py: Python<'_>,
    metadata: kairos_protocol::generated::kairos::common::v_2::EventMetadata<'_>,
    value: kairos_protocol::generated::kairos::risk::v_2::CircuitState<'_>,
    state: &'static str,
) -> PyResult<(
    RiskEventMetadata,
    &'static str,
    Option<String>,
    Option<String>,
    Py<PyAny>,
)> {
    let scope = value.scope();
    let account_id = optional(scope.account_id());
    let strategy_id = optional(scope.strategy_id());
    let payload = RiskCircuitEventPayload {
        exchange_id: optional(scope.exchange_id()),
        state: state.to_owned(),
        reason: optional(value.reason()),
        opened_at_unix_nanos: value.opened_at_unix_nanos(),
        reset_at_unix_nanos: value.reset_at_unix_nanos(),
    };
    Ok((
        risk_event_metadata(metadata)?,
        "circuit_changed",
        account_id,
        strategy_id,
        Py::new(py, payload)?.into_any(),
    ))
}
#[pyfunction]
fn decode_event(py: Python<'_>, payload: &[u8]) -> PyResult<RiskEvent> {
    let value = kairos_risk_contract::decode_event(payload)
        .map_err(|error| RiskInvalidEventError::new_err(error.to_string()))?;
    project_event(py, value)
}
fn optional(value: Option<&str>) -> Option<String> {
    value
        .filter(|value| !value.trim().is_empty())
        .map(str::to_owned)
}
fn identity(
    workspace_id: String,
    launch_id: Option<String>,
    instance_id: Option<String>,
) -> PyResult<InstanceIdentity> {
    match (launch_id, instance_id) {
        (Some(launch), Some(instance)) => InstanceIdentity::new(workspace_id, launch, instance)
            .map_err(|error| RiskInvalidInputError::new_err(error.to_string())),
        (None, None) => InstanceIdentity::unscoped(workspace_id)
            .map_err(|error| RiskInvalidInputError::new_err(error.to_string())),
        _ => Err(RiskInvalidInputError::new_err(
            "launch_id and instance_id must both be present or both be absent",
        )),
    }
}
fn contract_error(error: ContractError) -> PyErr {
    match error {
        ContractError::Invalid(message) => RiskInvalidCurrentViewError::new_err(message),
        ContractError::Transport(message)
        | ContractError::NotSent(message)
        | ContractError::Indeterminate(message)
        | ContractError::Rejected(message)
        | ContractError::Unsupported(message) => RiskCurrentViewUnavailableError::new_err(message),
    }
}
fn lock_error() -> PyErr {
    PyRuntimeError::new_err("Risk current-view reader lock is poisoned")
}

fn amount(value: &str) -> PyResult<DecimalParts> {
    value.parse().map_err(value_error)
}

fn synthetic_risk_metadata(
    sequence: u64,
    launch_id: Option<String>,
    instance_id: Option<String>,
    producer_incarnation: u64,
) -> PyResult<RiskEventMetadata> {
    if sequence == 0 || producer_incarnation == 0 {
        return Err(RiskInvalidInputError::new_err(
            "Risk event sequence must be positive",
        ));
    }
    let (launch_id, instance_id) = match (launch_id, instance_id) {
        (Some(launch), Some(instance)) => (
            Some(LaunchId::new(launch).map_err(value_error)?.to_string()),
            Some(InstanceId::new(instance).map_err(value_error)?.to_string()),
        ),
        (None, None) => (None, None),
        _ => {
            return Err(RiskInvalidInputError::new_err(
                "launch_id and instance_id must both be present or both be absent",
            ));
        },
    };
    Ok(RiskEventMetadata {
        event_id: format!("risk:event:{sequence}"),
        stream_id: "risk.events".to_owned(),
        sequence,
        producer: "risk".to_owned(),
        producer_incarnation,
        workspace_id: "workspace".to_owned(),
        launch_id,
        instance_id,
        correlation_id: None,
        causation_id: None,
        occurred_at_unix_nanos: sequence,
        published_at_unix_nanos: sequence,
    })
}

fn basis_points(value: u64) -> PyResult<BasisPoints> {
    if value > 10_000 {
        return Err(RiskInvalidInputError::new_err(
            "Risk basis points must be between 0 and 10000",
        ));
    }
    Ok(BasisPoints::new(value))
}

fn metric_value(value: &str) -> PyResult<Metric> {
    match value {
        "notional" => Ok(Metric::Notional),
        "margin" => Ok(Metric::Margin),
        "gross_exposure" => Ok(Metric::GrossExposure),
        "net_exposure" => Ok(Metric::NetExposure),
        "turnover" => Ok(Metric::Turnover),
        "order_rate" => Ok(Metric::OrderRate),
        "daily_loss" => Ok(Metric::DailyLoss),
        "drawdown" => Ok(Metric::Drawdown),
        "leverage" => Ok(Metric::Leverage),
        "price_deviation" => Ok(Metric::PriceDeviation),
        "stress_loss" => Ok(Metric::StressLoss),
        _ => Err(RiskInvalidInputError::new_err(format!(
            "unsupported Risk metric '{value}'"
        ))),
    }
}

fn enforcement_value(value: &str) -> PyResult<EnforcementMode> {
    match value {
        "reject" => Ok(EnforcementMode::Reject),
        "warn" => Ok(EnforcementMode::Warn),
        "observe" => Ok(EnforcementMode::Observe),
        _ => Err(RiskInvalidInputError::new_err(format!(
            "unsupported Risk enforcement '{value}'"
        ))),
    }
}

fn native_amount(value: DecimalParts) -> NativeDecimal {
    NativeDecimal {
        mantissa: value.mantissa(),
        scale: value.scale(),
    }
}

fn project_allocation(value: Allocation) -> RiskAllocation {
    RiskAllocation {
        policy_id: value.policy_id.to_string(),
        metric: value.metric.as_str().to_owned(),
        amount: native_amount(value.amount),
    }
}

fn reservation_status_name(value: ReservationStatus) -> &'static str {
    match value {
        ReservationStatus::Reserved => "reserved",
        ReservationStatus::Consumed => "consumed",
        ReservationStatus::Released => "released",
        ReservationStatus::Expired => "expired",
    }
}

fn project_reservation(value: Reservation) -> RiskReservation {
    RiskReservation {
        reservation_id: value.reservation_id.to_string(),
        request_id: value.request_id.to_string(),
        account_id: value.account_id.map(|item| item.to_string()),
        strategy_id: value.strategy_id.map(|item| item.to_string()),
        idempotency_key: value.idempotency_key.to_string(),
        allocations: value
            .allocations
            .into_iter()
            .map(project_allocation)
            .collect(),
        status: reservation_status_name(value.status).to_owned(),
        created_at_unix_nanos: value.created_at_unix_nanos.get(),
        updated_at_unix_nanos: value.updated_at_unix_nanos.get(),
        expires_at_unix_nanos: value.expires_at_unix_nanos.get(),
        policy_version: value.policy_version.get(),
    }
}

fn reason_code_name(value: ReasonCode) -> &'static str {
    match value {
        ReasonCode::NoMatchingPolicy => "no_matching_policy",
        ReasonCode::LimitExceeded => "limit_exceeded",
        ReasonCode::StaleDependency => "stale_dependency",
        ReasonCode::DuplicateRequest => "duplicate_request",
        ReasonCode::ReservationNotFound => "reservation_not_found",
        ReasonCode::ReservationNotActive => "reservation_not_active",
        ReasonCode::InvalidRequest => "invalid_request",
        ReasonCode::PersistenceFailure => "persistence_failure",
        ReasonCode::CircuitOpen => "circuit_open",
        ReasonCode::StaleMarket => "stale_market",
        ReasonCode::InsufficientMargin => "insufficient_margin",
        ReasonCode::LeverageExceeded => "leverage_exceeded",
        ReasonCode::LossLimitExceeded => "loss_limit_exceeded",
    }
}

fn project_decision(value: RiskDecision) -> NativeRiskDecision {
    NativeRiskDecision {
        decision_id: value.decision_id.to_string(),
        request_id: value.request_id.to_string(),
        account_id: value.account_id.to_string(),
        strategy_id: value.strategy_id.to_string(),
        instrument_id: value.instrument_id.to_string(),
        allowed: value.allowed,
        degraded: value.degraded,
        reason_codes: value
            .reason_codes
            .into_iter()
            .map(|item| reason_code_name(item).to_owned())
            .collect(),
        violations: value.violations,
        allocations: value
            .allocations
            .into_iter()
            .map(project_allocation)
            .collect(),
        reservation: value.reservation.map(project_reservation),
        policy_version: value.policy_version.get(),
        dependency_generation: value.dependency_watermarks.generation.get(),
        dependency_event_sequence: value.dependency_watermarks.event_sequence.get(),
        evaluated_at_unix_nanos: value.evaluated_at_unix_nanos.get(),
    }
}

fn project_circuit_scope(value: CircuitScope) -> RiskScope {
    RiskScope {
        account_id: value.account_id.map(|item| item.to_string()),
        strategy_id: value.strategy_id.map(|item| item.to_string()),
        instrument_id: None,
        exchange_id: value.exchange_id.map(|item| item.to_string()),
    }
}

fn project_circuit_state(value: CircuitState) -> NativeRiskCircuitState {
    NativeRiskCircuitState {
        scope: project_circuit_scope(value.scope),
        open: value.open,
        opened_at_unix_nanos: value.opened_at_unix_nanos.map(|item| item.get()),
        reset_at_unix_nanos: value.reset_at_unix_nanos.map(|item| item.get()),
        reason: value.reason,
    }
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
        .map_err(|error| RiskControlUnavailableError::new_err(error.to_string()))?;
    runtime
        .block_on(async move { tokio::time::timeout(timeout, future).await })
        .map_err(|_| RiskControlUnavailableError::new_err("Risk control request timed out"))?
        .map_err(|error| {
            if kairos_protocol::contract::is_control_rejection(&error) {
                RiskControlRejectedError::new_err(error.to_string())
            } else {
                RiskControlUnavailableError::new_err(error.to_string())
            }
        })
}

fn value_error(error: impl std::fmt::Display) -> PyErr {
    RiskInvalidInputError::new_err(error.to_string())
}
#[pyfunction]
fn build_info() -> NativeBuildInfo {
    NativeBuildInfo {
        api_version: API_VERSION,
        owner: "Risk".to_owned(),
        package_version: env!("CARGO_PKG_VERSION").to_owned(),
        contract_fingerprint: kairos_risk_contract::CONTRACT_FINGERPRINT.to_owned(),
    }
}
#[pyfunction]
#[pyo3(signature = (root, actor_id, workspace_id, launch_id=None, instance_id=None))]
fn indexed_environment_path(
    root: PathBuf,
    actor_id: String,
    workspace_id: String,
    launch_id: Option<String>,
    instance_id: Option<String>,
) -> PyResult<PathBuf> {
    let identity = identity(workspace_id, launch_id, instance_id)?;
    let actor_id = ActorId::new(actor_id)
        .map_err(|error| RiskInvalidInputError::new_err(error.to_string()))?;
    kairos_risk_contract::risk_indexed_environment_path(root, &identity, &actor_id)
        .map_err(contract_error)
}
#[pymodule]
fn _native_risk_contract(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module
        .py()
        .get_type::<RiskInvalidInputError>()
        .setattr("code", "invalid_input")?;
    module
        .py()
        .get_type::<RiskInvalidCurrentViewError>()
        .setattr("code", "invalid_wire_data")?;
    module
        .py()
        .get_type::<RiskCurrentViewUnavailableError>()
        .setattr("code", "current_view_unavailable")?;
    module
        .py()
        .get_type::<RiskInvalidEventError>()
        .setattr("code", "invalid_wire_data")?;
    module
        .py()
        .get_type::<RiskControlUnavailableError>()
        .setattr("code", "transport_unavailable")?;
    module
        .py()
        .get_type::<RiskControlRejectedError>()
        .setattr("code", "operation_rejected")?;
    module.add(
        "RISK_EVENT_STREAM_ID",
        kairos_risk_contract::RISK_EVENTS_STREAM_ID,
    )?;
    module.add(
        "DEFAULT_AERON_CHANNEL",
        kairos_risk_contract::DEFAULT_AERON_CHANNEL,
    )?;
    module.add(
        "RiskInvalidInputError",
        module.py().get_type::<RiskInvalidInputError>(),
    )?;
    module.add(
        "RiskInvalidCurrentViewError",
        module.py().get_type::<RiskInvalidCurrentViewError>(),
    )?;
    module.add(
        "RiskCurrentViewUnavailableError",
        module.py().get_type::<RiskCurrentViewUnavailableError>(),
    )?;
    module.add(
        "RiskInvalidEventError",
        module.py().get_type::<RiskInvalidEventError>(),
    )?;
    module.add(
        "RiskControlUnavailableError",
        module.py().get_type::<RiskControlUnavailableError>(),
    )?;
    module.add(
        "RiskControlRejectedError",
        module.py().get_type::<RiskControlRejectedError>(),
    )?;
    module.add_class::<NativeBuildInfo>()?;
    module.add_class::<NativeRiskClient>()?;
    module.add_class::<NativeTradeRiskProposal>()?;
    module.add_class::<NativeRiskContext>()?;
    module.add_class::<NativePublishPolicyRequest>()?;
    module.add_class::<NativeAuthorizeRequest>()?;
    module.add_class::<NativeOpenCircuitRequest>()?;
    module.add_class::<NativeCloseCircuitRequest>()?;
    module.add_class::<NativeReleaseReservationRequest>()?;
    module.add_class::<NativeConsumeReservationRequest>()?;
    module.add_class::<NativeResizeReservationRequest>()?;
    module.add_class::<NativeAdvanceRiskTimeRequest>()?;
    module.add_class::<NativeRiskHealth>()?;
    module.add_class::<NativeRiskCommandStatus>()?;
    module.add_class::<NativeRiskDecision>()?;
    module.add_class::<NativeRiskCircuitState>()?;
    module.add_class::<NativeAdvanceRiskTimeResponse>()?;
    module.add_class::<NativeRiskControlClient>()?;
    module.add_class::<RiskEventMetadata>()?;
    module.add_class::<RiskDecisionEventPayload>()?;
    module.add_class::<RiskReservationEventPayload>()?;
    module.add_class::<RiskCircuitEventPayload>()?;
    module.add_class::<RiskEvent>()?;
    module.add_class::<NativeDecimal>()?;
    module.add_class::<RiskScope>()?;
    module.add_class::<RiskPolicy>()?;
    module.add_class::<RiskLimitUsage>()?;
    module.add_class::<RiskAllocation>()?;
    module.add_class::<RiskReservation>()?;
    module.add_class::<RiskCircuit>()?;
    module.add_class::<RiskCurrentSnapshot>()?;
    module.add_class::<RiskCurrentView>()?;
    module.add_function(wrap_pyfunction!(build_info, module)?)?;
    module.add_function(wrap_pyfunction!(decode_event, module)?)?;
    module.add_function(wrap_pyfunction!(indexed_environment_path, module)?)?;
    Ok(())
}
