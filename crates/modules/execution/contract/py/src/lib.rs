use std::path::PathBuf;
use std::sync::Mutex;

use kairos_execution_contract::{
    ContractError, ExecutionEvent as RustExecutionEvent, ExecutionIndexedView as RustView,
};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::generated::kairos::common::v_2::Decimal64;
use kairos_protocol::generated::kairos::execution::v_2 as fb;
use kairos_protocol::{EventMetadataOwned, decode_event_metadata};
use pyo3::create_exception;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule};

mod control;

create_exception!(
    _native_execution_contract,
    ExecutionInvalidCurrentViewError,
    PyValueError
);
create_exception!(
    _native_execution_contract,
    ExecutionInvalidEventError,
    PyValueError
);
create_exception!(
    _native_execution_contract,
    ExecutionCurrentViewUnavailableError,
    PyRuntimeError
);
create_exception!(
    _native_execution_contract,
    ExecutionInvalidInputError,
    PyValueError
);

const API_VERSION: u32 = 1;

#[pyclass(
    name = "ExecutionClient",
    module = "kairospy._native_execution_contract"
)]
struct NativeExecutionClient {
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
impl NativeExecutionClient {
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
        if !timeout.is_finite() || timeout <= 0.0 {
            return Err(ExecutionInvalidInputError::new_err(
                "Execution control timeout must be finite and positive",
            ));
        }
        let stream_id = stream_id.unwrap_or(kairos_execution_contract::EXECUTION_EVENTS_STREAM_ID);
        if stream_id <= 0 {
            return Err(ExecutionInvalidInputError::new_err(
                "Execution event stream_id must be positive",
            ));
        }
        Ok(Self {
            control_socket,
            view_root,
            workspace_id,
            launch_id,
            instance_id,
            aeron_dir,
            channel: channel
                .unwrap_or_else(|| kairos_execution_contract::DEFAULT_AERON_CHANNEL.to_owned()),
            stream_id,
            timeout,
        })
    }

    #[getter]
    fn control(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let module = PyModule::import(py, "kairospy._native_execution_contract")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("timeout", self.timeout)?;
        Ok(module
            .getattr("ExecutionControlClient")?
            .call((self.control_socket.clone(),), Some(&kwargs))?
            .unbind())
    }

