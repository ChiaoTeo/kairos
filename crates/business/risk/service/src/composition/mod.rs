use std::path::PathBuf;

use crate::domain::RiskPolicy;
use crate::services::actor::RiskActor;
use crate::RiskApplication;

/// Build the Risk application and select its concrete persistence mode.
///
/// Callers provide business configuration and an optional state path; they do
/// not construct actors or persistence objects directly.
pub fn compose_risk_application(
    actor_id: impl Into<String>,
    policies: Vec<RiskPolicy>,
    state_path: Option<PathBuf>,
) -> Result<RiskApplication, String> {
    let store = state_path
        .map(crate::services::persistence::JournalRiskStore::new)
        .map(|store| Box::new(store) as Box<dyn crate::services::persistence::RiskStateStore>);
    let actor = RiskActor::new(actor_id, policies, store)?;
    Ok(RiskApplication::new(actor))
}

pub struct FlatbuffersRiskSnapshotWriter {
    inner: kairos_risk_contract::FlatbuffersRiskSnapshotWriter,
    pub last_payload: Option<Vec<u8>>,
}

impl FlatbuffersRiskSnapshotWriter {
    pub fn new(actor_id: impl Into<String>) -> Self {
        Self {
            inner: kairos_risk_contract::FlatbuffersRiskSnapshotWriter::new(actor_id),
            last_payload: None,
        }
    }

    pub fn publish(&mut self, snapshot: &crate::RiskCurrentView) -> Result<(), String> {
        self.inner.publish(&risk_contract_current_view(snapshot))?;
        self.last_payload = self.inner.last_payload.clone();
        Ok(())
    }
}

pub struct FlatbuffersRiskEventWriter {
    inner: kairos_risk_contract::FlatbuffersRiskEventWriter,
    pub last_payload: Option<Vec<u8>>,
}

pub struct MmapRiskSnapshotPublisher {
    inner: kairos_risk_contract::MmapRiskSnapshotPublisher,
}

pub struct AeronRiskEventPublisher {
    inner: kairos_risk_contract::RiskAeronEventPublisher,
}

impl AeronRiskEventPublisher {
    pub fn connect(
        aeron_dir: Option<&str>,
        channel: &str,
        stream_id: i32,
        actor_id: impl Into<String>,
        identity: kairos_protocol::InstanceIdentity,
    ) -> Result<Self, String> {
        Ok(Self {
            inner: kairos_risk_contract::RiskAeronEventPublisher::connect(
                aeron_dir, channel, stream_id, actor_id, identity,
            )
            .map_err(|error| error.to_string())?,
        })
    }
}

impl crate::application::RiskEventPublisher for AeronRiskEventPublisher {
    fn publish(&mut self, event: &crate::RiskEvent) -> Result<(), String> {
        self.inner
            .publish(&risk_contract_event(event))
            .map_err(|error| error.to_string())
    }
}

impl crate::application::RiskSnapshotPublisher for MmapRiskSnapshotPublisher {
    fn publish(&mut self, snapshot: &crate::RiskCurrentView) -> Result<(), String> {
        MmapRiskSnapshotPublisher::publish(self, snapshot)
    }
}

impl MmapRiskSnapshotPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            inner: kairos_risk_contract::MmapRiskSnapshotPublisher::create(
                path, slot_size, actor_id,
            )
            .map_err(|error| error.to_string())?,
        })
    }

    pub fn publish(&mut self, snapshot: &crate::RiskCurrentView) -> Result<(), String> {
        self.inner
            .publish(&risk_contract_current_view(snapshot))
            .map_err(|error| error.to_string())
    }
}

impl FlatbuffersRiskEventWriter {
    pub fn new(actor_id: impl Into<String>) -> Self {
        Self {
            inner: kairos_risk_contract::FlatbuffersRiskEventWriter::new(actor_id),
            last_payload: None,
        }
    }

    pub fn new_with_identity(
        actor_id: impl Into<String>,
        identity: kairos_protocol::InstanceIdentity,
    ) -> Self {
        Self {
            inner: kairos_risk_contract::FlatbuffersRiskEventWriter::new_with_identity(
                actor_id, identity,
            ),
            last_payload: None,
        }
    }

    pub fn publish(&mut self, event: &crate::RiskEvent) -> Result<(), String> {
        self.inner.publish(&risk_contract_event(event))?;
        self.last_payload = self.inner.last_payload.clone();
        Ok(())
    }
}

fn risk_contract_amount(value: crate::Amount) -> kairos_risk_contract::Amount {
    kairos_risk_contract::Amount {
        mantissa: value.mantissa(),
        scale: value.scale(),
    }
}

fn risk_contract_money(value: kairos_domain_types::Money) -> kairos_risk_contract::Amount {
    kairos_risk_contract::Amount {
        mantissa: value.mantissa(),
        scale: value.scale(),
    }
}

