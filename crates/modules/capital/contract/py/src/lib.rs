use std::path::PathBuf;
use std::sync::Mutex;

use kairos_capital_contract::{
    CAPITAL_ALERTS_DATABASE, CAPITAL_AVAILABILITY_DATABASE, CAPITAL_DEMANDS_DATABASE,
    CAPITAL_FACTS_DATABASE, CAPITAL_OBJECTIVES_DATABASE, CAPITAL_OPERATIONS_DATABASE,
    CAPITAL_PLANS_DATABASE, CAPITAL_POLICIES_DATABASE, CAPITAL_RESERVATIONS_DATABASE,
    CAPITAL_ROUTES_DATABASE, CapitalIndexedView as RustView, ContractError,
};
use kairos_primitives::capital::CapitalGroupId;
use kairos_primitives::decimal::DecimalParts;
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::generated::kairos::capital::v_2 as fb;
use kairos_protocol::generated::kairos::common::v_2::Decimal64;
use pyo3::create_exception;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

create_exception!(
    _native_capital_contract,
    CapitalInvalidCurrentViewError,
    PyValueError
);
create_exception!(
    _native_capital_contract,
    CapitalCurrentViewUnavailableError,
    PyRuntimeError
);

const API_VERSION: u32 = 1;

#[pyclass(frozen, module = "kairospy._native_capital_contract")]
struct NativeBuildInfo {
    #[pyo3(get)]
    api_version: u32,
    #[pyo3(get)]
    owner: String,
    #[pyo3(get)]
    package_version: String,
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

#[pyclass(module = "kairospy._native_capital_contract")]
struct CapitalCurrentView {
    creator_pid: u32,
    reader: Mutex<Option<RustView>>,
}
#[pymethods]
impl CapitalCurrentView {
    #[new]
    #[pyo3(signature = (root, capital_group_id, workspace_id, launch_id=None, instance_id=None))]
    fn new(
        py: Python<'_>,
        root: PathBuf,
        capital_group_id: String,
        workspace_id: String,
        launch_id: Option<String>,
        instance_id: Option<String>,
    ) -> PyResult<Self> {
        let identity = identity(workspace_id, launch_id, instance_id)?;
        let group = CapitalGroupId::new(capital_group_id)
            .map_err(|error| PyValueError::new_err(error.to_string()))?;
        let reader = py
            .detach(move || RustView::open(root, &identity, group))
            .map_err(contract_error)?;
        Ok(Self {
            creator_pid: std::process::id(),
            reader: Mutex::new(Some(reader)),
        })
    }
    fn snapshot(&self, py: Python<'_>) -> PyResult<CapitalCurrentSnapshot> {
        self.ensure_process()?;
        py.detach(|| {
            let reader = self.reader.lock().map_err(|_| lock_error())?;
            let snapshot = reader
                .as_ref()
                .ok_or_else(|| PyRuntimeError::new_err("Capital current-view reader is closed"))?
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
impl CapitalCurrentView {
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

fn identity(
    workspace_id: String,
    launch_id: Option<String>,
    instance_id: Option<String>,
) -> PyResult<InstanceIdentity> {
    match (launch_id, instance_id) {
        (Some(l), Some(i)) => InstanceIdentity::new(workspace_id, l, i)
            .map_err(|e| PyValueError::new_err(e.to_string())),
        (None, None) => InstanceIdentity::unscoped(workspace_id)
            .map_err(|e| PyValueError::new_err(e.to_string())),
        _ => Err(PyValueError::new_err(
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
#[pyfunction]
fn build_info() -> NativeBuildInfo {
    NativeBuildInfo {
        api_version: API_VERSION,
        owner: "Capital".to_owned(),
        package_version: env!("CARGO_PKG_VERSION").to_owned(),
    }
}
#[pymodule]
fn _native_capital_contract(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add(
        "CapitalInvalidCurrentViewError",
        m.py().get_type::<CapitalInvalidCurrentViewError>(),
    )?;
    m.add(
        "CapitalCurrentViewUnavailableError",
        m.py().get_type::<CapitalCurrentViewUnavailableError>(),
    )?;
    m.add_class::<NativeBuildInfo>()?;
    m.add_class::<FundingLocation>()?;
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
    Ok(())
}
