use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use kairos_capital_contract::{
    CAPITAL_ALERTS_DATABASE, CAPITAL_AVAILABILITY_DATABASE, CAPITAL_DEMANDS_DATABASE,
    CAPITAL_FACTS_DATABASE, CAPITAL_OBJECTIVES_DATABASE, CAPITAL_OPERATIONS_DATABASE,
    CAPITAL_PLANS_DATABASE, CAPITAL_POLICIES_DATABASE, CAPITAL_RESERVATIONS_DATABASE,
    CAPITAL_ROUTES_DATABASE, CancelFundingObjectiveRequest, CapitalAvailabilityResponse,
    CapitalControlError, CapitalControlResponse, CapitalControlRpcClient, CapitalDemandResponse,
    CapitalDemandStatus, CapitalHealthResponse, CapitalIndexedView as RustView,
    CapitalPlanReconcileStatus, CapitalReadinessStatus, ContractError, DecodedCapitalEvent,
    FundingLocation as RustFundingLocation, FundingObjectivePriority, FundingObjectiveStatus,
    ObserveCapitalDemandRequest, PublishFundingObjectiveRequest, QueryCapitalAvailabilityRequest,
    ReconcileCapitalPlanRequest, ReconcileCapitalPlanResponse,
};
use kairos_primitives::account::{AccountId, BrokerId, SegmentKey};
use kairos_primitives::capital::{
    CapitalDemandId, CapitalGroupId, CapitalPlanId, FundingObjectiveId,
};
use kairos_primitives::decimal::{DecimalParts, Quantity};
use kairos_primitives::reference::Currency;
use kairos_primitives::runtime::{
    IdempotencyKey, InstanceId, InstanceIdentity, LaunchId, RequestId, StrategyDecisionId,
    StrategyId,
};
use kairos_primitives::time::{BasisPoints, Generation, Sequence, UnixNanos};
use kairos_protocol::generated::kairos::capital::v_2 as fb;
use kairos_protocol::generated::kairos::common::v_2::Decimal64;
use kairos_protocol::{EventMetadataOwned, decode_event_metadata};
use pyo3::create_exception;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule};

create_exception!(
    _native_capital_contract,
    CapitalInvalidCurrentViewError,
    PyValueError
);
create_exception!(
    _native_capital_contract,
    CapitalInvalidEventError,
    PyValueError
);
create_exception!(
    _native_capital_contract,
    CapitalCurrentViewUnavailableError,
    PyRuntimeError
);
create_exception!(
    _native_capital_contract,
    CapitalControlUnavailableError,
    PyRuntimeError
);
create_exception!(
    _native_capital_contract,
    CapitalControlRejectedError,
    PyRuntimeError
);
create_exception!(
    _native_capital_contract,
    CapitalInvalidInputError,
    PyValueError
);

const API_VERSION: u32 = 1;

#[pyclass(name = "CapitalClient", module = "kairospy._native_capital_contract")]
struct NativeCapitalClient {
    control_socket: PathBuf,
    view_root: Option<PathBuf>,
    capital_group_id: String,
    workspace_id: String,
    launch_id: Option<String>,
    instance_id: Option<String>,
    aeron_dir: Option<String>,
    channel: String,
    stream_id: i32,
    timeout: f64,
}

#[pymethods]
impl NativeCapitalClient {
    #[new]
    #[pyo3(signature = (control_socket, *, capital_group_id, workspace_id, view_root=None, launch_id=None, instance_id=None, aeron_dir=None, channel=None, stream_id=None, timeout=5.0))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        control_socket: PathBuf,
        capital_group_id: String,
        workspace_id: String,
        view_root: Option<PathBuf>,
        launch_id: Option<String>,
        instance_id: Option<String>,
        aeron_dir: Option<String>,
        channel: Option<String>,
        stream_id: Option<i32>,
        timeout: f64,
    ) -> PyResult<Self> {
        CapitalGroupId::new(capital_group_id.clone())
            .map_err(|error| CapitalInvalidInputError::new_err(error.to_string()))?;
        identity(workspace_id.clone(), launch_id.clone(), instance_id.clone())?;
        validate_client_facts(stream_id, timeout)?;
        Ok(Self {
            control_socket,
            view_root,
            capital_group_id,
            workspace_id,
            launch_id,
            instance_id,
            aeron_dir,
            channel: channel
                .unwrap_or_else(|| kairos_capital_contract::DEFAULT_AERON_CHANNEL.to_owned()),
            stream_id: stream_id.unwrap_or(kairos_capital_contract::CAPITAL_EVENTS_STREAM_ID),
            timeout,
        })
    }

    #[getter]
    fn control(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let module = PyModule::import(py, "kairospy._native_capital_contract")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("timeout", self.timeout)?;
        Ok(module
            .getattr("CapitalControlClient")?
            .call((self.control_socket.clone(),), Some(&kwargs))?
            .unbind())
    }

    #[getter]
    fn current(&self, py: Python<'_>) -> PyResult<Option<Py<PyAny>>> {
        let Some(root) = &self.view_root else {
            return Ok(None);
        };
        let module = PyModule::import(py, "kairospy._native_capital_contract")?;
        Ok(Some(
            module
                .getattr("CapitalCurrentView")?
                .call1((
                    root.clone(),
                    self.capital_group_id.clone(),
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
        let owner = PyModule::import(py, "kairospy._native_capital_contract")?;
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
        return Err(CapitalInvalidInputError::new_err(
            "Capital control timeout must be finite and positive",
        ));
    }
    if stream_id.is_some_and(|value| value <= 0) {
        return Err(CapitalInvalidInputError::new_err(
            "Capital event stream_id must be positive",
        ));
    }
    Ok(())
}

#[pyclass(frozen, module = "kairospy._native_capital_contract")]
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

#[pyclass(frozen, module = "kairospy._native_capital_contract")]
#[derive(Clone)]
struct CapitalEventMetadata {
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

#[pyclass(frozen, module = "kairospy._native_capital_contract")]
struct CapitalAvailabilityEventPayload {
    #[pyo3(get)]
    availability: Vec<CapitalAvailability>,
}

#[pyclass(frozen, module = "kairospy._native_capital_contract")]
struct CapitalPlanEventPayload {
    #[pyo3(get)]
    plan: CapitalPlan,
    #[pyo3(get)]
    reservation: CapitalReservation,
    #[pyo3(get)]
    operation: Option<CapitalOperation>,
}

#[pyclass(frozen, module = "kairospy._native_capital_contract")]
struct CapitalEvent {
    #[pyo3(get)]
    metadata: CapitalEventMetadata,
    #[pyo3(get)]
    kind: String,
    payload: Py<PyAny>,
}

#[pymethods]
impl CapitalEvent {
    #[getter]
    fn payload(&self, py: Python<'_>) -> Py<PyAny> {
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
#[pyclass(frozen, module = "kairospy._native_capital_contract")]
#[derive(Clone)]
struct FundingLocation {
    #[pyo3(get)]
    broker: String,
    #[pyo3(get)]
    account_id: String,
    #[pyo3(get)]
    segment: String,
    #[pyo3(get)]
    asset: String,
}

#[pymethods]
impl FundingLocation {
    #[new]
    fn new(broker: String, account_id: String, segment: String, asset: String) -> PyResult<Self> {
        let value = rust_funding_location(broker, account_id, segment, asset)?;
        Ok(Self::from_rust(&value))
    }
}

#[pyclass(
    name = "PublishFundingObjectiveRequest",
    frozen,
    module = "kairospy._native_capital_contract"
)]
#[derive(Clone)]
struct NativePublishFundingObjectiveRequest {
    inner: PublishFundingObjectiveRequest,
}

#[pymethods]
impl NativePublishFundingObjectiveRequest {
    #[new]
    #[allow(clippy::too_many_arguments)]
    fn new(
        request_id: String,
        capital_group_id: String,
        objective_id: String,
        version: u64,
        strategy_id: String,
        destination: PyRef<'_, FundingLocation>,
        desired_available: String,
        required_by_unix_nanos: u64,
        expires_at_unix_nanos: u64,
        priority: String,
        confidence_bps: u32,
        strategy_decision_id: String,
        observed_at_unix_nanos: u64,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: PublishFundingObjectiveRequest {
                request_id: RequestId::new(request_id).map_err(value_error)?,
                capital_group_id: CapitalGroupId::new(capital_group_id).map_err(value_error)?,
                objective_id: FundingObjectiveId::new(objective_id).map_err(value_error)?,
                version: Generation::new(version),
                strategy_id: StrategyId::new(strategy_id).map_err(value_error)?,
                destination: destination.to_rust()?,
                desired_available: quantity(&desired_available)?,
                required_by_unix_nanos: UnixNanos::new(required_by_unix_nanos),
                expires_at_unix_nanos: UnixNanos::new(expires_at_unix_nanos),
                priority: funding_priority(&priority)?,
                confidence_bps: basis_points(confidence_bps)?,
                strategy_decision_id: StrategyDecisionId::new(strategy_decision_id)
                    .map_err(value_error)?,
                observed_at_unix_nanos: UnixNanos::new(observed_at_unix_nanos),
            },
        })
    }

    #[getter]
    fn request_id(&self) -> String {
        self.inner.request_id.to_string()
    }
    #[getter]
    fn capital_group_id(&self) -> String {
        self.inner.capital_group_id.to_string()
    }
    #[getter]
    fn objective_id(&self) -> String {
        self.inner.objective_id.to_string()
    }
    #[getter]
    fn version(&self) -> u64 {
        self.inner.version.get()
    }
    #[getter]
    fn strategy_id(&self) -> String {
        self.inner.strategy_id.to_string()
    }
    #[getter]
    fn destination(&self) -> FundingLocation {
        FundingLocation::from_rust(&self.inner.destination)
    }
    #[getter]
    fn desired_available(&self) -> String {
        self.inner.desired_available.to_string()
    }
    #[getter]
    fn required_by_unix_nanos(&self) -> u64 {
        self.inner.required_by_unix_nanos.get()
    }
    #[getter]
    fn expires_at_unix_nanos(&self) -> u64 {
        self.inner.expires_at_unix_nanos.get()
    }
    #[getter]
    fn priority(&self) -> &'static str {
        priority_name(self.inner.priority)
    }
    #[getter]
    fn confidence_bps(&self) -> u64 {
        self.inner.confidence_bps.get()
    }
    #[getter]
    fn strategy_decision_id(&self) -> String {
        self.inner.strategy_decision_id.to_string()
    }
    #[getter]
    fn observed_at_unix_nanos(&self) -> u64 {
        self.inner.observed_at_unix_nanos.get()
    }
}

#[pyclass(
    name = "CancelFundingObjectiveRequest",
    frozen,
    module = "kairospy._native_capital_contract"
)]
#[derive(Clone)]
struct NativeCancelFundingObjectiveRequest {
    inner: CancelFundingObjectiveRequest,
}