fn risk_contract_metric(value: crate::Metric) -> kairos_risk_contract::Metric {
    use crate::Metric as Domain;
    use kairos_risk_contract::Metric as Contract;
    match value {
        Domain::Notional => Contract::Notional,
        Domain::Margin => Contract::Margin,
        Domain::GrossExposure => Contract::GrossExposure,
        Domain::NetExposure => Contract::NetExposure,
        Domain::Turnover => Contract::Turnover,
        Domain::OrderRate => Contract::OrderRate,
        Domain::DailyLoss => Contract::DailyLoss,
        Domain::Drawdown => Contract::Drawdown,
        Domain::Leverage => Contract::Leverage,
        Domain::PriceDeviation => Contract::PriceDeviation,
        Domain::StressLoss => Contract::StressLoss,
    }
}

fn risk_contract_policy(value: &crate::RiskPolicy) -> kairos_risk_contract::RiskPolicy {
    kairos_risk_contract::RiskPolicy {
        policy_id: value.policy_id.to_string(),
        version: value.version.get(),
        scope: kairos_risk_contract::PolicyScope {
            account_id: value.scope.account_id.as_ref().map(ToString::to_string),
            strategy_id: value.scope.strategy_id.as_ref().map(ToString::to_string),
            instrument_id: value.scope.instrument_id.as_ref().map(ToString::to_string),
            exchange_id: value.scope.exchange_id.as_ref().map(ToString::to_string),
        },
        metric: risk_contract_metric(value.metric),
        limit: risk_contract_amount(value.limit),
        enforcement: match value.enforcement {
            crate::EnforcementMode::Reject => kairos_risk_contract::EnforcementMode::Reject,
            crate::EnforcementMode::Warn => kairos_risk_contract::EnforcementMode::Warn,
            crate::EnforcementMode::Observe => kairos_risk_contract::EnforcementMode::Observe,
        },
        valid_from_unix_nanos: value.valid_from_unix_nanos.get(),
        valid_until_unix_nanos: value.valid_until_unix_nanos.map(|value| value.get()),
        window_nanos: value.window_nanos.map(|value| value.get()),
    }
}

fn risk_contract_allocation(value: &crate::Allocation) -> kairos_risk_contract::Allocation {
    kairos_risk_contract::Allocation {
        policy_id: value.policy_id.to_string(),
        metric: risk_contract_metric(value.metric),
        amount: risk_contract_amount(value.amount),
    }
}

fn risk_contract_reservation(value: &crate::Reservation) -> kairos_risk_contract::Reservation {
    kairos_risk_contract::Reservation {
        reservation_id: value.reservation_id.to_string(),
        request_id: value.request_id.to_string(),
        account_id: value.account_id.as_ref().map(ToString::to_string),
        strategy_id: value.strategy_id.as_ref().map(ToString::to_string),
        idempotency_key: value.idempotency_key.to_string(),
        allocations: value
            .allocations
            .iter()
            .map(risk_contract_allocation)
            .collect(),
        status: match value.status {
            crate::ReservationStatus::Reserved => kairos_risk_contract::ReservationStatus::Reserved,
            crate::ReservationStatus::Consumed => kairos_risk_contract::ReservationStatus::Consumed,
            crate::ReservationStatus::Released => kairos_risk_contract::ReservationStatus::Released,
            crate::ReservationStatus::Expired => kairos_risk_contract::ReservationStatus::Expired,
        },
        created_at_unix_nanos: value.created_at_unix_nanos.get(),
        updated_at_unix_nanos: value.updated_at_unix_nanos.get(),
        expires_at_unix_nanos: value.expires_at_unix_nanos.get(),
        policy_version: value.policy_version.get(),
    }
}

fn risk_contract_circuit(value: &crate::CircuitState) -> kairos_risk_contract::CircuitState {
    kairos_risk_contract::CircuitState {
        scope: kairos_risk_contract::CircuitScope {
            account_id: value.scope.account_id.as_ref().map(ToString::to_string),
            strategy_id: value.scope.strategy_id.as_ref().map(ToString::to_string),
            exchange_id: value.scope.exchange_id.as_ref().map(ToString::to_string),
        },
        open: value.open,
        opened_at_unix_nanos: value.opened_at_unix_nanos.map(|value| value.get()),
        reset_at_unix_nanos: value.reset_at_unix_nanos.map(|value| value.get()),
        reason: value.reason.clone(),
    }
}

fn risk_contract_context(value: &crate::RiskContext) -> kairos_risk_contract::RiskContext {
    kairos_risk_contract::RiskContext {
        account_snapshot_watermark: value.account_snapshot_watermark.get(),
        market_freshness_watermark: value.market_freshness_watermark.get(),
        portfolio_version: value.portfolio_version.get(),
        current_exposure: risk_contract_amount(value.current_exposure),
        current_margin: risk_contract_amount(value.current_margin),
        available_margin: risk_contract_amount(value.available_margin),
        current_pnl: risk_contract_money(value.current_pnl),
        current_drawdown: risk_contract_amount(value.current_drawdown),
        market_is_fresh: value.market_is_fresh,
        leverage_bps: u64::from(value.leverage_bps.get()),
        price_deviation_bps: u64::from(value.price_deviation_bps.get()),
        stress_loss: risk_contract_amount(value.stress_loss),
    }
}

