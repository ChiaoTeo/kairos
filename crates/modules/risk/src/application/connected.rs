//! Risk connected/runtime application facade.
//!
//! This facade is used by connected CLI entry points that talk to a running
//! Risk server through the module contract or read its published view. It must
//! not be used by standalone policy/schema/dry-run commands.

use kairos_risk_contract::{
    AdvanceRiskTimeRequest, AdvanceRiskTimeResponse, AuthorizeRequest, CircuitState,
    CloseCircuitRequest, ConsumeReservationRequest, Health, OpenCircuitRequest,
    PublishPolicyRequest, ReleaseReservationRequest, Reservation, ResizeReservationRequest,
    RiskClient, RiskCommandStatus, RiskControlRpcClient, RiskDecision, RiskLatestSnapshot,
};
use serde::Serialize;

pub struct ConnectedRiskApplication {
    client: RiskClient,
}

#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum ConnectedRiskOutput {
    Health(Health),
    Latest(RiskLatestResult),
    Limits(RiskLimitsResult),
    Reservations(RiskReservationsResult),
    Circuits(RiskCircuitsResult),
    Decision(RiskDecision),
    Reservation(Reservation),
    Circuit(CircuitState),
    Command(RiskCommandStatus),
    AdvanceTime(AdvanceRiskTimeResponse),
}

impl ConnectedRiskApplication {
    pub fn connect(client: RiskClient) -> Self {
        Self { client }
    }

    pub async fn health(&self) -> Result<Health, Box<dyn std::error::Error>> {
        Ok(self.client.control().health().await?)
    }

    pub fn latest(&self, actor_id: String) -> Result<RiskLatestResult, Box<dyn std::error::Error>> {
        let latest = self.client.latest(actor_id.clone())?;
        let snapshot = latest.read()?;
        latest_snapshot_json(actor_id, &snapshot)
    }

    pub fn limits(&self, actor_id: String) -> Result<RiskLimitsResult, Box<dyn std::error::Error>> {
        let latest = self.client.latest(actor_id.clone())?;
        limits_snapshot_json(actor_id, &latest.read()?)
    }

    pub fn reservations(
        &self,
        actor_id: String,
    ) -> Result<RiskReservationsResult, Box<dyn std::error::Error>> {
        let latest = self.client.latest(actor_id.clone())?;
        reservations_snapshot_json(actor_id, &latest.read()?)
    }

    pub fn circuits(
        &self,
        actor_id: String,
    ) -> Result<RiskCircuitsResult, Box<dyn std::error::Error>> {
        let latest = self.client.latest(actor_id.clone())?;
        circuits_snapshot_json(actor_id, &latest.read()?)
    }

    pub async fn pre_trade_check(
        &self,
        request: AuthorizeRequest,
    ) -> Result<RiskDecision, Box<dyn std::error::Error>> {
        Ok(self.client.control().pre_trade_check(request).await?)
    }

    pub async fn authorize_and_reserve(
        &self,
        request: AuthorizeRequest,
    ) -> Result<RiskDecision, Box<dyn std::error::Error>> {
        Ok(self.client.control().authorize_and_reserve(request).await?)
    }

    pub async fn release_reservation(
        &self,
        request: ReleaseReservationRequest,
    ) -> Result<Reservation, Box<dyn std::error::Error>> {
        Ok(self.client.control().release_reservation(request).await?)
    }

    pub async fn consume_reservation(
        &self,
        request: ConsumeReservationRequest,
    ) -> Result<Reservation, Box<dyn std::error::Error>> {
        Ok(self.client.control().consume_reservation(request).await?)
    }

    pub async fn resize_reservation(
        &self,
        request: ResizeReservationRequest,
    ) -> Result<Reservation, Box<dyn std::error::Error>> {
        Ok(self.client.control().resize_reservation(request).await?)
    }

    pub async fn open_circuit(
        &self,
        request: OpenCircuitRequest,
    ) -> Result<CircuitState, Box<dyn std::error::Error>> {
        Ok(self.client.control().open_circuit(request).await?)
    }

    pub async fn close_circuit(
        &self,
        request: CloseCircuitRequest,
    ) -> Result<CircuitState, Box<dyn std::error::Error>> {
        Ok(self.client.control().close_circuit(request).await?)
    }

    pub async fn publish_policy(
        &self,
        request: PublishPolicyRequest,
    ) -> Result<RiskCommandStatus, Box<dyn std::error::Error>> {
        Ok(self.client.control().publish_policy(request).await?)
    }

    pub async fn advance_time(
        &self,
        request: AdvanceRiskTimeRequest,
    ) -> Result<AdvanceRiskTimeResponse, Box<dyn std::error::Error>> {
        Ok(self.client.control().advance_time(request).await?)
    }
}