#[pymethods]
impl NativeCancelFundingObjectiveRequest {
    #[new]
    fn new(
        request_id: String,
        capital_group_id: String,
        objective_id: String,
        expected_version: u64,
        strategy_id: String,
        observed_at_unix_nanos: u64,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: CancelFundingObjectiveRequest {
                request_id: RequestId::new(request_id).map_err(value_error)?,
                capital_group_id: CapitalGroupId::new(capital_group_id).map_err(value_error)?,
                objective_id: FundingObjectiveId::new(objective_id).map_err(value_error)?,
                expected_version: Generation::new(expected_version),
                strategy_id: StrategyId::new(strategy_id).map_err(value_error)?,
                observed_at_unix_nanos: UnixNanos::new(observed_at_unix_nanos),
            },
        })
    }

    #[getter]
    fn request_id(&self) -> String {
        self.inner.request_id.to_string()
    }
    #[getter]
    fn capital_group_id(&self) -> String {
        self.inner.capital_group_id.to_string()
    }
    #[getter]
    fn objective_id(&self) -> String {
        self.inner.objective_id.to_string()
    }
    #[getter]
    fn expected_version(&self) -> u64 {
        self.inner.expected_version.get()
    }
    #[getter]
    fn strategy_id(&self) -> String {
        self.inner.strategy_id.to_string()
    }
    #[getter]
    fn observed_at_unix_nanos(&self) -> u64 {
        self.inner.observed_at_unix_nanos.get()
    }
}

#[pyclass(
    name = "ObserveCapitalDemandRequest",
    frozen,
    module = "kairospy._native_capital_contract"
)]
#[derive(Clone)]
struct NativeObserveCapitalDemandRequest {
    inner: ObserveCapitalDemandRequest,
}

#[pymethods]
impl NativeObserveCapitalDemandRequest {
    #[new]
    #[allow(clippy::too_many_arguments)]
    fn new(
        request_id: String,
        demand_id: String,
        idempotency_key: String,
        capital_group_id: String,
        strategy_id: String,
        destination: PyRef<'_, FundingLocation>,
        observed_shortfall: String,
        observed_at_unix_nanos: u64,
        required_by_unix_nanos: u64,
        expires_at_unix_nanos: u64,
        priority: String,
        confidence_bps: u32,
        account_watermark: u64,
        risk_watermark: u64,
        launch_id: String,
        instance_id: String,
        destination_lease_fence: String,
        causal_references: Vec<String>,
    ) -> PyResult<Self> {
        if destination_lease_fence.trim().is_empty() {
            return Err(CapitalInvalidInputError::new_err(
                "Capital destination lease fence is required",
            ));
        }
        Ok(Self {
            inner: ObserveCapitalDemandRequest {
                request_id: RequestId::new(request_id).map_err(value_error)?,
                demand_id: CapitalDemandId::new(demand_id).map_err(value_error)?,
                idempotency_key: IdempotencyKey::new(idempotency_key).map_err(value_error)?,
                capital_group_id: CapitalGroupId::new(capital_group_id).map_err(value_error)?,
                strategy_id: StrategyId::new(strategy_id).map_err(value_error)?,
                destination: destination.to_rust()?,
                observed_shortfall: quantity(&observed_shortfall)?,
                observed_at_unix_nanos: UnixNanos::new(observed_at_unix_nanos),
                required_by_unix_nanos: UnixNanos::new(required_by_unix_nanos),
                expires_at_unix_nanos: UnixNanos::new(expires_at_unix_nanos),
                priority: funding_priority(&priority)?,
                confidence_bps: basis_points(confidence_bps)?,
                account_watermark: Sequence::new(account_watermark),
                risk_watermark: Sequence::new(risk_watermark),
                launch_id: LaunchId::new(launch_id).map_err(value_error)?,
                instance_id: InstanceId::new(instance_id).map_err(value_error)?,
                destination_lease_fence,
                causal_references,
            },
        })
    }

    #[getter]
    fn request_id(&self) -> String {
        self.inner.request_id.to_string()
    }
    #[getter]
    fn demand_id(&self) -> String {
        self.inner.demand_id.to_string()
    }
    #[getter]
    fn idempotency_key(&self) -> String {
        self.inner.idempotency_key.to_string()
    }
    #[getter]
    fn capital_group_id(&self) -> String {
        self.inner.capital_group_id.to_string()
    }
    #[getter]
    fn strategy_id(&self) -> String {
        self.inner.strategy_id.to_string()
    }
    #[getter]
    fn destination(&self) -> FundingLocation {
        FundingLocation::from_rust(&self.inner.destination)
    }
    #[getter]
    fn observed_shortfall(&self) -> String {
        self.inner.observed_shortfall.to_string()
    }
    #[getter]
    fn observed_at_unix_nanos(&self) -> u64 {
        self.inner.observed_at_unix_nanos.get()
    }
    #[getter]
    fn required_by_unix_nanos(&self) -> u64 {
        self.inner.required_by_unix_nanos.get()
    }
    #[getter]
    fn expires_at_unix_nanos(&self) -> u64 {
        self.inner.expires_at_unix_nanos.get()
    }
    #[getter]
    fn priority(&self) -> &'static str {
        priority_name(self.inner.priority)
    }
    #[getter]
    fn confidence_bps(&self) -> u64 {
        self.inner.confidence_bps.get()
    }
    #[getter]
    fn account_watermark(&self) -> u64 {
        self.inner.account_watermark.get()
    }
    #[getter]
    fn risk_watermark(&self) -> u64 {
        self.inner.risk_watermark.get()
    }
    #[getter]
    fn launch_id(&self) -> String {
        self.inner.launch_id.to_string()
    }
    #[getter]
    fn instance_id(&self) -> String {
        self.inner.instance_id.to_string()
    }
    #[getter]
    fn destination_lease_fence(&self) -> &str {
        &self.inner.destination_lease_fence
    }
    #[getter]
    fn causal_references(&self) -> Vec<String> {
        self.inner.causal_references.clone()
    }
}

#[pyclass(
    name = "QueryCapitalAvailabilityRequest",
    frozen,
    module = "kairospy._native_capital_contract"
)]
#[derive(Clone)]
struct NativeQueryCapitalAvailabilityRequest {
    inner: QueryCapitalAvailabilityRequest,
}

#[pymethods]
impl NativeQueryCapitalAvailabilityRequest {
    #[new]
    fn new(
        request_id: String,
        capital_group_id: String,
        location: PyRef<'_, FundingLocation>,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: QueryCapitalAvailabilityRequest {
                request_id: RequestId::new(request_id).map_err(value_error)?,
                capital_group_id: CapitalGroupId::new(capital_group_id).map_err(value_error)?,
                location: location.to_rust()?,
            },
        })
    }
}

