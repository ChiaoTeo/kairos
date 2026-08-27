use std::path::PathBuf;
use std::sync::Mutex;

use kairos_execution_contract::{ContractError, ExecutionIndexedView as RustView};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::generated::kairos::common::v_2::Decimal64;
use kairos_protocol::generated::kairos::execution::v_2 as fb;
use pyo3::create_exception;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

create_exception!(
    _native_execution_contract,
    ExecutionInvalidCurrentViewError,
    PyValueError
);
create_exception!(
    _native_execution_contract,
    ExecutionCurrentViewUnavailableError,
    PyRuntimeError
);

const API_VERSION: u32 = 1;

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
struct NativeBuildInfo {
    #[pyo3(get)]
    api_version: u32,
    #[pyo3(get)]
    owner: String,
    #[pyo3(get)]
    package_version: String,
}

#[pyclass(frozen, module = "kairospy._native_execution_contract")]
#[derive(Clone)]
struct NativeDecimal {
    #[pyo3(get)]
    mantissa: i64,
    #[pyo3(get)]
    scale: u8,
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
    reader: Mutex<Option<RustView>>,
}

#[pymethods]
impl ExecutionCurrentView {
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
            let reader = self.reader.lock().map_err(|_| lock_error())?;
            let reader = reader.as_ref().ok_or_else(|| {
                PyRuntimeError::new_err("Execution current-view reader is closed")
            })?;
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
            .map_err(|error| PyValueError::new_err(error.to_string())),
        (None, None) => InstanceIdentity::unscoped(workspace_id)
            .map_err(|error| PyValueError::new_err(error.to_string())),
        _ => Err(PyValueError::new_err(
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

#[pyfunction]
fn build_info() -> NativeBuildInfo {
    NativeBuildInfo {
        api_version: API_VERSION,
        owner: "Execution".to_owned(),
        package_version: env!("CARGO_PKG_VERSION").to_owned(),
    }
}

#[pymodule]
fn _native_execution_contract(module: &Bound<'_, PyModule>) -> PyResult<()> {
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
    module.add_class::<NativeBuildInfo>()?;
    module.add_class::<NativeDecimal>()?;
    module.add_class::<ExecutionOrderCurrent>()?;
    module.add_class::<ExecutionIntentCurrent>()?;
    module.add_class::<ExecutionCommitmentCurrent>()?;
    module.add_class::<ExecutionFundingRequirement>()?;
    module.add_class::<ExecutionRiskReservationCurrent>()?;
    module.add_class::<ExecutionAlgorithmRunCurrent>()?;
    module.add_class::<ExecutionUnknownRemoteOrderCurrent>()?;
    module.add_class::<ExecutionCurrentView>()?;
    module.add_function(wrap_pyfunction!(build_info, module)?)?;
    Ok(())
}