fn risk_contract_reason(value: &crate::ReasonCode) -> kairos_risk_contract::ReasonCode {
    use crate::ReasonCode as Domain;
    use kairos_risk_contract::ReasonCode as Contract;
    match value {
        Domain::NoMatchingPolicy => Contract::NoMatchingPolicy,
        Domain::LimitExceeded => Contract::LimitExceeded,
        Domain::StaleDependency => Contract::StaleDependency,
        Domain::DuplicateRequest => Contract::DuplicateRequest,
        Domain::ReservationNotFound => Contract::ReservationNotFound,
        Domain::ReservationNotActive => Contract::ReservationNotActive,
        Domain::InvalidRequest => Contract::InvalidRequest,
        Domain::PersistenceFailure => Contract::PersistenceFailure,
        Domain::CircuitOpen => Contract::CircuitOpen,
        Domain::StaleMarket => Contract::StaleMarket,
        Domain::InsufficientMargin => Contract::InsufficientMargin,
        Domain::LeverageExceeded => Contract::LeverageExceeded,
        Domain::LossLimitExceeded => Contract::LossLimitExceeded,
    }
}

fn risk_contract_decision(
    value: &crate::RiskDecision,
    account_id: &kairos_domain_types::AccountId,
    strategy_id: &kairos_domain_types::StrategyId,
) -> kairos_risk_contract::RiskDecision {
    kairos_risk_contract::RiskDecision {
        decision_id: value.decision_id.to_string(),
        request_id: value.request_id.to_string(),
        account_id: account_id.to_string(),
        strategy_id: strategy_id.to_string(),
        instrument_id: String::new(),
        allowed: value.allowed,
        degraded: value.degraded,
        reason_codes: value
            .reason_codes
            .iter()
            .map(risk_contract_reason)
            .collect(),
        violations: value.violations.clone(),
        allocations: value
            .allocations
            .iter()
            .map(risk_contract_allocation)
            .collect(),
        reservation: value.reservation.as_ref().map(risk_contract_reservation),
        policy_version: value.policy_version.get(),
        dependency_watermarks: kairos_risk_contract::DependencyWatermarks {
            generation: value.dependency_watermarks.generation.get(),
            event_sequence: value.dependency_watermarks.event_sequence.get(),
        },
        context: value.context.as_ref().map(risk_contract_context),
        evaluated_at_unix_nanos: value.evaluated_at_unix_nanos.get(),
    }
}

fn risk_contract_current_view(
    value: &crate::RiskCurrentView,
) -> kairos_risk_contract::RiskCurrentView {
    kairos_risk_contract::RiskCurrentView {
        actor_id: value.actor_id.to_string(),
        generation: value.generation.get(),
        policy_version: value.policy_version.get(),
        limits: value
            .limits
            .iter()
            .map(|limit| kairos_risk_contract::LimitView {
                policy: risk_contract_policy(&limit.policy),
                used: risk_contract_amount(limit.used),
                reserved: risk_contract_amount(limit.reserved),
                available: risk_contract_amount(limit.available),
            })
            .collect(),
        reservations: value
            .reservations
            .iter()
            .map(risk_contract_reservation)
            .collect(),
        circuits: value.circuits.iter().map(risk_contract_circuit).collect(),
    }
}

fn risk_contract_event(value: &crate::RiskEvent) -> kairos_risk_contract::RiskEvent {
    match value {
        crate::RiskEvent::PolicyActivated {
            policy,
            event_sequence,
        } => kairos_risk_contract::RiskEvent::PolicyActivated {
            policy: risk_contract_policy(policy),
            event_sequence: event_sequence.get(),
        },
        crate::RiskEvent::ReservationChanged {
            reservation,
            event_sequence,
        } => kairos_risk_contract::RiskEvent::ReservationChanged {
            reservation: risk_contract_reservation(reservation),
            event_sequence: event_sequence.get(),
        },
        crate::RiskEvent::DecisionEvaluated {
            decision,
            account_id,
            strategy_id,
            event_sequence,
        } => kairos_risk_contract::RiskEvent::DecisionEvaluated {
            decision: risk_contract_decision(decision, account_id, strategy_id),
            account_id: account_id.to_string(),
            strategy_id: strategy_id.to_string(),
            event_sequence: event_sequence.get(),
        },
        crate::RiskEvent::CircuitChanged {
            circuit,
            event_sequence,
        } => kairos_risk_contract::RiskEvent::CircuitChanged {
            circuit: risk_contract_circuit(circuit),
            event_sequence: event_sequence.get(),
        },
    }
}