#[pyclass(
    name = "ReconcileCapitalPlanRequest",
    frozen,
    module = "kairospy._native_capital_contract"
)]
#[derive(Clone)]
struct NativeReconcileCapitalPlanRequest {
    inner: ReconcileCapitalPlanRequest,
}

#[pymethods]
impl NativeReconcileCapitalPlanRequest {
    #[new]
    fn new(
        request_id: String,
        capital_group_id: String,
        plan_id: String,
        observed_at_unix_nanos: u64,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: ReconcileCapitalPlanRequest {
                request_id: RequestId::new(request_id).map_err(value_error)?,
                capital_group_id: CapitalGroupId::new(capital_group_id).map_err(value_error)?,
                plan_id: CapitalPlanId::new(plan_id).map_err(value_error)?,
                observed_at_unix_nanos: UnixNanos::new(observed_at_unix_nanos),
            },
        })
    }
}

#[pyclass(
    name = "CapitalHealth",
    frozen,
    module = "kairospy._native_capital_contract"
)]
struct NativeCapitalHealth {
    #[pyo3(get)]
    status: String,
}

#[pyclass(
    name = "CapitalControlResponse",
    frozen,
    module = "kairospy._native_capital_contract"
)]
struct NativeCapitalControlResponse {
    #[pyo3(get)]
    request_id: String,
    #[pyo3(get)]
    objective_id: String,
    #[pyo3(get)]
    version: u64,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    error_code: Option<String>,
    #[pyo3(get)]
    error_message: Option<String>,
    #[pyo3(get)]
    retryable: bool,
}

#[pyclass(
    name = "CapitalDemandResponse",
    frozen,
    module = "kairospy._native_capital_contract"
)]
struct NativeCapitalDemandResponse {
    #[pyo3(get)]
    request_id: String,
    #[pyo3(get)]
    demand_id: String,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    error_code: Option<String>,
    #[pyo3(get)]
    error_message: Option<String>,
    #[pyo3(get)]
    retryable: bool,
}

#[pyclass(
    name = "CapitalAvailabilityResponse",
    frozen,
    module = "kairospy._native_capital_contract"
)]
struct NativeCapitalAvailabilityResponse {
    #[pyo3(get)]
    request_id: String,
    #[pyo3(get)]
    capital_group_id: String,
    #[pyo3(get)]
    location: FundingLocation,
    #[pyo3(get)]
    readiness: String,
    #[pyo3(get)]
    policy_minimum: String,
    #[pyo3(get)]
    policy_default_target: String,
    #[pyo3(get)]
    policy_maximum: String,
    #[pyo3(get)]
    policy_version: u64,
    #[pyo3(get)]
    active_objective_ids: Vec<String>,
    #[pyo3(get)]
    active_demand_ids: Vec<String>,
    #[pyo3(get)]
    desired_target: String,
    #[pyo3(get)]
    observed_available: String,
    #[pyo3(get)]
    effective_target: String,
    #[pyo3(get)]
    deficit: String,
    #[pyo3(get)]
    account_watermark: u64,
    #[pyo3(get)]
    risk_policy_version: u64,
    #[pyo3(get)]
    risk_watermark: u64,
    #[pyo3(get)]
    evaluated_at_unix_nanos: u64,
    #[pyo3(get)]
    reason: Option<String>,
}

#[pyclass(
    name = "ReconcileCapitalPlanResponse",
    frozen,
    module = "kairospy._native_capital_contract"
)]
struct NativeReconcileCapitalPlanResponse {
    #[pyo3(get)]
    request_id: String,
    #[pyo3(get)]
    plan_id: String,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    error_code: Option<String>,
    #[pyo3(get)]
    error_message: Option<String>,
    #[pyo3(get)]
    retryable: bool,
}

#[pyclass(
    name = "CapitalControlClient",
    module = "kairospy._native_capital_contract"
)]
struct NativeCapitalControlClient {
    client: kairos_protocol::ContractClient,
    timeout: Duration,
}

#[pymethods]
impl NativeCapitalControlClient {
    #[getter]
    fn socket_path(&self) -> PathBuf {
        self.client.control_socket_path().to_path_buf()
    }

    #[new]
    #[pyo3(signature = (socket_path, *, timeout=5.0))]
    fn new(socket_path: PathBuf, timeout: f64) -> PyResult<Self> {
        if !timeout.is_finite() || timeout <= 0.0 {
            return Err(CapitalInvalidInputError::new_err(
                "Capital control timeout must be finite and positive",
            ));
        }
        Ok(Self {
            client: kairos_protocol::ContractClient::control_only(socket_path),
            timeout: Duration::from_secs_f64(timeout),
        })
    }

    fn health(&self, py: Python<'_>) -> PyResult<NativeCapitalHealth> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let value: CapitalHealthResponse = py
            .detach(move || run_control(timeout, async move { client.control().health().await }))?;
        Ok(NativeCapitalHealth {
            status: value.status,
        })
    }

    fn publish_funding_objective(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativePublishFundingObjectiveRequest>,
    ) -> PyResult<NativeCapitalControlResponse> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let request = request.inner.clone();
        let value = py.detach(move || {
            run_control(timeout, async move {
                client.control().publish_funding_objective(request).await
            })
        })?;
        Ok(project_control_response(value))
    }

    fn cancel_funding_objective(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeCancelFundingObjectiveRequest>,
    ) -> PyResult<NativeCapitalControlResponse> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let request = request.inner.clone();
        let value = py.detach(move || {
            run_control(timeout, async move {
                client.control().cancel_funding_objective(request).await
            })
        })?;
        Ok(project_control_response(value))
    }

    fn observe_capital_demand(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeObserveCapitalDemandRequest>,
    ) -> PyResult<NativeCapitalDemandResponse> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let request = request.inner.clone();
        let value = py.detach(move || {
            run_control(timeout, async move {
                client.control().observe_capital_demand(request).await
            })
        })?;
        Ok(project_demand_response(value))
    }

    fn query_capital_availability(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeQueryCapitalAvailabilityRequest>,
    ) -> PyResult<NativeCapitalAvailabilityResponse> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let request = request.inner.clone();
        let value = py.detach(move || {
            run_control(timeout, async move {
                client.control().query_capital_availability(request).await
            })
        })?;
        Ok(project_availability_response(value))
    }

    fn reconcile_capital_plan(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeReconcileCapitalPlanRequest>,
    ) -> PyResult<NativeReconcileCapitalPlanResponse> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let request = request.inner.clone();
        let value = py.detach(move || {
            run_control(timeout, async move {
                client.control().reconcile_capital_plan(request).await
            })
        })?;
        Ok(project_reconcile_response(value))
    }
}

impl FundingLocation {
    fn from_rust(value: &RustFundingLocation) -> Self {
        Self {
            broker: value.broker.to_string(),
            account_id: value.account_id.to_string(),
            segment: value.segment.to_string(),
            asset: value.asset.to_string(),
        }
    }