#[derive(Debug, Serialize)]
pub struct RiskLatestResult {
    pub actor_id: String,
    pub kind: &'static str,
    pub generation: u64,
    pub policy_version: u64,
    pub limits: Vec<LimitResult>,
    pub active_reservations: Vec<ReservationResult>,
    pub circuits: Vec<CircuitResult>,
    pub summary: RiskLatestSummary,
    pub envelope_metadata: EnvelopeMetadataResult,
}

#[derive(Debug, Serialize)]
pub struct RiskLatestSummary {
    pub limit_count: usize,
    pub active_reservation_count: usize,
    pub open_circuit_count: usize,
}

#[derive(Debug, Serialize)]
pub struct RiskLimitsResult {
    pub actor_id: String,
    pub limits: Vec<LimitResult>,
}

#[derive(Debug, Serialize)]
pub struct RiskReservationsResult {
    pub actor_id: String,
    pub active_reservations: Vec<ReservationResult>,
}

#[derive(Debug, Serialize)]
pub struct RiskCircuitsResult {
    pub actor_id: String,
    pub circuits: Vec<CircuitResult>,
}

#[derive(Debug, Serialize)]
pub struct EnvelopeMetadataResult {
    pub resource_epoch: u64,
    pub producer_incarnation: u64,
    pub generation: u64,
    pub applied_event_sequence: u64,
    pub published_at_unix_nanos: u64,
}

#[derive(Debug, Serialize)]
pub struct LimitResult {
    pub policy: PolicyResult,
    pub used: String,
    pub reserved: String,
    pub available: String,
}

#[derive(Debug, Serialize)]
pub struct PolicyResult {
    pub policy_id: String,
    pub version: u64,
    pub scope: PolicyScopeResult,
    pub metric: String,
    pub limit: String,
    pub enforcement: String,
    pub valid_from_unix_nanos: u64,
    pub valid_until_unix_nanos: Option<u64>,
    pub window_nanos: Option<u64>,
}

#[derive(Debug, Serialize)]
pub struct PolicyScopeResult {
    pub account_id: Option<String>,
    pub strategy_id: Option<String>,
    pub instrument_id: Option<String>,
    pub exchange_id: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct ReservationResult {
    pub reservation_id: String,
    pub request_id: String,
    pub account_id: String,
    pub strategy_id: String,
    pub instrument_id: String,
    pub idempotency_key: String,
    pub requested_usages: Vec<RiskUsageResult>,
    pub allocations: Vec<AllocationResult>,
    pub status: String,
    pub created_at_unix_nanos: u64,
    pub updated_at_unix_nanos: u64,
    pub expires_at_unix_nanos: u64,
    pub policy_version: u64,
}

#[derive(Debug, Serialize)]
pub struct RiskUsageResult {
    pub metric: String,
    pub amount: String,
}

#[derive(Debug, Serialize)]
pub struct AllocationResult {
    pub policy_id: String,
    pub metric: String,
    pub amount: String,
}

#[derive(Debug, Serialize)]
pub struct CircuitResult {
    pub circuit_id: String,
    pub scope: CircuitScopeResult,
    pub status: String,
    pub opened_at_unix_nanos: Option<u64>,
    pub reset_at_unix_nanos: Option<u64>,
    pub reason: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct CircuitScopeResult {
    pub account_id: Option<String>,
    pub strategy_id: Option<String>,
    pub exchange_id: Option<String>,
}

fn latest_snapshot_json(
    actor_id: String,
    snapshot: &RiskLatestSnapshot,
) -> Result<RiskLatestResult, Box<dyn std::error::Error>> {
    let metadata = snapshot.envelope_metadata();
    let view = snapshot.view()?;
    let state = view.state();
    let limits = state.limits().iter().map(limit_result).collect::<Vec<_>>();
    let active_reservations = state
        .active_reservations()
        .iter()
        .map(reservation_result)
        .collect::<Vec<_>>();
    let circuits = state
        .circuits()
        .iter()
        .map(circuit_result)
        .collect::<Vec<_>>();
    let open_circuit_count = circuits
        .iter()
        .filter(|value| value.status == "open")
        .count();
    Ok(RiskLatestResult {
        actor_id,
        kind: "latest",
        generation: snapshot.generation(),
        policy_version: state.policy_version(),
        summary: RiskLatestSummary {
            limit_count: limits.len(),
            active_reservation_count: active_reservations.len(),
            open_circuit_count,
        },
        limits,
        active_reservations,
        circuits,
        envelope_metadata: EnvelopeMetadataResult {
            resource_epoch: metadata.resource_epoch,
            producer_incarnation: metadata.producer_incarnation,
            generation: metadata.generation,
            applied_event_sequence: metadata.applied_event_sequence,
            published_at_unix_nanos: metadata.published_at_unix_nanos,
        },
    })
}

fn limits_snapshot_json(
    actor_id: String,
    snapshot: &RiskLatestSnapshot,
) -> Result<RiskLimitsResult, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(RiskLimitsResult {
        actor_id,
        limits: state.limits().iter().map(limit_result).collect(),
    })
}

fn reservations_snapshot_json(
    actor_id: String,
    snapshot: &RiskLatestSnapshot,
) -> Result<RiskReservationsResult, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(RiskReservationsResult {
        actor_id,
        active_reservations: state
            .active_reservations()
            .iter()
            .map(reservation_result)
            .collect(),
    })
}

