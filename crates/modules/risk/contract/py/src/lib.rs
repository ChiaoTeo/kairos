use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Mutex;

use kairos_primitives::runtime::{ActorId, InstanceIdentity};
use kairos_protocol::generated::kairos::common::v_2::Decimal64;
use kairos_risk_contract::{ContractError, RiskIndexedView as RustView};
use pyo3::create_exception;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

create_exception!(
    _native_risk_contract,
    RiskInvalidCurrentViewError,
    PyValueError
);
create_exception!(
    _native_risk_contract,
    RiskCurrentViewUnavailableError,
    PyRuntimeError
);

const API_VERSION: u32 = 1;
#[pyclass(frozen, module = "kairospy._native_risk_contract")]
struct NativeBuildInfo {
    #[pyo3(get)]
    api_version: u32,
    #[pyo3(get)]
    owner: String,
    #[pyo3(get)]
    package_version: String,
}
#[pyclass(frozen, module = "kairospy._native_risk_contract")]
#[derive(Clone)]
struct NativeDecimal {
    #[pyo3(get)]
    mantissa: i64,
    #[pyo3(get)]
    scale: u8,
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
    reader: Mutex<Option<RustView>>,
}
#[pymethods]
impl RiskCurrentView {
    #[new]
    #[pyo3(signature = (root, actor_id, workspace_id, launch_id=None, instance_id=None))]
    fn new(
        py: Python<'_>,
        root: PathBuf,
        actor_id: String,
        workspace_id: String,
        launch_id: Option<String>,
        instance_id: Option<String>,
    ) -> PyResult<Self> {
        let identity = identity(workspace_id, launch_id, instance_id)?;
        let actor =
            ActorId::new(actor_id).map_err(|error| PyValueError::new_err(error.to_string()))?;
        let reader = py
            .detach(move || RustView::open(root, &identity, actor))
            .map_err(contract_error)?;
        Ok(Self {
            creator_pid: std::process::id(),
            reader: Mutex::new(Some(reader)),
        })
    }
    fn snapshot(&self, py: Python<'_>) -> PyResult<RiskCurrentSnapshot> {
        self.ensure_process()?;
        py.detach(|| {
            let reader = self.reader.lock().map_err(|_| lock_error())?;
            let snapshot = reader
                .as_ref()
                .ok_or_else(|| PyRuntimeError::new_err("Risk current-view reader is closed"))?
                .snapshot()
                .map_err(contract_error)?;
            project(snapshot).map_err(contract_error)
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
impl RiskCurrentView {
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
#[pyfunction]
fn build_info() -> NativeBuildInfo {
    NativeBuildInfo {
        api_version: API_VERSION,
        owner: "Risk".to_owned(),
        package_version: env!("CARGO_PKG_VERSION").to_owned(),
    }
}
#[pymodule]
fn _native_risk_contract(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add(
        "RiskInvalidCurrentViewError",
        module.py().get_type::<RiskInvalidCurrentViewError>(),
    )?;
    module.add(
        "RiskCurrentViewUnavailableError",
        module.py().get_type::<RiskCurrentViewUnavailableError>(),
    )?;
    module.add_class::<NativeBuildInfo>()?;
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
    Ok(())
}