    fn to_rust(&self) -> PyResult<RustFundingLocation> {
        rust_funding_location(
            self.broker.clone(),
            self.account_id.clone(),
            self.segment.clone(),
            self.asset.clone(),
        )
    }
}
#[pyclass(frozen, module = "kairospy._native_capital_contract")]
#[derive(Clone)]
struct FundingHorizon {
    #[pyo3(get)]
    required_by_unix_nanos: u64,
    #[pyo3(get)]
    objective_ids: Vec<String>,
    #[pyo3(get)]
    demand_ids: Vec<String>,
    #[pyo3(get)]
    desired_available: String,
}
#[pyclass(frozen, module = "kairospy._native_capital_contract")]
#[derive(Clone)]
struct CapitalAvailability {
    #[pyo3(get)]
    readiness: String,
    #[pyo3(get)]
    location: FundingLocation,
    #[pyo3(get)]
    policy_version: u64,
    #[pyo3(get)]
    active_objective_ids: Vec<String>,
    #[pyo3(get)]
    active_demand_ids: Vec<String>,
    #[pyo3(get)]
    funding_horizons: Vec<FundingHorizon>,
    #[pyo3(get)]
    desired_target: String,
    #[pyo3(get)]
    observed_available: String,
    #[pyo3(get)]
    effective_target: String,
    #[pyo3(get)]
    deficit: String,
    #[pyo3(get)]
    account_watermark: u64,
    #[pyo3(get)]
    risk_policy_version: u64,
    #[pyo3(get)]
    risk_watermark: u64,
    #[pyo3(get)]
    reason: Option<String>,
}
#[pyclass(frozen, module = "kairospy._native_capital_contract")]
#[derive(Clone)]
struct FundingObjective {
    #[pyo3(get)]
    objective_id: String,
    #[pyo3(get)]
    version: u64,
    #[pyo3(get)]
    strategy_id: String,
    #[pyo3(get)]
    destination: FundingLocation,
    #[pyo3(get)]
    desired_available: String,
    #[pyo3(get)]
    required_by_unix_nanos: u64,
    #[pyo3(get)]
    expires_at_unix_nanos: u64,
    #[pyo3(get)]
    priority: String,
    #[pyo3(get)]
    confidence_bps: u32,
    #[pyo3(get)]
    strategy_decision_id: String,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    updated_at_unix_nanos: u64,
}
#[pyclass(frozen, module = "kairospy._native_capital_contract")]
#[derive(Clone)]
struct CapitalDemand {
    #[pyo3(get)]
    demand_id: String,
    #[pyo3(get)]
    idempotency_key: String,
    #[pyo3(get)]
    strategy_id: String,
    #[pyo3(get)]
    destination: FundingLocation,
    #[pyo3(get)]
    observed_shortfall: String,
    #[pyo3(get)]
    observed_at_unix_nanos: u64,
    #[pyo3(get)]
    required_by_unix_nanos: u64,
    #[pyo3(get)]
    expires_at_unix_nanos: u64,
    #[pyo3(get)]
    priority: String,
    #[pyo3(get)]
    confidence_bps: u32,
    #[pyo3(get)]
    account_watermark: u64,
    #[pyo3(get)]
    risk_watermark: u64,
    #[pyo3(get)]
    launch_id: String,
    #[pyo3(get)]
    instance_id: String,
    #[pyo3(get)]
    causal_references: Vec<String>,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    updated_at_unix_nanos: u64,
}
#[pyclass(frozen, module = "kairospy._native_capital_contract")]
#[derive(Clone)]
struct CapitalPolicy {
    #[pyo3(get)]
    destination: FundingLocation,
    #[pyo3(get)]
    version: u64,
    #[pyo3(get)]
    minimum: String,
    #[pyo3(get)]
    default_target: String,
    #[pyo3(get)]
    maximum: String,
    #[pyo3(get)]
    stress_buffer: String,
    #[pyo3(get)]
    minimum_movement: String,
    #[pyo3(get)]
    hysteresis: String,
    #[pyo3(get)]
    deficit_dwell_nanos: u64,
    #[pyo3(get)]
    cooldown_nanos: u64,
    #[pyo3(get)]
    max_fact_age_nanos: u64,
}
#[pyclass(frozen, module = "kairospy._native_capital_contract")]
#[derive(Clone)]
struct CapitalEarnHolding {
    #[pyo3(get)]
    product_id: String,
    #[pyo3(get)]
    principal: String,
    #[pyo3(get)]
    redeemable_amount: String,
    #[pyo3(get)]
    immediately_redeemable: bool,
    #[pyo3(get)]
    active: bool,
}
#[pyclass(frozen, module = "kairospy._native_capital_contract")]
#[derive(Clone)]
struct CapitalFacts {
    #[pyo3(get)]
    destination: FundingLocation,
    #[pyo3(get)]
    observed_available: String,
    #[pyo3(get)]
    account_watermark: u64,
    #[pyo3(get)]
    account_observed_at_unix_nanos: u64,
    #[pyo3(get)]
    account_complete: bool,
    #[pyo3(get)]
    risk_capacity: String,
    #[pyo3(get)]
    risk_policy_version: u64,
    #[pyo3(get)]
    risk_watermark: u64,
    #[pyo3(get)]
    earn_holdings: Vec<CapitalEarnHolding>,
}
#[pyclass(frozen, module = "kairospy._native_capital_contract")]
#[derive(Clone)]
struct CapitalPlan {
    #[pyo3(get)]
    plan_id: String,
    #[pyo3(get)]
    rebalance_decision_id: String,
    #[pyo3(get)]
    route_id: String,
    #[pyo3(get)]
    route_version: u64,
    #[pyo3(get)]
    route_kind: String,
    #[pyo3(get)]
    source: FundingLocation,
    #[pyo3(get)]
    destination: FundingLocation,
    #[pyo3(get)]
    amount: String,
    #[pyo3(get)]
    objective_ids: Vec<String>,
    #[pyo3(get)]
    demand_ids: Vec<String>,
    #[pyo3(get)]
    reservation_id: String,
    #[pyo3(get)]
    idempotency_key: String,
    #[pyo3(get)]
    selected_earn_product_id: Option<String>,
    #[pyo3(get)]
    source_account_watermark: u64,
    #[pyo3(get)]
    destination_account_watermark: u64,
    #[pyo3(get)]
    source_observed_available: String,
    #[pyo3(get)]
    destination_observed_available: String,
    #[pyo3(get)]
    redemption_account_watermark: Option<u64>,
    #[pyo3(get)]
    redemption_observed_available: Option<String>,
    #[pyo3(get)]
    earn_principal_before: String,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    recovery_action: String,
    #[pyo3(get)]
    recovery_reason: Option<String>,
    #[pyo3(get)]
    recovery_decided_at_unix_nanos: Option<u64>,
    #[pyo3(get)]
    created_at_unix_nanos: u64,
    #[pyo3(get)]
    expires_at_unix_nanos: u64,
}
#[pyclass(frozen, module = "kairospy._native_capital_contract")]
#[derive(Clone)]
struct CapitalRoute {
    #[pyo3(get)]
    route_id: String,
    #[pyo3(get)]
    version: u64,
    #[pyo3(get)]
    source: FundingLocation,
    #[pyo3(get)]
    destination: FundingLocation,
    #[pyo3(get)]
    kind: String,
    #[pyo3(get)]
    per_operation_limit: String,
    #[pyo3(get)]
    daily_limit: String,
    #[pyo3(get)]
    required_source_authority: String,
    #[pyo3(get)]
    settlement_class: String,
    #[pyo3(get)]
    enabled: bool,
    #[pyo3(get)]
    earn_product_id: Option<String>,
    #[pyo3(get)]
    demand_guard_nanos: u64,
    #[pyo3(get)]
    allow_unknown_redemption_quota: bool,
}
#[pyclass(frozen, module = "kairospy._native_capital_contract")]
#[derive(Clone)]
struct CapitalReservation {
    #[pyo3(get)]
    reservation_id: String,
    #[pyo3(get)]
    plan_id: String,
    #[pyo3(get)]
    source: FundingLocation,
    #[pyo3(get)]
    amount: String,
    #[pyo3(get)]
    source_account_watermark: u64,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    created_at_unix_nanos: u64,
    #[pyo3(get)]
    expires_at_unix_nanos: u64,
}
#[pyclass(frozen, module = "kairospy._native_capital_contract")]
#[derive(Clone)]
struct CapitalOperation {
    #[pyo3(get)]
    operation_id: String,
    #[pyo3(get)]
    plan_id: String,
    #[pyo3(get)]
    idempotency_key: String,
    #[pyo3(get)]
    operation_index: u32,
    #[pyo3(get)]
    kind: String,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    participant_operation_id: Option<String>,
    #[pyo3(get)]
    participant_state: Option<String>,
    #[pyo3(get)]
    dispatch_started_at_unix_nanos: Option<u64>,
    #[pyo3(get)]
    attempt_count: u32,
    #[pyo3(get)]
    failure_reason: Option<String>,
    #[pyo3(get)]
    account_observation_watermark: Option<u64>,
    #[pyo3(get)]
    updated_at_unix_nanos: u64,
}
#[pyclass(frozen, module = "kairospy._native_capital_contract")]
#[derive(Clone)]
struct CapitalAlert {
    #[pyo3(get)]
    alert_id: String,
    #[pyo3(get)]
    plan_id: String,
    #[pyo3(get)]
    operation_id: Option<String>,
    #[pyo3(get)]
    kind: String,
    #[pyo3(get)]
    severity: String,
    #[pyo3(get)]
    recovery_action: String,
    #[pyo3(get)]
    message: String,
    #[pyo3(get)]
    opened_at_unix_nanos: u64,
}
#[pyclass(frozen, module = "kairospy._native_capital_contract")]
struct CapitalCurrentSnapshot {
    #[pyo3(get)]
    capital_group_id: String,
    #[pyo3(get)]
    applied_event_sequence: u64,
    #[pyo3(get)]
    strategy_id: Option<String>,
    #[pyo3(get)]
    environment: Option<String>,
    #[pyo3(get)]
    membership_version: u64,
    #[pyo3(get)]
    event_sequence: u64,
    #[pyo3(get)]
    journal_sequence: u64,
    #[pyo3(get)]
    policy_count: usize,
    #[pyo3(get)]
    facts_count: usize,
    #[pyo3(get)]
    policies: Vec<CapitalPolicy>,
    #[pyo3(get)]
    facts: Vec<CapitalFacts>,
    #[pyo3(get)]
    availabilities: Vec<CapitalAvailability>,
    #[pyo3(get)]
    objectives: Vec<FundingObjective>,
    #[pyo3(get)]
    demands: Vec<CapitalDemand>,
    #[pyo3(get)]
    plans: Vec<CapitalPlan>,
    #[pyo3(get)]
    routes: Vec<CapitalRoute>,
    #[pyo3(get)]
    reservations: Vec<CapitalReservation>,
    #[pyo3(get)]
    operations: Vec<CapitalOperation>,
    #[pyo3(get)]
    alerts: Vec<CapitalAlert>,
}