fn circuits_snapshot_json(
    actor_id: String,
    snapshot: &RiskLatestSnapshot,
) -> Result<RiskCircuitsResult, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(RiskCircuitsResult {
        actor_id,
        circuits: state.circuits().iter().map(circuit_result).collect(),
    })
}

fn limit_result(
    value: kairos_protocol::generated::kairos::risk::v_2::LimitUsage<'_>,
) -> LimitResult {
    LimitResult {
        policy: policy_result(value.policy()),
        used: decimal_string(value.used()),
        reserved: decimal_string(value.reserved()),
        available: decimal_string(value.available()),
    }
}

fn policy_result(
    value: kairos_protocol::generated::kairos::risk::v_2::RiskPolicy<'_>,
) -> PolicyResult {
    PolicyResult {
        policy_id: value.policy_id().to_owned(),
        version: value.version(),
        scope: policy_scope_result(value.scope()),
        metric: enum_name(value.metric().variant_name()),
        limit: decimal_string(value.limit()),
        enforcement: enum_name(value.enforcement().variant_name()),
        valid_from_unix_nanos: value.valid_from_unix_nanos(),
        valid_until_unix_nanos: value.valid_until_unix_nanos(),
        window_nanos: value.window_nanos(),
    }
}

fn reservation_result(
    value: kairos_protocol::generated::kairos::risk::v_2::Reservation<'_>,
) -> ReservationResult {
    ReservationResult {
        reservation_id: value.reservation_id().to_owned(),
        request_id: value.request_id().to_owned(),
        account_id: value.account_id().to_owned(),
        strategy_id: value.strategy_id().to_owned(),
        instrument_id: value.instrument_id().to_owned(),
        idempotency_key: value.idempotency_key().to_owned(),
        requested_usages: value
            .requested_usages()
            .iter()
            .map(risk_usage_result)
            .collect(),
        allocations: value.allocations().iter().map(allocation_result).collect(),
        status: enum_name(value.status().variant_name()),
        created_at_unix_nanos: value.created_at_unix_nanos(),
        updated_at_unix_nanos: value.updated_at_unix_nanos(),
        expires_at_unix_nanos: value.expires_at_unix_nanos(),
        policy_version: value.policy_version(),
    }
}

fn risk_usage_result(
    value: kairos_protocol::generated::kairos::risk::v_2::RiskUsage<'_>,
) -> RiskUsageResult {
    RiskUsageResult {
        metric: enum_name(value.metric().variant_name()),
        amount: decimal_string(value.amount()),
    }
}

fn allocation_result(
    value: kairos_protocol::generated::kairos::risk::v_2::Allocation<'_>,
) -> AllocationResult {
    AllocationResult {
        policy_id: value.policy_id().to_owned(),
        metric: enum_name(value.metric().variant_name()),
        amount: decimal_string(value.amount()),
    }
}

fn circuit_result(
    value: kairos_protocol::generated::kairos::risk::v_2::CircuitState<'_>,
) -> CircuitResult {
    CircuitResult {
        circuit_id: value.circuit_id().to_owned(),
        scope: circuit_scope_result(value.scope()),
        status: enum_name(value.status().variant_name()),
        opened_at_unix_nanos: value.opened_at_unix_nanos(),
        reset_at_unix_nanos: value.reset_at_unix_nanos(),
        reason: value.reason().map(str::to_owned),
    }
}

fn policy_scope_result(
    value: kairos_protocol::generated::kairos::risk::v_2::PolicyScope<'_>,
) -> PolicyScopeResult {
    PolicyScopeResult {
        account_id: value.account_id().map(str::to_owned),
        strategy_id: value.strategy_id().map(str::to_owned),
        instrument_id: value.instrument_id().map(str::to_owned),
        exchange_id: value.exchange_id().map(str::to_owned),
    }
}

fn circuit_scope_result(
    value: kairos_protocol::generated::kairos::risk::v_2::CircuitScope<'_>,
) -> CircuitScopeResult {
    CircuitScopeResult {
        account_id: value.account_id().map(str::to_owned),
        strategy_id: value.strategy_id().map(str::to_owned),
        exchange_id: value.exchange_id().map(str::to_owned),
    }
}

fn decimal_string(value: &kairos_protocol::generated::kairos::common::v_2::Decimal64) -> String {
    rust_decimal::Decimal::new(value.mantissa(), value.scale().into()).to_string()
}

fn enum_name(value: Option<&str>) -> String {
    value.unwrap_or("UNKNOWN").to_ascii_lowercase()
}