    #[getter]
    fn current(&self, py: Python<'_>) -> PyResult<Option<Py<PyAny>>> {
        let Some(root) = &self.view_root else {
            return Ok(None);
        };
        let module = PyModule::import(py, "kairospy._native_execution_contract")?;
        Ok(Some(
            module
                .getattr("ExecutionCurrentView")?
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
        let platform = PyModule::import(py, "kairospy.infrastructure.transport.native_event")?;
        let owner = PyModule::import(py, "kairospy._native_execution_contract")?;
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

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
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

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
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
        Ok(decimal
            .call1((decimal_text(self.mantissa, self.scale),))?
            .unbind())
    }
}

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
#[derive(Clone)]
struct ExecutionEventMetadata {
    #[pyo3(get)]
    event_id: String,
    #[pyo3(get)]
    stream_id: String,
    #[pyo3(get)]
    sequence: u64,
    #[pyo3(get)]
    producer: String,
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

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
struct ExecutionBenchmark {
    #[pyo3(get)]
    kind: String,
    #[pyo3(get)]
    leg_id: Option<String>,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    market_id: String,
    #[pyo3(get)]
    price: NativeDecimal,
    #[pyo3(get)]
    observed_at_unix_nanos: u64,
}

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
struct ExecutionIntentEventValue {
    #[pyo3(get)]
    intent_id: String,
    #[pyo3(get)]
    strategy_decision_id: Option<String>,
    #[pyo3(get)]
    strategy_id: String,
    #[pyo3(get)]
    launch_id: String,
    #[pyo3(get)]
    instance_id: String,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    account_ids: Vec<String>,
    #[pyo3(get)]
    execution_benchmarks: Vec<Py<ExecutionBenchmark>>,
    #[pyo3(get)]
    target_quantity: Option<NativeDecimal>,
    #[pyo3(get)]
    reason: String,
}

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
struct ExecutionIntentUpdate {
    #[pyo3(get)]
    intent: Option<Py<ExecutionIntentEventValue>>,
    #[pyo3(get)]
    intent_id: String,
    #[pyo3(get)]
    strategy_id: String,
    #[pyo3(get)]
    account_ids: Vec<String>,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    previous_status: Option<String>,
    #[pyo3(get)]
    order_ids: Vec<String>,
    #[pyo3(get)]
    completed_quantity: Option<NativeDecimal>,
    #[pyo3(get)]
    reason: String,
}

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
struct ExecutionOrderUpdate {
    #[pyo3(get)]
    order_id: String,
    #[pyo3(get)]
    intent_id: String,
    #[pyo3(get)]
    plan_id: String,
    #[pyo3(get)]
    leg_id: String,
    #[pyo3(get)]
    strategy_id: String,
    #[pyo3(get)]
    account_id: String,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    market_id: String,
    #[pyo3(get)]
    execution_route_id: String,
    #[pyo3(get)]
    side: String,
    #[pyo3(get)]
    quantity: NativeDecimal,
    #[pyo3(get)]
    filled_quantity: NativeDecimal,
    #[pyo3(get)]
    limit_price: Option<NativeDecimal>,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    updated_at_unix_nanos: u64,
    #[pyo3(get)]
    reason: String,
}

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
struct ExecutionFillValue {
    #[pyo3(get)]
    fill_id: String,
    #[pyo3(get)]
    trade_id: Option<String>,
    #[pyo3(get)]
    order_id: String,
    #[pyo3(get)]
    intent_id: String,
    #[pyo3(get)]
    plan_id: String,
    #[pyo3(get)]
    leg_id: String,
    #[pyo3(get)]
    strategy_id: String,
    #[pyo3(get)]
    account_id: String,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    market_id: String,
    #[pyo3(get)]
    execution_route_id: String,
    #[pyo3(get)]
    remote_order_id: Option<String>,
    #[pyo3(get)]
    reported_broker_id: Option<String>,
    #[pyo3(get)]
    execution_channel: Option<String>,
    #[pyo3(get)]
    order_entry_symbol: Option<String>,
    #[pyo3(get)]
    quantity: NativeDecimal,
    #[pyo3(get)]
    price: NativeDecimal,
    #[pyo3(get)]
    occurred_at_unix_nanos: u64,
}

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
struct ExecutionPlanCreatedValue {
    #[pyo3(get)]
    plan_id: String,
    #[pyo3(get)]
    intent_id: String,
}

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
struct ExecutionEvent {
    #[pyo3(get)]
    metadata: ExecutionEventMetadata,
    #[pyo3(get)]
    kind: String,
    data: Py<PyAny>,
    #[pyo3(get)]
    strategy_id: Option<String>,
    #[pyo3(get)]
    account_id: Option<String>,
}

#[pymethods]
impl ExecutionEvent {
    #[getter]
    fn data(&self, py: Python<'_>) -> Py<PyAny> {
        self.data.clone_ref(py)
    }
    #[getter]
    fn payload(&self, py: Python<'_>) -> Py<PyAny> {
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
    fn launch_id(&self) -> Option<&str> {
        self.metadata.launch_id.as_deref()
    }
    #[getter]
    fn instance_id(&self) -> Option<&str> {
        self.metadata.instance_id.as_deref()
    }

    #[staticmethod]
    #[pyo3(signature = (*, sequence, instance_id, occurred_at_unix_nanos, launch_id=None))]
    fn simulation_ignored(
        py: Python<'_>,
        sequence: u64,
        instance_id: String,
        occurred_at_unix_nanos: u64,
        launch_id: Option<String>,
    ) -> PyResult<Self> {
        let metadata = simulation_execution_metadata(
            sequence,
            instance_id,
            occurred_at_unix_nanos,
            launch_id,
        )?;
        Ok(Self {
            metadata,
            kind: "plan_created".to_owned(),
            data: Py::new(
                py,
                ExecutionPlanCreatedValue {
                    plan_id: format!("simulation-plan-{sequence}"),
                    intent_id: format!("simulation-intent-{sequence}"),
                },
            )?
            .into_any(),
            strategy_id: None,
            account_id: None,
        })
    }

    #[staticmethod]
    #[pyo3(signature = (*, sequence, strategy_id, account_id, intent_id, instrument_id, status, instance_id, occurred_at_unix_nanos, strategy_decision_id=None, launch_id=None, previous_status=None, order_ids=None, reason=None))]
    #[allow(clippy::too_many_arguments)]
    fn simulation_intent_update(
        py: Python<'_>,
        sequence: u64,
        strategy_id: String,
        account_id: String,
        intent_id: String,
        instrument_id: String,
        status: String,
        instance_id: String,
        occurred_at_unix_nanos: u64,
        strategy_decision_id: Option<String>,
        launch_id: Option<String>,
        previous_status: Option<String>,
        order_ids: Option<Vec<String>>,
        reason: Option<String>,
    ) -> PyResult<Self> {
        let lifecycle = simulation_intent_lifecycle(&status)?;
        let previous_status = previous_status
            .as_deref()
            .map(simulation_intent_lifecycle)
            .transpose()?
            .map(|value| intent_status(value).to_owned());
        for (name, value) in [
            ("strategy_id", &strategy_id),
            ("account_id", &account_id),
            ("intent_id", &intent_id),
            ("instrument_id", &instrument_id),
        ] {
            if value.trim().is_empty() {
                return Err(ExecutionInvalidInputError::new_err(format!(
                    "{name} is required"
                )));
            }
        }
        let metadata = simulation_execution_metadata(
            sequence,
            instance_id.clone(),
            occurred_at_unix_nanos,
            launch_id.clone(),
        )?;
        let intent = ExecutionIntentEventValue {
            intent_id: intent_id.clone(),
            strategy_decision_id,
            strategy_id: strategy_id.clone(),
            launch_id: launch_id.unwrap_or_else(|| "simulation".to_owned()),
            instance_id,
            instrument_id,
            account_ids: vec![account_id.clone()],
            execution_benchmarks: Vec::new(),
            target_quantity: None,
            reason: reason.clone().unwrap_or_default(),
        };
        let payload = ExecutionIntentUpdate {
            intent: Some(Py::new(py, intent)?),
            intent_id,
            strategy_id: strategy_id.clone(),
            account_ids: vec![account_id.clone()],
            status: intent_status(lifecycle).to_owned(),
            previous_status,
            order_ids: order_ids.unwrap_or_default(),
            completed_quantity: None,
            reason: reason.unwrap_or_default(),
        };
        Ok(Self {
            metadata,
            kind: "intent_update".to_owned(),
            data: Py::new(py, payload)?.into_any(),
            strategy_id: Some(strategy_id),
            account_id: Some(account_id),
        })
    }

    #[staticmethod]
    #[pyo3(signature = (*, sequence, strategy_id, account_id, intent_id, fill_id, order_id, instrument_id, quantity, price, instance_id, occurred_at_unix_nanos, launch_id=None))]
    #[allow(clippy::too_many_arguments)]
    fn simulation_fill(
        py: Python<'_>,
        sequence: u64,
        strategy_id: String,
        account_id: String,
        intent_id: String,
        fill_id: String,
        order_id: String,
        instrument_id: String,
        quantity: i64,
        price: i64,
        instance_id: String,
        occurred_at_unix_nanos: u64,
        launch_id: Option<String>,
    ) -> PyResult<Self> {
        for (name, value) in [
            ("strategy_id", &strategy_id),
            ("account_id", &account_id),
            ("intent_id", &intent_id),
            ("fill_id", &fill_id),
            ("order_id", &order_id),
            ("instrument_id", &instrument_id),
        ] {
            if value.trim().is_empty() {
                return Err(ExecutionInvalidInputError::new_err(format!(
                    "{name} is required"
                )));
            }
        }
        let metadata = simulation_execution_metadata(
            sequence,
            instance_id,
            occurred_at_unix_nanos,
            launch_id,
        )?;
        let payload = ExecutionFillValue {
            fill_id,
            trade_id: None,
            order_id,
            intent_id: intent_id.clone(),
            plan_id: format!("simulation-plan-{sequence}"),
            leg_id: format!("simulation-leg-{sequence}"),
            strategy_id: strategy_id.clone(),
            account_id: account_id.clone(),
            instrument_id,
            market_id: "market:simulation".to_owned(),
            execution_route_id: "route:simulation".to_owned(),
            remote_order_id: None,
            reported_broker_id: None,
            execution_channel: None,
            order_entry_symbol: None,
            quantity: NativeDecimal {
                mantissa: quantity,
                scale: 0,
            },
            price: NativeDecimal {
                mantissa: price,
                scale: 0,
            },
            occurred_at_unix_nanos,
        };
        Ok(Self {
            metadata,
            kind: "fill".to_owned(),
            data: Py::new(py, payload)?.into_any(),
            strategy_id: Some(strategy_id),
            account_id: Some(account_id),
        })
    }
}

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
struct ExecutionOrderCurrent {
    #[pyo3(get)]
    order_id: String,
    #[pyo3(get)]
    strategy_id: String,
    #[pyo3(get)]
    intent_id: Option<String>,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    account_id: String,
    #[pyo3(get)]
    side: String,
    #[pyo3(get)]
    quantity: NativeDecimal,
    #[pyo3(get)]
    filled_quantity: NativeDecimal,
    #[pyo3(get)]
    limit_price: Option<NativeDecimal>,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    updated_at_unix_nanos: u64,
}

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
struct ExecutionIntentCurrent {
    #[pyo3(get)]
    intent_id: String,
    #[pyo3(get)]
    strategy_id: String,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    account_ids: Vec<String>,
    #[pyo3(get)]
    target_quantity: NativeDecimal,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    reason: String,
    #[pyo3(get)]
    order_ids: Vec<String>,
    #[pyo3(get)]
    strategy_decision_id: Option<String>,
    #[pyo3(get)]
    updated_at_unix_nanos: u64,
}

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
struct ExecutionCommitmentCurrent {
    #[pyo3(get)]
    order_id: String,
    #[pyo3(get)]
    account_id: String,
    #[pyo3(get)]
    segment_key: String,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    resource_kind: String,
    #[pyo3(get)]
    resource_id: String,
    #[pyo3(get)]
    amount: NativeDecimal,
    #[pyo3(get)]
    remaining_quantity: NativeDecimal,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    basis_kind: String,
    #[pyo3(get)]
    updated_at_unix_nanos: u64,
}

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
#[derive(Clone)]
struct ExecutionFundingRequirement {
    #[pyo3(get)]
    required_margin: NativeDecimal,
    #[pyo3(get)]
    available_margin: NativeDecimal,
    #[pyo3(get)]
    shortfall: NativeDecimal,
    #[pyo3(get)]
    margin_rule_id: String,
    #[pyo3(get)]
    risk_decision_id: String,
    #[pyo3(get)]
    risk_policy_version: u64,
    #[pyo3(get)]
    account_snapshot_watermark: u64,
    #[pyo3(get)]
    broker: String,
    #[pyo3(get)]
    segment: String,
    #[pyo3(get)]
    collateral_asset: String,
}

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
struct ExecutionRiskReservationCurrent {
    #[pyo3(get)]
    order_id: String,
    #[pyo3(get)]
    reservation_id: String,
    #[pyo3(get)]
    idempotency_key: String,
    #[pyo3(get)]
    account_id: String,
    #[pyo3(get)]
    amount: NativeDecimal,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    risk_generation: u64,
    #[pyo3(get)]
    risk_event_sequence: u64,
    #[pyo3(get)]
    policy_version: u64,
    #[pyo3(get)]
    expires_at_unix_nanos: u64,
    #[pyo3(get)]
    updated_at_unix_nanos: u64,
    #[pyo3(get)]
    funding_requirement: Option<ExecutionFundingRequirement>,
}

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
struct ExecutionAlgorithmRunCurrent {
    #[pyo3(get)]
    algorithm_run_id: String,
    #[pyo3(get)]
    algorithm_version: u32,
    #[pyo3(get)]
    intent_id: String,
    #[pyo3(get)]
    algorithm_kind: String,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    decision_sequence: u64,
    #[pyo3(get)]
    last_decision_at_unix_nanos: Option<u64>,
    #[pyo3(get)]
    next_wake_at_unix_nanos: Option<u64>,
    #[pyo3(get)]
    action_count: u64,
    #[pyo3(get)]
    pending_action_count: u64,
    #[pyo3(get)]
    indeterminate_action_count: u64,
    #[pyo3(get)]
    leg_count: usize,
}

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
struct ExecutionUnknownRemoteOrderCurrent {
    #[pyo3(get)]
    remote_order_id: String,
    #[pyo3(get)]
    symbol: String,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    execution_id: Option<String>,
    #[pyo3(get)]
    fill_quantity: Option<NativeDecimal>,
    #[pyo3(get)]
    fill_price: Option<NativeDecimal>,
    #[pyo3(get)]
    fee_currency: Option<String>,
    #[pyo3(get)]
    fee_amount: Option<NativeDecimal>,
    #[pyo3(get)]
    first_seen_at_unix_nanos: u64,
    #[pyo3(get)]
    last_seen_at_unix_nanos: u64,
    #[pyo3(get)]
    resolution: String,
    #[pyo3(get)]
    reason: Option<String>,
}

#[pyclass(module = "kairospy._native_execution_contract")]
struct ExecutionCurrentView {
    creator_pid: u32,
    root: PathBuf,
    identity: InstanceIdentity,
    path: PathBuf,
    reader: Mutex<Option<RustView>>,
    closed: Mutex<bool>,
}

#[pymethods]
impl ExecutionCurrentView {
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
        let path = kairos_execution_contract::execution_indexed_environment_path(&root, &identity)
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

    fn orders(&self, py: Python<'_>) -> PyResult<Vec<ExecutionOrderCurrent>> {
        self.read(py, |reader| {
            reader
                .orders()?
                .into_iter()
                .map(|value| value.order().map(order))
                .collect()
        })
    }

    fn get_order(
        &self,
        py: Python<'_>,
        order_id: String,
    ) -> PyResult<Option<ExecutionOrderCurrent>> {
        self.read(py, move |reader| {
            reader.with_order(&order_id, |value| Ok(value.map(order)))
        })
    }

    fn intents(&self, py: Python<'_>) -> PyResult<Vec<ExecutionIntentCurrent>> {
        self.read(py, |reader| {
            reader
                .intents()?
                .into_iter()
                .map(|value| value.intent().map(intent))
                .collect()
        })
    }

    fn get_intent(
        &self,
        py: Python<'_>,
        intent_id: String,
    ) -> PyResult<Option<ExecutionIntentCurrent>> {
        self.read(py, move |reader| {
            reader
                .intent(&intent_id)?
                .map(|value| value.intent().map(intent))
                .transpose()
        })
    }

    fn commitments(&self, py: Python<'_>) -> PyResult<Vec<ExecutionCommitmentCurrent>> {
        self.read(py, |reader| {
            reader
                .commitments()?
                .into_iter()
                .map(|value| value.commitment().map(commitment))
                .collect()
        })
    }

    fn algorithm_runs(&self, py: Python<'_>) -> PyResult<Vec<ExecutionAlgorithmRunCurrent>> {
        self.read(py, |reader| {
            reader
                .algorithm_runs()?
                .into_iter()
                .map(|value| value.algorithm_run().map(algorithm_run))
                .collect()
        })
    }

    fn risk_reservations(&self, py: Python<'_>) -> PyResult<Vec<ExecutionRiskReservationCurrent>> {
        self.read(py, |reader| {
            reader
                .risk_reservations()?
                .into_iter()
                .map(|value| value.risk_reservation().map(risk_reservation))
                .collect()
        })
    }

    fn unknown_remote_orders(
        &self,
        py: Python<'_>,
    ) -> PyResult<Vec<ExecutionUnknownRemoteOrderCurrent>> {
        self.read(py, |reader| {
            reader
                .unknown_remote_orders()?
                .into_iter()
                .map(|value| value.unknown_remote_order().map(unknown_remote_order))
                .collect()
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

impl ExecutionCurrentView {
    fn read<T: Send>(
        &self,
        py: Python<'_>,
        operation: impl FnOnce(&RustView) -> Result<T, ContractError> + Send,
    ) -> PyResult<T> {
        self.ensure_process()?;
        py.detach(|| {
            if *self.closed.lock().map_err(|_| lock_error())? {
                return Err(PyRuntimeError::new_err(
                    "Execution current-view reader is closed",
                ));
            }
            let mut reader = self.reader.lock().map_err(|_| lock_error())?;
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
                "Execution current-view reader cannot be used after fork",
            ));
        }
        Ok(())
    }
}

fn order(value: fb::ExecutionOrderCurrent<'_>) -> ExecutionOrderCurrent {
    let state = value.state();
    ExecutionOrderCurrent {
        order_id: state.order_id().to_owned(),
        strategy_id: state.strategy_id().to_owned(),
        intent_id: optional_text(state.intent_id()),
        instrument_id: state.instrument_id().to_owned(),
        account_id: state.account_id().to_owned(),
        side: if state.side().0 == 1 { "buy" } else { "sell" }.to_owned(),
        quantity: decimal(state.quantity()),
        filled_quantity: decimal(state.filled_quantity()),
        limit_price: state.limit_price().map(decimal),
        status: order_status(state.lifecycle().0).to_owned(),
        updated_at_unix_nanos: state.updated_at_unix_nanos(),
    }
}

fn intent(value: fb::ExecutionIntentCurrent<'_>) -> ExecutionIntentCurrent {
    let state = value.state();
    let raw = state.intent();
    let legs = raw.legs();
    let first = legs.get(0);
    let account_ids = legs.iter().map(|leg| leg.account_id().to_owned()).collect();
    let mut order_ids = Vec::new();
    if let Some(plan) = state.plan() {
        for leg in plan.legs() {
            for order_id in leg.order_ids() {
                if !order_ids.iter().any(|existing| existing == order_id) {
                    order_ids.push(order_id.to_owned());
                }
            }
        }
    }
    ExecutionIntentCurrent {
        intent_id: raw.intent_id().to_owned(),
        strategy_id: raw.strategy_id().to_owned(),
        instrument_id: first.instrument_id().to_owned(),
        account_ids,
        target_quantity: decimal(first.quantity()),
        status: intent_status(state.lifecycle().0).to_owned(),
        reason: state.reason().unwrap_or_default().to_owned(),
        order_ids,
        strategy_decision_id: optional_text(raw.strategy_decision_id().unwrap_or_default()),
        updated_at_unix_nanos: state.updated_at_unix_nanos(),
    }
}

fn commitment(value: fb::ExecutionCommitmentCurrent<'_>) -> ExecutionCommitmentCurrent {
    let state = value.state();
    ExecutionCommitmentCurrent {
        order_id: state.order_id().to_owned(),
        account_id: state.account_id().to_owned(),
        segment_key: state.segment_key().to_owned(),
        instrument_id: state.instrument_id().to_owned(),
        resource_kind: match state.resource_kind().0 {
            1 => "asset",
            2 => "instrument",
            3 => "margin_notional",
            4 => "closeable_position",
            _ => "unspecified",
        }
        .to_owned(),
        resource_id: state.resource_id().to_owned(),
        amount: decimal(state.amount()),
        remaining_quantity: decimal(state.remaining_quantity()),
        status: match state.lifecycle().0 {
            1 => "held_before_send",
            2 => "active",
            3 => "uncertain",
            4 => "reduced",
            5 => "released",
            6 => "reconciled",
            _ => "uncertain",
        }
        .to_owned(),
        basis_kind: match state.basis_kind().0 {
            1 => "quote_price_cap",
            2 => "base_quantity",
            3 => "contract_notional",
            4 => "simulation_quantity",
            5 => "closeable_position_quantity",
            _ => "unspecified",
        }
        .to_owned(),
        updated_at_unix_nanos: state.updated_at_unix_nanos(),
    }
}

fn risk_reservation(
    value: fb::ExecutionRiskReservationCurrent<'_>,
) -> ExecutionRiskReservationCurrent {
    let state = value.state();
    ExecutionRiskReservationCurrent {
        order_id: state.order_id().to_owned(),
        reservation_id: state.reservation_id().to_owned(),
        idempotency_key: state.idempotency_key().to_owned(),
        account_id: state.account_id().to_owned(),
        amount: decimal(state.amount()),
        status: match state.lifecycle().0 {
            1 => "authorize_pending",
            2 => "active",
            3 => "resize_pending",
            4 => "release_pending",
            5 => "consume_pending",
            6 => "released",
            7 => "consumed",
            8 => "expired",
            9 => "uncertain",
            10 => "failed",
            _ => "uncertain",
        }
        .to_owned(),
        risk_generation: state.risk_generation(),
        risk_event_sequence: state.risk_event_sequence(),
        policy_version: state.policy_version(),
        expires_at_unix_nanos: state.expires_at_unix_nanos(),
        updated_at_unix_nanos: state.updated_at_unix_nanos(),
        funding_requirement: state
            .funding_requirement()
            .map(|raw| ExecutionFundingRequirement {
                required_margin: decimal(raw.required_margin()),
                available_margin: decimal(raw.available_margin()),
                shortfall: decimal(raw.shortfall()),
                margin_rule_id: raw.margin_rule_id().to_owned(),
                risk_decision_id: raw.risk_decision_id().to_owned(),
                risk_policy_version: raw.risk_policy_version(),
                account_snapshot_watermark: raw.account_snapshot_watermark(),
                broker: raw.broker().to_owned(),
                segment: raw.segment().to_owned(),
                collateral_asset: raw.collateral_asset().to_owned(),
            }),
    }
}

fn algorithm_run(value: fb::ExecutionAlgorithmRunCurrent<'_>) -> ExecutionAlgorithmRunCurrent {
    let state = value.state();
    ExecutionAlgorithmRunCurrent {
        algorithm_run_id: state.algorithm_run_id().to_owned(),
        algorithm_version: state.algorithm_version(),
        intent_id: state.intent_id().to_owned(),
        algorithm_kind: state.algorithm_kind().to_owned(),
        status: match state.lifecycle().0 {
            1 => "planned",
            2 => "running",
            3 => "waiting",
            4 => "completed",
            5 => "unwound",
            6 => "failed",
            7 => "reconciliation_required",
            _ => "unspecified",
        }
        .to_owned(),
        decision_sequence: state.decision_sequence(),
        last_decision_at_unix_nanos: state.last_decision_at_unix_nanos(),
        next_wake_at_unix_nanos: state.next_wake_at_unix_nanos(),
        action_count: state.action_count(),
        pending_action_count: state.pending_action_count(),
        indeterminate_action_count: state.indeterminate_action_count(),
        leg_count: state.legs().len(),
    }
}

fn unknown_remote_order(
    value: fb::ExecutionUnknownRemoteOrderCurrent<'_>,
) -> ExecutionUnknownRemoteOrderCurrent {
    let state = value.state();
    ExecutionUnknownRemoteOrderCurrent {
        remote_order_id: state.remote_order_id().to_owned(),
        symbol: state.symbol().to_owned(),
        status: order_status(state.lifecycle().0).to_owned(),
        execution_id: optional_text(state.execution_id().unwrap_or_default()),
        fill_quantity: state.fill_quantity().map(decimal),
        fill_price: state.fill_price().map(decimal),
        fee_currency: optional_text(state.fee_currency().unwrap_or_default()),
        fee_amount: state.fee_amount().map(decimal),
        first_seen_at_unix_nanos: state.first_seen_at_unix_nanos(),
        last_seen_at_unix_nanos: state.last_seen_at_unix_nanos(),
        resolution: state.resolution().to_owned(),
        reason: optional_text(state.reason().unwrap_or_default()),
    }
}

fn decimal(value: &Decimal64) -> NativeDecimal {
    NativeDecimal {
        mantissa: value.mantissa(),
        scale: value.scale(),
    }
}
fn optional_text(value: &str) -> Option<String> {
    (!value.trim().is_empty()).then(|| value.to_owned())
}
fn decimal_text(mantissa: i64, scale: u8) -> String {
    let sign = if mantissa < 0 { "-" } else { "" };
    let mut digits = mantissa.unsigned_abs().to_string();
    if scale == 0 {
        return format!("{sign}{digits}");
    }
    let scale = usize::from(scale);
    if digits.len() <= scale {
        digits.insert_str(0, &"0".repeat(scale + 1 - digits.len()));
    }
    let split = digits.len() - scale;
    format!("{sign}{}.{}", &digits[..split], &digits[split..])
}
fn order_status(value: u8) -> &'static str {
    match value {
        1 => "pending",
        2 => "submitting",
        3 => "accepted",
        4 => "partially_filled",
        5 => "filled",
        6 => "cancel_requested",
        7 => "canceled",
        8 => "rejected",
        9 => "expired",
        11 => "failed",
        _ => "unknown",
    }
}
fn intent_status(value: u8) -> &'static str {
    match value {
        1 => "accepted",
        2 => "planning",
        3 => "planned",
        4 => "executing",
        5 => "partially_filled",
        6 => "cancel_requested",
        7 => "satisfied",
        8 => "rejected",
        9 => "canceled",
        10 => "expired",
        11 => "failed",
        12 => "compensating",
        13 => "reconciliation_required",
        _ => "unknown",
    }
}

fn identity(
    workspace_id: String,
    launch_id: Option<String>,
    instance_id: Option<String>,
) -> PyResult<InstanceIdentity> {
    match (launch_id, instance_id) {
        (Some(launch), Some(instance)) => InstanceIdentity::new(workspace_id, launch, instance)
            .map_err(|error| ExecutionInvalidInputError::new_err(error.to_string())),
        (None, None) => InstanceIdentity::unscoped(workspace_id)
            .map_err(|error| ExecutionInvalidInputError::new_err(error.to_string())),
        _ => Err(ExecutionInvalidInputError::new_err(
            "launch_id and instance_id must both be present or both be absent",
        )),
    }
}
fn contract_error(error: ContractError) -> PyErr {
    match error {
        ContractError::Invalid(message) => ExecutionInvalidCurrentViewError::new_err(message),
        ContractError::Transport(message) | ContractError::Unsupported(message) => {
            ExecutionCurrentViewUnavailableError::new_err(message)
        },
    }
}
fn lock_error() -> PyErr {
    PyRuntimeError::new_err("Execution current-view reader lock is poisoned")
}

fn execution_event_metadata(
    value: kairos_protocol::generated::kairos::common::v_2::EventMetadata<'_>,
) -> PyResult<ExecutionEventMetadata> {
    let EventMetadataOwned {
        event_id,
        stream_id,
        sequence,
        producer_id,
        workspace_id,
        launch_id,
        instance_id,
        correlation_id,
        causation_id,
        occurred_at_unix_nanos,
        published_at_unix_nanos,
    } = decode_event_metadata(value)
        .map_err(|error| ExecutionInvalidEventError::new_err(error.to_string()))?;
    Ok(ExecutionEventMetadata {
        event_id: event_id.to_string(),
        stream_id: stream_id.to_string(),
        sequence: sequence.get(),
        producer: producer_id.to_string(),
        workspace_id: workspace_id.to_string(),
        launch_id: launch_id.map(|value| value.to_string()),
        instance_id: instance_id.map(|value| value.to_string()),
        correlation_id: correlation_id.map(|value| value.to_string()),
        causation_id: causation_id.map(|value| value.to_string()),
        occurred_at_unix_nanos: occurred_at_unix_nanos.get(),
        published_at_unix_nanos: published_at_unix_nanos.get(),
    })
}

fn simulation_execution_metadata(
    sequence: u64,
    instance_id: String,
    occurred_at_unix_nanos: u64,
    launch_id: Option<String>,
) -> PyResult<ExecutionEventMetadata> {
    if sequence == 0 || instance_id.trim().is_empty() || occurred_at_unix_nanos == 0 {
        return Err(ExecutionInvalidInputError::new_err(
            "simulation event requires positive sequence/time and instance_id",
        ));
    }
    Ok(ExecutionEventMetadata {
        event_id: format!("execution.simulation.{sequence}"),
        stream_id: "execution.events".to_owned(),
        sequence,
        producer: "execution-simulation".to_owned(),
        workspace_id: "workspace:simulation".to_owned(),
        launch_id,
        instance_id: Some(instance_id),
        correlation_id: None,
        causation_id: None,
        occurred_at_unix_nanos,
        published_at_unix_nanos: occurred_at_unix_nanos,
    })
}

fn simulation_intent_lifecycle(status: &str) -> PyResult<u8> {
    match status {
        "accepted" => Ok(1),
        "planning" => Ok(2),
        "planned" => Ok(3),
        "executing" => Ok(4),
        "partially_filled" => Ok(5),
        "cancel_requested" => Ok(6),
        "satisfied" => Ok(7),
        "rejected" => Ok(8),
        "canceled" => Ok(9),
        "expired" => Ok(10),
        "failed" => Ok(11),
        "compensating" => Ok(12),
        "reconciliation_required" => Ok(13),
        _ => Err(ExecutionInvalidInputError::new_err(
            "unknown Execution intent status",
        )),
    }
}

fn event_intent(
    py: Python<'_>,
    value: fb::ExecutionIntent<'_>,
) -> PyResult<ExecutionIntentEventValue> {
    let first = value.legs().get(0);
    let account_ids = value
        .legs()
        .iter()
        .map(|leg| leg.account_id().to_owned())
        .collect::<Vec<_>>();
    let execution_benchmarks = value
        .execution_benchmarks()
        .iter()
        .map(|benchmark| {
            Py::new(
                py,
                ExecutionBenchmark {
                    kind: match benchmark.kind().0 {
                        1 => "arrival",
                        _ => "unspecified",
                    }
                    .to_owned(),
                    leg_id: benchmark.leg_id().map(str::to_owned),
                    instrument_id: benchmark.instrument_id().to_owned(),
                    market_id: benchmark.market_id().to_owned(),
                    price: decimal(benchmark.price()),
                    observed_at_unix_nanos: benchmark.observed_at_unix_nanos(),
                },
            )
        })
        .collect::<PyResult<Vec<_>>>()?;
    Ok(ExecutionIntentEventValue {
        intent_id: value.intent_id().to_owned(),
        strategy_decision_id: value.strategy_decision_id().map(str::to_owned),
        strategy_id: value.strategy_id().to_owned(),
        launch_id: value.launch_id().to_owned(),
        instance_id: value.instance_id().to_owned(),
        instrument_id: first.instrument_id().to_owned(),
        account_ids,
        execution_benchmarks,
        target_quantity: None,
        reason: value.reason().unwrap_or_default().to_owned(),
    })
}

fn intent_update(
    py: Python<'_>,
    value: Option<fb::ExecutionIntent<'_>>,
    intent_id: &str,
    status: fb::IntentLifecycle,
    previous_status: Option<fb::IntentLifecycle>,
    order_ids: Vec<String>,
    completed_quantity: Option<NativeDecimal>,
    reason: String,
) -> PyResult<(ExecutionIntentUpdate, Option<String>, Option<String>)> {
    let Some(value) = value else {
        return Ok((
            ExecutionIntentUpdate {
                intent: None,
                intent_id: intent_id.to_owned(),
                strategy_id: String::new(),
                account_ids: Vec::new(),
                status: intent_status(status.0).to_owned(),
                previous_status: previous_status.map(|value| intent_status(value.0).to_owned()),
                order_ids,
                completed_quantity,
                reason,
            },
            None,
            None,
        ));
    };
    let intent = event_intent(py, value)?;
    let strategy_id = intent.strategy_id.clone();
    let account_id = intent.account_ids.first().cloned();
    let account_ids = intent.account_ids.clone();
    Ok((
        ExecutionIntentUpdate {
            intent: Some(Py::new(py, intent)?),
            intent_id: intent_id.to_owned(),
            strategy_id: strategy_id.clone(),
            account_ids,
            status: intent_status(status.0).to_owned(),
            previous_status: previous_status.map(|value| intent_status(value.0).to_owned()),
            order_ids,
            completed_quantity,
            reason,
        },
        Some(strategy_id),
        account_id,
    ))
}

fn event_order(value: fb::OrderState<'_>) -> ExecutionOrderUpdate {
    ExecutionOrderUpdate {
        order_id: value.order_id().to_owned(),
        intent_id: value.intent_id().to_owned(),
        plan_id: value.plan_id().to_owned(),
        leg_id: value.leg_id().to_owned(),
        strategy_id: value.strategy_id().to_owned(),
        account_id: value.account_id().to_owned(),
        instrument_id: value.instrument_id().to_owned(),
        market_id: value.market_id().to_owned(),
        execution_route_id: value.execution_route_id().to_owned(),
        side: match value.side().0 {
            1 => "buy",
            2 => "sell",
            _ => "unknown",
        }
        .to_owned(),
        quantity: decimal(value.quantity()),
        filled_quantity: decimal(value.filled_quantity()),
        limit_price: value.limit_price().map(decimal),
        status: order_status(value.lifecycle().0).to_owned(),
        updated_at_unix_nanos: value.updated_at_unix_nanos(),
        reason: value.reason().unwrap_or_default().to_owned(),
    }
}

fn event_fill(value: fb::Fill<'_>, occurred_at_unix_nanos: u64) -> ExecutionFillValue {
    ExecutionFillValue {
        fill_id: value.fill_id().to_owned(),
        trade_id: value.trade_id().map(str::to_owned),
        order_id: value.order_id().to_owned(),
        intent_id: value.intent_id().to_owned(),
        plan_id: value.plan_id().to_owned(),
        leg_id: value.leg_id().to_owned(),
        strategy_id: value.strategy_id().to_owned(),
        account_id: value.account_id().to_owned(),
        instrument_id: value.instrument_id().to_owned(),
        market_id: value.market_id().to_owned(),
        execution_route_id: value.execution_route_id().to_owned(),
        remote_order_id: value.remote_order_id().map(str::to_owned),
        reported_broker_id: value.reported_broker_id().map(str::to_owned),
        execution_channel: value.execution_channel().map(str::to_owned),
        order_entry_symbol: value.provider_symbol().map(str::to_owned),
        quantity: decimal(value.quantity()),
        price: decimal(value.price()),
        occurred_at_unix_nanos,
    }
}

fn project_execution_event(
    py: Python<'_>,
    value: RustExecutionEvent<'_>,
) -> PyResult<ExecutionEvent> {
    macro_rules! order_event {
        ($root:expr) => {{
            let root = $root;
            let metadata = execution_event_metadata(root.metadata())?;
            let payload = event_order(root.order());
            let strategy_id = Some(payload.strategy_id.clone());
            let account_id = Some(payload.account_id.clone());
            (
                metadata,
                "order_update",
                Py::new(py, payload)?.into_any(),
                strategy_id,
                account_id,
            )
        }};
    }
    let (metadata, kind, data, strategy_id, account_id) = match value {
        RustExecutionEvent::IntentAccepted(root) => {
            let metadata = execution_event_metadata(root.metadata())?;
            let (payload, strategy_id, account_id) = intent_update(
                py,
                root.intent(),
                root.intent_id(),
                root.lifecycle(),
                None,
                Vec::new(),
                None,
                String::new(),
            )?;
            (
                metadata,
                "intent_update",
                Py::new(py, payload)?.into_any(),
                strategy_id,
                account_id,
            )
        },
        RustExecutionEvent::IntentRejected(root) => {
            let metadata = execution_event_metadata(root.metadata())?;
            let reason = root.details().iter().collect::<Vec<_>>().join("; ");
            let (payload, strategy_id, account_id) = intent_update(
                py,
                root.intent(),
                root.intent_id(),
                root.lifecycle(),
                None,
                Vec::new(),
                None,
                reason,
            )?;
            (
                metadata,
                "intent_update",
                Py::new(py, payload)?.into_any(),
                strategy_id,
                account_id,
            )
        },
        RustExecutionEvent::IntentLifecycleChanged(root) => {
            let metadata = execution_event_metadata(root.metadata())?;
            let intent = root.intent();
            let intent_id = intent.intent_id().to_owned();
            let (payload, strategy_id, account_id) = intent_update(
                py,
                Some(intent),
                &intent_id,
                root.lifecycle(),
                Some(root.previous_lifecycle()),
                root.order_ids().iter().map(str::to_owned).collect(),
                Some(decimal(root.completed_quantity())),
                root.reason().unwrap_or_default().to_owned(),
            )?;
            (
                metadata,
                "intent_update",
                Py::new(py, payload)?.into_any(),
                strategy_id,
                account_id,
            )
        },
        RustExecutionEvent::PlanCreated(root) => {
            let metadata = execution_event_metadata(root.metadata())?;
            let plan = root.plan();
            let payload = ExecutionPlanCreatedValue {
                plan_id: plan.plan_id().to_owned(),
                intent_id: plan.intent_id().to_owned(),
            };
            (
                metadata,
                "plan_created",
                Py::new(py, payload)?.into_any(),
                None,
                None,
            )
        },
        RustExecutionEvent::OrderSubmitted(root) => order_event!(root),
        RustExecutionEvent::OrderAccepted(root) => order_event!(root),
        RustExecutionEvent::OrderRejected(root) => order_event!(root),
        RustExecutionEvent::OrderCanceled(root) => order_event!(root),
        RustExecutionEvent::OrderExpired(root) => order_event!(root),
        RustExecutionEvent::FillRecorded(root) => {
            let metadata = execution_event_metadata(root.metadata())?;
            let payload = event_fill(root.fill(), metadata.occurred_at_unix_nanos);
            let strategy_id = Some(payload.strategy_id.clone());
            let account_id = Some(payload.account_id.clone());
            (
                metadata,
                "fill",
                Py::new(py, payload)?.into_any(),
                strategy_id,
                account_id,
            )
        },
    };
    Ok(ExecutionEvent {
        metadata,
        kind: kind.to_owned(),
        data,
        strategy_id,
        account_id,
    })
}

#[pyfunction]
fn decode_event(py: Python<'_>, payload: &[u8]) -> PyResult<ExecutionEvent> {
    let value = kairos_execution_contract::event::decode_event(payload)
        .map_err(|error| ExecutionInvalidEventError::new_err(error.to_string()))?;
    project_execution_event(py, value)
}

#[pyfunction]
fn build_info() -> NativeBuildInfo {
    NativeBuildInfo {
        api_version: API_VERSION,
        owner: "Execution".to_owned(),
        package_version: env!("CARGO_PKG_VERSION").to_owned(),
        contract_fingerprint: kairos_execution_contract::CONTRACT_FINGERPRINT.to_owned(),
    }
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
    kairos_execution_contract::execution_indexed_environment_path(root, &identity)
        .map_err(contract_error)
}

#[pymodule]
fn _native_execution_contract(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module
        .py()
        .get_type::<ExecutionInvalidInputError>()
        .setattr("code", "invalid_input")?;
    module
        .py()
        .get_type::<ExecutionInvalidCurrentViewError>()
        .setattr("code", "invalid_wire_data")?;
    module
        .py()
        .get_type::<ExecutionCurrentViewUnavailableError>()
        .setattr("code", "current_view_unavailable")?;
    module
        .py()
        .get_type::<ExecutionInvalidEventError>()
        .setattr("code", "invalid_wire_data")?;
    control::register(module)?;
    module.add(
        "EXECUTION_EVENT_STREAM_ID",
        kairos_execution_contract::EXECUTION_EVENTS_STREAM_ID,
    )?;
    module.add(
        "DEFAULT_AERON_CHANNEL",
        kairos_execution_contract::DEFAULT_AERON_CHANNEL,
    )?;
    module.add(
        "ExecutionInvalidInputError",
        module.py().get_type::<ExecutionInvalidInputError>(),
    )?;
    module.add(
        "ExecutionInvalidCurrentViewError",
        module.py().get_type::<ExecutionInvalidCurrentViewError>(),
    )?;
    module.add(
        "ExecutionCurrentViewUnavailableError",
        module
            .py()
            .get_type::<ExecutionCurrentViewUnavailableError>(),
    )?;
    module.add(
        "ExecutionInvalidEventError",
        module.py().get_type::<ExecutionInvalidEventError>(),
    )?;
    module.add_class::<NativeBuildInfo>()?;
    module.add_class::<NativeExecutionClient>()?;
    module.add_class::<NativeDecimal>()?;
    module.add_class::<ExecutionEventMetadata>()?;
    module.add_class::<ExecutionBenchmark>()?;
    module.add_class::<ExecutionIntentEventValue>()?;
    module.add_class::<ExecutionIntentUpdate>()?;
    module.add_class::<ExecutionOrderUpdate>()?;
    module.add_class::<ExecutionFillValue>()?;
    module.add_class::<ExecutionPlanCreatedValue>()?;
    module.add_class::<ExecutionEvent>()?;
    module.add_class::<ExecutionOrderCurrent>()?;
    module.add_class::<ExecutionIntentCurrent>()?;
    module.add_class::<ExecutionCommitmentCurrent>()?;
    module.add_class::<ExecutionFundingRequirement>()?;
    module.add_class::<ExecutionRiskReservationCurrent>()?;
    module.add_class::<ExecutionAlgorithmRunCurrent>()?;
    module.add_class::<ExecutionUnknownRemoteOrderCurrent>()?;
    module.add_class::<ExecutionCurrentView>()?;
    module.add_function(wrap_pyfunction!(build_info, module)?)?;
    module.add_function(wrap_pyfunction!(decode_event, module)?)?;
    module.add_function(wrap_pyfunction!(indexed_environment_path, module)?)?;
    Ok(())
}