#[pymethods]
impl CapitalCurrentSnapshot {
    #[pyo3(signature = (location=None))]
    fn availability(
        &self,
        location: Option<(String, String, String, String)>,
    ) -> PyResult<CapitalAvailability> {
        if let Some((broker, account_id, segment, asset)) = location {
            return self
                .availabilities
                .iter()
                .find(|value| {
                    value.location.broker == broker
                        && value.location.account_id == account_id
                        && value.location.segment == segment
                        && value.location.asset == asset
                })
                .cloned()
                .ok_or_else(|| {
                    CapitalInvalidInputError::new_err("Capital location has not been evaluated")
                });
        }
        match self.availabilities.as_slice() {
            [value] => Ok(value.clone()),
            _ => Err(CapitalInvalidInputError::new_err(
                "Capital location is required when the group has multiple locations",
            )),
        }
    }
}

#[pyclass(module = "kairospy._native_capital_contract")]
struct CapitalCurrentView {
    creator_pid: u32,
    root: PathBuf,
    identity: InstanceIdentity,
    group: CapitalGroupId,
    path: PathBuf,
    reader: Mutex<Option<RustView>>,
    closed: Mutex<bool>,
}
#[pymethods]
impl CapitalCurrentView {
    #[new]
    #[pyo3(signature = (root, capital_group_id, workspace_id, launch_id=None, instance_id=None))]
    fn new(
        _py: Python<'_>,
        root: PathBuf,
        capital_group_id: String,
        workspace_id: String,
        launch_id: Option<String>,
        instance_id: Option<String>,
    ) -> PyResult<Self> {
        let identity = identity(workspace_id, launch_id, instance_id)?;
        let group = CapitalGroupId::new(capital_group_id)
            .map_err(|error| CapitalInvalidInputError::new_err(error.to_string()))?;
        let path =
            kairos_capital_contract::capital_indexed_environment_path(&root, &identity, &group)
                .map_err(contract_error)?;
        Ok(Self {
            creator_pid: std::process::id(),
            root,
            identity,
            group,
            path,
            reader: Mutex::new(None),
            closed: Mutex::new(false),
        })
    }

    #[getter]
    fn path(&self) -> PathBuf {
        self.path.clone()
    }

    fn snapshot(&self, py: Python<'_>) -> PyResult<CapitalCurrentSnapshot> {
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
impl CapitalCurrentView {
    fn read<T: Send>(
        &self,
        py: Python<'_>,
        operation: impl FnOnce(&RustView) -> PyResult<T> + Send,
    ) -> PyResult<T> {
        self.ensure_process()?;
        py.detach(|| {
            if *self.closed.lock().map_err(|_| lock_error())? {
                return Err(PyRuntimeError::new_err(
                    "Capital current-view reader is closed",
                ));
            }
            let mut reader = self.reader.lock().map_err(|_| lock_error())?;
            if reader.is_none() {
                *reader = Some(
                    RustView::open(self.root.clone(), &self.identity, self.group.clone())
                        .map_err(contract_error)?,
                );
            }
            operation(reader.as_ref().expect("reader initialized above"))
        })
    }

    fn ensure_process(&self) -> PyResult<()> {
        if std::process::id() != self.creator_pid {
            Err(PyRuntimeError::new_err(
                "Capital current-view reader cannot be used after fork",
            ))
        } else {
            Ok(())
        }
    }
}

fn project(
    snapshot: kairos_capital_contract::CapitalIndexedSnapshot,
) -> Result<CapitalCurrentSnapshot, ContractError> {
    let applied_event_sequence = snapshot.metadata().applied_event_sequence;
    let state = snapshot.state()?;
    let capital_group_id = state.capital_group_id().to_owned();
    let strategy_id = optional_required(state.strategy_id());
    let environment = optional_required(state.environment());
    let membership_version = state.membership_version();
    let event_sequence = state.event_sequence();
    let journal_sequence = state.journal_sequence();
    let policies = snapshot
        .entities(CAPITAL_POLICIES_DATABASE)?
        .into_iter()
        .map(|v| policy(v.policy().unwrap()))
        .collect::<Result<Vec<_>, _>>()?;
    let facts = snapshot
        .entities(CAPITAL_FACTS_DATABASE)?
        .into_iter()
        .map(|v| capital_facts(v.facts().unwrap()))
        .collect::<Result<Vec<_>, _>>()?;
    let policy_count = policies.len();
    let facts_count = facts.len();
    let objectives = snapshot
        .entities(CAPITAL_OBJECTIVES_DATABASE)?
        .into_iter()
        .map(|v| objective(v.objective().unwrap()))
        .collect::<Result<_, _>>()?;
    let demands = snapshot
        .entities(CAPITAL_DEMANDS_DATABASE)?
        .into_iter()
        .map(|v| demand(v.demand().unwrap()))
        .collect::<Result<_, _>>()?;
    let availabilities = snapshot
        .entities(CAPITAL_AVAILABILITY_DATABASE)?
        .into_iter()
        .map(|v| availability(v.availability().unwrap()))
        .collect::<Result<_, _>>()?;
    let plans = snapshot
        .entities(CAPITAL_PLANS_DATABASE)?
        .into_iter()
        .map(|v| plan(v.plan().unwrap()))
        .collect::<Result<_, _>>()?;
    let routes = snapshot
        .entities(CAPITAL_ROUTES_DATABASE)?
        .into_iter()
        .map(|v| route(v.route().unwrap()))
        .collect::<Result<_, _>>()?;
    let reservations = snapshot
        .entities(CAPITAL_RESERVATIONS_DATABASE)?
        .into_iter()
        .map(|v| reservation(v.reservation().unwrap()))
        .collect::<Result<_, _>>()?;
    let operations = snapshot
        .entities(CAPITAL_OPERATIONS_DATABASE)?
        .into_iter()
        .map(|v| operation(v.operation().unwrap()))
        .collect::<Result<_, _>>()?;
    let alerts = snapshot
        .entities(CAPITAL_ALERTS_DATABASE)?
        .into_iter()
        .map(|v| alert(v.alert().unwrap()))
        .collect::<Result<_, _>>()?;
    Ok(CapitalCurrentSnapshot {
        capital_group_id,
        applied_event_sequence,
        strategy_id,
        environment,
        membership_version,
        event_sequence,
        journal_sequence,
        policy_count,
        facts_count,
        policies,
        facts,
        availabilities,
        objectives,
        demands,
        plans,
        routes,
        reservations,
        operations,
        alerts,
    })
}

fn location(v: fb::FundingLocation<'_>) -> FundingLocation {
    FundingLocation {
        broker: v.broker().to_owned(),
        account_id: v.account_id().to_owned(),
        segment: v.segment().to_owned(),
        asset: v.asset().to_owned(),
    }
}
fn strings(v: flatbuffers::Vector<'_, flatbuffers::ForwardsUOffset<&str>>) -> Vec<String> {
    v.iter().map(str::to_owned).collect()
}
fn decimal(v: &Decimal64) -> Result<String, ContractError> {
    DecimalParts::new(v.mantissa(), v.scale())
        .map(|v| v.to_string())
        .map_err(|e| ContractError::Invalid(e.to_string()))
}
fn optional(v: Option<&str>) -> Option<String> {
    v.filter(|v| !v.is_empty()).map(str::to_owned)
}
fn optional_required(v: &str) -> Option<String> {
    (!v.is_empty()).then(|| v.to_owned())
}
fn priority(v: u8) -> Result<String, ContractError> {
    enum_name(
        v,
        &["low", "normal", "high", "critical"],
        "funding priority",
    )
}
fn route_kind(v: u8) -> Result<String, ContractError> {
    enum_name(
        v,
        &[
            "internal_transfer",
            "account_transfer",
            "earn_redemption_then_transfer",
            "earn_subscription",
        ],
        "route kind",
    )
}
fn enum_name(v: u8, names: &[&str], label: &str) -> Result<String, ContractError> {
    names
        .get(usize::from(v))
        .map(|v| (*v).to_owned())
        .ok_or_else(|| ContractError::Invalid(format!("unknown Capital {label}: {v}")))
}

fn objective(v: fb::FundingObjective<'_>) -> Result<FundingObjective, ContractError> {
    Ok(FundingObjective {
        objective_id: v.objective_id().to_owned(),
        version: v.version(),
        strategy_id: v.strategy_id().to_owned(),
        destination: location(v.destination()),
        desired_available: decimal(v.desired_available())?,
        required_by_unix_nanos: v.required_by_unix_nanos(),
        expires_at_unix_nanos: v.expires_at_unix_nanos(),
        priority: priority(v.priority().0)?,
        confidence_bps: u32::from(v.confidence_bps()),
        strategy_decision_id: v.strategy_decision_id().to_owned(),
        status: enum_name(
            v.status().0,
            &["active", "cancelled", "expired"],
            "objective status",
        )?,
        updated_at_unix_nanos: v.updated_at_unix_nanos(),
    })
}
fn demand(v: fb::CapitalDemand<'_>) -> Result<CapitalDemand, ContractError> {
    Ok(CapitalDemand {
        demand_id: v.demand_id().to_owned(),
        idempotency_key: v.idempotency_key().to_owned(),
        strategy_id: v.strategy_id().to_owned(),
        destination: location(v.destination()),
        observed_shortfall: decimal(v.observed_shortfall())?,
        observed_at_unix_nanos: v.observed_at_unix_nanos(),
        required_by_unix_nanos: v.required_by_unix_nanos(),
        expires_at_unix_nanos: v.expires_at_unix_nanos(),
        priority: priority(v.priority().0)?,
        confidence_bps: u32::from(v.confidence_bps()),
        account_watermark: v.account_watermark(),
        risk_watermark: v.risk_watermark(),
        launch_id: v.launch_id().to_owned(),
        instance_id: v.instance_id().to_owned(),
        causal_references: strings(v.causal_references()),
        status: enum_name(v.status().0, &["active", "expired"], "demand status")?,
        updated_at_unix_nanos: v.updated_at_unix_nanos(),
    })
}
fn policy(v: fb::CapitalPolicy<'_>) -> Result<CapitalPolicy, ContractError> {
    Ok(CapitalPolicy {
        destination: location(v.destination()),
        version: v.version(),
        minimum: decimal(v.minimum())?,
        default_target: decimal(v.default_target())?,
        maximum: decimal(v.maximum())?,
        stress_buffer: decimal(v.stress_buffer())?,
        minimum_movement: decimal(v.minimum_movement())?,
        hysteresis: decimal(v.hysteresis())?,
        deficit_dwell_nanos: v.deficit_dwell_nanos(),
        cooldown_nanos: v.cooldown_nanos(),
        max_fact_age_nanos: v.max_fact_age_nanos(),
    })
}
fn capital_facts(v: fb::CapitalFacts<'_>) -> Result<CapitalFacts, ContractError> {
    let earn_holdings = v
        .earn_holdings()
        .iter()
        .map(|holding| {
            Ok(CapitalEarnHolding {
                product_id: holding.product_id().to_owned(),
                principal: decimal(holding.principal())?,
                redeemable_amount: decimal(holding.redeemable_amount())?,
                immediately_redeemable: holding.immediately_redeemable(),
                active: holding.active(),
            })
        })
        .collect::<Result<_, ContractError>>()?;
    Ok(CapitalFacts {
        destination: location(v.destination()),
        observed_available: decimal(v.observed_available())?,
        account_watermark: v.account_watermark(),
        account_observed_at_unix_nanos: v.account_observed_at_unix_nanos(),
        account_complete: v.account_complete(),
        risk_capacity: decimal(v.risk_capacity())?,
        risk_policy_version: v.risk_policy_version(),
        risk_watermark: v.risk_watermark(),
        earn_holdings,
    })
}
fn availability(v: fb::CapitalAvailability<'_>) -> Result<CapitalAvailability, ContractError> {
    let funding_horizons = v
        .funding_horizons()
        .iter()
        .map(|h| {
            Ok(FundingHorizon {
                required_by_unix_nanos: h.required_by_unix_nanos(),
                objective_ids: strings(h.objective_ids()),
                demand_ids: strings(h.demand_ids()),
                desired_available: decimal(h.desired_available())?,
            })
        })
        .collect::<Result<_, ContractError>>()?;
    Ok(CapitalAvailability {
        readiness: enum_name(
            v.readiness().0,
            &[
                "waiting_for_facts",
                "degraded",
                "ready",
                "waiting_for_accounts",
            ],
            "readiness",
        )?,
        location: location(v.destination()),
        policy_version: v.policy_version(),
        active_objective_ids: strings(v.active_objective_ids()),
        active_demand_ids: strings(v.active_demand_ids()),
        funding_horizons,
        desired_target: decimal(v.desired_target())?,
        observed_available: decimal(v.observed_available())?,
        effective_target: decimal(v.effective_target())?,
        deficit: decimal(v.deficit())?,
        account_watermark: v.account_watermark(),
        risk_policy_version: v.risk_policy_version(),
        risk_watermark: v.risk_watermark(),
        reason: optional(v.reason()),
    })
}
fn plan(v: fb::CapitalPlan<'_>) -> Result<CapitalPlan, ContractError> {
    Ok(CapitalPlan {
        plan_id: v.plan_id().to_owned(),
        rebalance_decision_id: v.rebalance_decision_id().to_owned(),
        route_id: v.route_id().to_owned(),
        route_version: v.route_version(),
        route_kind: route_kind(v.route_kind().0)?,
        source: location(v.source()),
        destination: location(v.destination()),
        amount: decimal(v.amount())?,
        objective_ids: strings(v.objective_ids()),
        demand_ids: strings(v.demand_ids()),
        reservation_id: v.reservation_id().to_owned(),
        idempotency_key: v.idempotency_key().to_owned(),
        selected_earn_product_id: optional(v.selected_earn_product_id()),
        source_account_watermark: v.source_account_watermark(),
        destination_account_watermark: v.destination_account_watermark(),
        source_observed_available: decimal(v.source_observed_available())?,
        destination_observed_available: decimal(v.destination_observed_available())?,
        redemption_account_watermark: v.redemption_account_watermark(),
        redemption_observed_available: v
            .redemption_observed_available()
            .map(decimal)
            .transpose()?,
        earn_principal_before: decimal(v.earn_principal_before())?,
        status: enum_name(
            v.status().0,
            &[
                "authorized",
                "transferring",
                "awaiting_transfer",
                "reconciling",
                "available",
                "completed",
                "indeterminate",
                "rejected",
                "expired",
                "failed",
                "redeeming",
                "awaiting_redemption",
                "subscribing",
                "awaiting_subscription",
            ],
            "plan status",
        )?,
        recovery_action: enum_name(
            v.recovery_action().0,
            &[
                "none",
                "no_compensation_required",
                "reconcile_original_operation",
                "hold_and_review",
            ],
            "recovery action",
        )?,
        recovery_reason: optional(v.recovery_reason()),
        recovery_decided_at_unix_nanos: v.recovery_decided_at_unix_nanos(),
        created_at_unix_nanos: v.created_at_unix_nanos(),
        expires_at_unix_nanos: v.expires_at_unix_nanos(),
    })
}
fn route(v: fb::CapitalRoute<'_>) -> Result<CapitalRoute, ContractError> {
    Ok(CapitalRoute {
        route_id: v.route_id().to_owned(),
        version: v.version(),
        source: location(v.source()),
        destination: location(v.destination()),
        kind: route_kind(v.kind().0)?,
        per_operation_limit: decimal(v.per_operation_limit())?,
        daily_limit: decimal(v.daily_limit())?,
        required_source_authority: v.required_source_authority().to_owned(),
        settlement_class: enum_name(
            v.settlement_class().0,
            &[
                "immediate_book_transfer",
                "participant_history_then_account_observation",
            ],
            "settlement class",
        )?,
        enabled: v.enabled(),
        earn_product_id: optional(v.earn_product_id()),
        demand_guard_nanos: v.demand_guard_nanos(),
        allow_unknown_redemption_quota: v.allow_unknown_redemption_quota(),
    })
}
fn reservation(v: fb::CapitalReservation<'_>) -> Result<CapitalReservation, ContractError> {
    Ok(CapitalReservation {
        reservation_id: v.reservation_id().to_owned(),
        plan_id: v.plan_id().to_owned(),
        source: location(v.source()),
        amount: decimal(v.amount())?,
        source_account_watermark: v.source_account_watermark(),
        status: enum_name(
            v.status().0,
            &["active", "consumed", "released", "expired"],
            "reservation status",
        )?,
        created_at_unix_nanos: v.created_at_unix_nanos(),
        expires_at_unix_nanos: v.expires_at_unix_nanos(),
    })
}
fn operation(v: fb::CapitalOperation<'_>) -> Result<CapitalOperation, ContractError> {
    Ok(CapitalOperation {
        operation_id: v.operation_id().to_owned(),
        plan_id: v.plan_id().to_owned(),
        idempotency_key: v.idempotency_key().to_owned(),
        operation_index: v.operation_index(),
        kind: enum_name(
            v.kind().0,
            &["transfer", "earn_redemption", "earn_subscription"],
            "operation kind",
        )?,
        status: enum_name(
            v.status().0,
            &[
                "prepared",
                "dispatching",
                "awaiting_participant",
                "indeterminate",
                "awaiting_account_observation",
                "settled",
                "expired",
                "rejected",
                "failed",
            ],
            "operation status",
        )?,
        participant_operation_id: optional(v.participant_operation_id()),
        participant_state: optional(v.participant_state()),
        dispatch_started_at_unix_nanos: v.dispatch_started_at_unix_nanos(),
        attempt_count: v.attempt_count(),
        failure_reason: optional(v.failure_reason()),
        account_observation_watermark: v.account_observation_watermark(),
        updated_at_unix_nanos: v.updated_at_unix_nanos(),
    })
}
fn alert(v: fb::CapitalAlert<'_>) -> Result<CapitalAlert, ContractError> {
    Ok(CapitalAlert {
        alert_id: v.alert_id().to_owned(),
        plan_id: v.plan_id().to_owned(),
        operation_id: optional(v.operation_id()),
        kind: enum_name(
            v.kind().0,
            &["reconciliation_required", "manual_review"],
            "alert kind",
        )?,
        severity: enum_name(v.severity().0, &["warning", "critical"], "alert severity")?,
        recovery_action: match v.recovery_action().0 {
            2 => "reconcile_original_operation",
            3 => "hold_and_review",
            x => {
                return Err(ContractError::Invalid(format!(
                    "unknown Capital alert recovery action: {x}"
                )));
            },
        }
        .to_owned(),
        message: v.message().to_owned(),
        opened_at_unix_nanos: v.opened_at_unix_nanos(),
    })
}

fn event_metadata(
    value: kairos_protocol::generated::kairos::common::v_2::EventMetadata<'_>,
) -> PyResult<CapitalEventMetadata> {
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
        .map_err(|error| CapitalInvalidEventError::new_err(error.to_string()))?;
    Ok(CapitalEventMetadata {
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

fn project_event(py: Python<'_>, value: DecodedCapitalEvent<'_>) -> PyResult<CapitalEvent> {
    macro_rules! event_payload {
        ($value:expr) => {
            Py::new(py, $value)?.into_any()
        };
    }
    let (metadata, kind, payload) = match value {
        DecodedCapitalEvent::FundingObjectiveChanged(root) => (
            event_metadata(root.metadata())?,
            "funding_objective_changed",
            event_payload!(objective(root.objective()).map_err(contract_error)?),
        ),
        DecodedCapitalEvent::CapitalDemandChanged(root) => (
            event_metadata(root.metadata())?,
            "capital_demand_changed",
            event_payload!(demand(root.demand()).map_err(contract_error)?),
        ),
        DecodedCapitalEvent::PolicyChanged(root) => (
            event_metadata(root.metadata())?,
            "policy_changed",
            event_payload!(policy(root.policy()).map_err(contract_error)?),
        ),
        DecodedCapitalEvent::FactsObserved(root) => (
            event_metadata(root.metadata())?,
            "facts_observed",
            event_payload!(capital_facts(root.facts()).map_err(contract_error)?),
        ),
        DecodedCapitalEvent::AvailabilityEvaluated(root) => {
            let availability = root
                .availability()
                .iter()
                .map(availability)
                .collect::<Result<Vec<_>, _>>()
                .map_err(contract_error)?;
            (
                event_metadata(root.metadata())?,
                "availability_evaluated",
                event_payload!(CapitalAvailabilityEventPayload { availability }),
            )
        },
        DecodedCapitalEvent::RouteChanged(root) => (
            event_metadata(root.metadata())?,
            "route_changed",
            event_payload!(route(root.route()).map_err(contract_error)?),
        ),
        DecodedCapitalEvent::PlanAuthorized(root) => (
            event_metadata(root.metadata())?,
            "plan_authorized",
            event_payload!(CapitalPlanEventPayload {
                plan: plan(root.plan()).map_err(contract_error)?,
                reservation: reservation(root.reservation()).map_err(contract_error)?,
                operation: None,
            }),
        ),
        DecodedCapitalEvent::PlanStateChanged(root) => (
            event_metadata(root.metadata())?,
            "plan_state_changed",
            event_payload!(CapitalPlanEventPayload {
                plan: plan(root.plan()).map_err(contract_error)?,
                reservation: reservation(root.reservation()).map_err(contract_error)?,
                operation: Some(operation(root.operation()).map_err(contract_error)?),
            }),
        ),
        DecodedCapitalEvent::PlanExpired(root) => (
            event_metadata(root.metadata())?,
            "plan_expired",
            event_payload!(CapitalPlanEventPayload {
                plan: plan(root.plan()).map_err(contract_error)?,
                reservation: reservation(root.reservation()).map_err(contract_error)?,
                operation: root
                    .operation()
                    .map(operation)
                    .transpose()
                    .map_err(contract_error)?,
            }),
        ),
    };
    Ok(CapitalEvent {
        metadata,
        kind: kind.to_owned(),
        payload,
    })
}

#[pyfunction]
fn decode_event(py: Python<'_>, payload: &[u8]) -> PyResult<CapitalEvent> {
    let value = kairos_capital_contract::decode_event(payload)
        .map_err(|error| CapitalInvalidEventError::new_err(error.to_string()))?;
    project_event(py, value)
}

#[pyfunction]
#[pyo3(signature = (root, capital_group_id, workspace_id, launch_id=None, instance_id=None))]
fn indexed_environment_path(
    root: PathBuf,
    capital_group_id: String,
    workspace_id: String,
    launch_id: Option<String>,
    instance_id: Option<String>,
) -> PyResult<PathBuf> {
    let identity = identity(workspace_id, launch_id, instance_id)?;
    let group = CapitalGroupId::new(capital_group_id)
        .map_err(|error| CapitalInvalidInputError::new_err(error.to_string()))?;
    kairos_capital_contract::capital_indexed_environment_path(root, &identity, &group)
        .map_err(contract_error)
}

fn identity(
    workspace_id: String,
    launch_id: Option<String>,
    instance_id: Option<String>,
) -> PyResult<InstanceIdentity> {
    match (launch_id, instance_id) {
        (Some(l), Some(i)) => InstanceIdentity::new(workspace_id, l, i)
            .map_err(|e| CapitalInvalidInputError::new_err(e.to_string())),
        (None, None) => InstanceIdentity::unscoped(workspace_id)
            .map_err(|e| CapitalInvalidInputError::new_err(e.to_string())),
        _ => Err(CapitalInvalidInputError::new_err(
            "launch_id and instance_id must both be present or both be absent",
        )),
    }
}
fn contract_error(e: ContractError) -> PyErr {
    match e {
        ContractError::Invalid(v) => CapitalInvalidCurrentViewError::new_err(v),
        ContractError::Transport(v) => CapitalCurrentViewUnavailableError::new_err(v),
    }
}
fn lock_error() -> PyErr {
    PyRuntimeError::new_err("Capital current-view reader lock is poisoned")
}

fn rust_funding_location(
    broker: String,
    account_id: String,
    segment: String,
    asset: String,
) -> PyResult<RustFundingLocation> {
    Ok(RustFundingLocation {
        broker: BrokerId::new(broker).map_err(value_error)?,
        account_id: AccountId::new(account_id).map_err(value_error)?,
        segment: SegmentKey::new(segment).map_err(value_error)?,
        asset: Currency::new(asset).map_err(value_error)?,
    })
}

fn quantity(value: &str) -> PyResult<Quantity> {
    let parts: DecimalParts = value.parse().map_err(value_error)?;
    Quantity::new(parts.mantissa(), parts.scale()).map_err(value_error)
}

fn funding_priority(value: &str) -> PyResult<FundingObjectivePriority> {
    match value {
        "low" => Ok(FundingObjectivePriority::Low),
        "normal" => Ok(FundingObjectivePriority::Normal),
        "high" => Ok(FundingObjectivePriority::High),
        "critical" => Ok(FundingObjectivePriority::Critical),
        _ => Err(CapitalInvalidInputError::new_err(format!(
            "unsupported Capital funding priority '{value}'"
        ))),
    }
}

fn priority_name(value: FundingObjectivePriority) -> &'static str {
    match value {
        FundingObjectivePriority::Low => "low",
        FundingObjectivePriority::Normal => "normal",
        FundingObjectivePriority::High => "high",
        FundingObjectivePriority::Critical => "critical",
    }
}

fn basis_points(value: u32) -> PyResult<BasisPoints> {
    if value > 10_000 {
        return Err(CapitalInvalidInputError::new_err(
            "Capital confidence_bps must be between 0 and 10000",
        ));
    }
    Ok(BasisPoints::new(u64::from(value)))
}

fn error_parts(error: Option<CapitalControlError>) -> (Option<String>, Option<String>, bool) {
    match error {
        Some(error) => (Some(error.code), Some(error.message), error.retryable),
        None => (None, None, false),
    }
}

fn project_control_response(value: CapitalControlResponse) -> NativeCapitalControlResponse {
    let (error_code, error_message, retryable) = error_parts(value.error);
    NativeCapitalControlResponse {
        request_id: value.request_id.to_string(),
        objective_id: value.objective_id.to_string(),
        version: value.version.get(),
        status: match value.status {
            FundingObjectiveStatus::Accepted => "accepted",
            FundingObjectiveStatus::Duplicate => "duplicate",
            FundingObjectiveStatus::Cancelled => "cancelled",
            FundingObjectiveStatus::Rejected => "rejected",
        }
        .to_owned(),
        error_code,
        error_message,
        retryable,
    }
}

fn project_demand_response(value: CapitalDemandResponse) -> NativeCapitalDemandResponse {
    let (error_code, error_message, retryable) = error_parts(value.error);
    NativeCapitalDemandResponse {
        request_id: value.request_id.to_string(),
        demand_id: value.demand_id.to_string(),
        status: match value.status {
            CapitalDemandStatus::Accepted => "accepted",
            CapitalDemandStatus::Duplicate => "duplicate",
            CapitalDemandStatus::Rejected => "rejected",
        }
        .to_owned(),
        error_code,
        error_message,
        retryable,
    }
}

fn project_availability_response(
    value: CapitalAvailabilityResponse,
) -> NativeCapitalAvailabilityResponse {
    NativeCapitalAvailabilityResponse {
        request_id: value.request_id.to_string(),
        capital_group_id: value.capital_group_id.to_string(),
        location: FundingLocation::from_rust(&value.location),
        readiness: match value.readiness {
            CapitalReadinessStatus::WaitingForFacts => "waiting_for_facts",
            CapitalReadinessStatus::WaitingForAccounts => "waiting_for_accounts",
            CapitalReadinessStatus::Degraded => "degraded",
            CapitalReadinessStatus::Ready => "ready",
        }
        .to_owned(),
        policy_minimum: value.policy_minimum.to_string(),
        policy_default_target: value.policy_default_target.to_string(),
        policy_maximum: value.policy_maximum.to_string(),
        policy_version: value.policy_version.get(),
        active_objective_ids: value
            .active_objective_ids
            .into_iter()
            .map(|item| item.to_string())
            .collect(),
        active_demand_ids: value
            .active_demand_ids
            .into_iter()
            .map(|item| item.to_string())
            .collect(),
        desired_target: value.desired_target.to_string(),
        observed_available: value.observed_available.to_string(),
        effective_target: value.effective_target.to_string(),
        deficit: value.deficit.to_string(),
        account_watermark: value.account_watermark.get(),
        risk_policy_version: value.risk_policy_version.get(),
        risk_watermark: value.risk_watermark.get(),
        evaluated_at_unix_nanos: value.evaluated_at_unix_nanos.get(),
        reason: value.reason,
    }
}

fn project_reconcile_response(
    value: ReconcileCapitalPlanResponse,
) -> NativeReconcileCapitalPlanResponse {
    let (error_code, error_message, retryable) = error_parts(value.error);
    NativeReconcileCapitalPlanResponse {
        request_id: value.request_id.to_string(),
        plan_id: value.plan_id.to_string(),
        status: match value.status {
            CapitalPlanReconcileStatus::Reconciled => "reconciled",
            CapitalPlanReconcileStatus::Unchanged => "unchanged",
            CapitalPlanReconcileStatus::Rejected => "rejected",
        }
        .to_owned(),
        error_code,
        error_message,
        retryable,
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
        .map_err(|error| CapitalControlUnavailableError::new_err(error.to_string()))?;
    runtime
        .block_on(async move { tokio::time::timeout(timeout, future).await })
        .map_err(|_| CapitalControlUnavailableError::new_err("Capital control request timed out"))?
        .map_err(|error| {
            if kairos_protocol::contract::is_control_rejection(&error) {
                CapitalControlRejectedError::new_err(error.to_string())
            } else {
                CapitalControlUnavailableError::new_err(error.to_string())
            }
        })
}

fn value_error(error: impl std::fmt::Display) -> PyErr {
    CapitalInvalidInputError::new_err(error.to_string())
}

#[pyfunction]
fn build_info() -> NativeBuildInfo {
    NativeBuildInfo {
        api_version: API_VERSION,
        owner: "Capital".to_owned(),
        package_version: env!("CARGO_PKG_VERSION").to_owned(),
        contract_fingerprint: kairos_capital_contract::CONTRACT_FINGERPRINT.to_owned(),
    }
}
#[pymodule]
fn _native_capital_contract(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.py()
        .get_type::<CapitalInvalidInputError>()
        .setattr("code", "invalid_input")?;
    m.py()
        .get_type::<CapitalInvalidCurrentViewError>()
        .setattr("code", "invalid_wire_data")?;
    m.py()
        .get_type::<CapitalCurrentViewUnavailableError>()
        .setattr("code", "current_view_unavailable")?;
    m.py()
        .get_type::<CapitalInvalidEventError>()
        .setattr("code", "invalid_wire_data")?;
    m.py()
        .get_type::<CapitalControlUnavailableError>()
        .setattr("code", "transport_unavailable")?;
    m.py()
        .get_type::<CapitalControlRejectedError>()
        .setattr("code", "operation_rejected")?;
    m.add(
        "CAPITAL_EVENT_STREAM_ID",
        kairos_capital_contract::CAPITAL_EVENTS_STREAM_ID,
    )?;
    m.add(
        "DEFAULT_AERON_CHANNEL",
        kairos_capital_contract::DEFAULT_AERON_CHANNEL,
    )?;
    m.add(
        "CapitalInvalidInputError",
        m.py().get_type::<CapitalInvalidInputError>(),
    )?;
    m.add(
        "CapitalInvalidCurrentViewError",
        m.py().get_type::<CapitalInvalidCurrentViewError>(),
    )?;
    m.add(
        "CapitalCurrentViewUnavailableError",
        m.py().get_type::<CapitalCurrentViewUnavailableError>(),
    )?;
    m.add(
        "CapitalInvalidEventError",
        m.py().get_type::<CapitalInvalidEventError>(),
    )?;
    m.add(
        "CapitalControlUnavailableError",
        m.py().get_type::<CapitalControlUnavailableError>(),
    )?;
    m.add(
        "CapitalControlRejectedError",
        m.py().get_type::<CapitalControlRejectedError>(),
    )?;
    m.add_class::<NativeBuildInfo>()?;
    m.add_class::<NativeCapitalClient>()?;
    m.add_class::<CapitalEventMetadata>()?;
    m.add_class::<CapitalAvailabilityEventPayload>()?;
    m.add_class::<CapitalPlanEventPayload>()?;
    m.add_class::<CapitalEvent>()?;
    m.add_class::<FundingLocation>()?;
    m.add_class::<NativePublishFundingObjectiveRequest>()?;
    m.add_class::<NativeCancelFundingObjectiveRequest>()?;
    m.add_class::<NativeObserveCapitalDemandRequest>()?;
    m.add_class::<NativeQueryCapitalAvailabilityRequest>()?;
    m.add_class::<NativeReconcileCapitalPlanRequest>()?;
    m.add_class::<NativeCapitalHealth>()?;
    m.add_class::<NativeCapitalControlResponse>()?;
    m.add_class::<NativeCapitalDemandResponse>()?;
    m.add_class::<NativeCapitalAvailabilityResponse>()?;
    m.add_class::<NativeReconcileCapitalPlanResponse>()?;
    m.add_class::<NativeCapitalControlClient>()?;
    m.add_class::<FundingHorizon>()?;
    m.add_class::<CapitalAvailability>()?;
    m.add_class::<FundingObjective>()?;
    m.add_class::<CapitalDemand>()?;
    m.add_class::<CapitalPolicy>()?;
    m.add_class::<CapitalEarnHolding>()?;
    m.add_class::<CapitalFacts>()?;
    m.add_class::<CapitalPlan>()?;
    m.add_class::<CapitalRoute>()?;
    m.add_class::<CapitalReservation>()?;
    m.add_class::<CapitalOperation>()?;
    m.add_class::<CapitalAlert>()?;
    m.add_class::<CapitalCurrentSnapshot>()?;
    m.add_class::<CapitalCurrentView>()?;
    m.add_function(wrap_pyfunction!(build_info, m)?)?;
    m.add_function(wrap_pyfunction!(decode_event, m)?)?;
    m.add_function(wrap_pyfunction!(indexed_environment_path, m)?)?;
    Ok(())
}
