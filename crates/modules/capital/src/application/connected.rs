//! Capital connected/runtime application facade.
//!
//! This facade is used by connected CLI entry points that talk to a running
//! Capital server through the module contract or read its published current
//! view. Standalone CLI commands must use `CliCapitalApplication`.

use kairos_capital_contract::{
    CancelFundingObjectiveRequest, CapitalAvailabilityResponse, CapitalClient,
    CapitalControlResponse, CapitalControlRpcClient, CapitalCurrentSnapshot, CapitalDemandResponse,
    CapitalHealthResponse, ObserveCapitalDemandRequest, PublishFundingObjectiveRequest,
    QueryCapitalAvailabilityRequest, ReconcileCapitalPlanRequest, ReconcileCapitalPlanResponse,
};
use serde::Serialize;

pub struct ConnectedCapitalApplication {
    client: CapitalClient,
}

#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum ConnectedCapitalOutput {
    Health(CapitalHealthResponse),
    Current(CapitalCurrentResult),
    Objectives(CapitalObjectivesResult),
    Demands(CapitalDemandsResult),
    Availabilities(CapitalAvailabilitiesResult),
    Routes(CapitalRoutesResult),
    Plans(CapitalPlansResult),
    Reservations(CapitalReservationsResult),
    Operations(CapitalOperationsResult),
    Alerts(CapitalAlertsResult),
    Availability(CapitalAvailabilityResponse),
    Control(CapitalControlResponse),
    Demand(CapitalDemandResponse),
    Reconcile(ReconcileCapitalPlanResponse),
}

#[derive(Debug, Serialize)]
pub struct CapitalCurrentResult {
    pub capital_group_id: String,
    pub kind: &'static str,
    pub generation: u64,
    pub applied_event_sequence: u64,
    pub strategy_id: String,
    pub environment: String,
    pub membership_version: u64,
    pub event_sequence: u64,
    pub journal_sequence: u64,
    pub summary: CapitalCurrentSummary,
    pub envelope_metadata: CapitalEnvelopeMetadata,
}

#[derive(Debug, Serialize)]
pub struct CapitalCurrentSummary {
    pub objective_count: usize,
    pub demand_count: usize,
    pub policy_count: usize,
    pub facts_count: usize,
    pub availability_count: usize,
    pub route_count: usize,
    pub plan_count: usize,
    pub reservation_count: usize,
    pub operation_count: usize,
    pub alert_count: usize,
}

#[derive(Debug, Serialize)]
pub struct CapitalEnvelopeMetadata {
    pub resource_epoch: u64,
    pub producer_incarnation: u64,
    pub generation: u64,
    pub applied_event_sequence: u64,
    pub published_at_unix_nanos: u64,
}

macro_rules! capital_collection_result {
    ($name:ident, $field:ident, $item:ty) => {
        #[derive(Debug, Serialize)]
        pub struct $name {
            pub capital_group_id: String,
            pub $field: Vec<$item>,
        }
    };
}

capital_collection_result!(CapitalObjectivesResult, objectives, FundingObjectiveResult);
capital_collection_result!(CapitalDemandsResult, demands, CapitalDemandResult);
capital_collection_result!(
    CapitalAvailabilitiesResult,
    availabilities,
    CapitalAvailabilityResult
);
capital_collection_result!(CapitalRoutesResult, routes, CapitalRouteResult);
capital_collection_result!(CapitalPlansResult, plans, CapitalPlanResult);
capital_collection_result!(
    CapitalReservationsResult,
    reservations,
    CapitalReservationResult
);
capital_collection_result!(CapitalOperationsResult, operations, CapitalOperationResult);
capital_collection_result!(CapitalAlertsResult, alerts, CapitalAlertResult);

#[derive(Debug, Serialize)]
pub struct FundingLocationResult {
    pub broker: String,
    pub account_id: String,
    pub segment: String,
    pub asset: String,
}

#[derive(Debug, Serialize)]
pub struct FundingObjectiveResult {
    pub objective_id: String,
    pub version: u64,
    pub strategy_id: String,
    pub destination: FundingLocationResult,
    pub desired_available: String,
    pub required_by_unix_nanos: u64,
    pub expires_at_unix_nanos: u64,
    pub priority: String,
    pub confidence_bps: u16,
    pub strategy_decision_id: String,
    pub status: String,
    pub updated_at_unix_nanos: u64,
}

#[derive(Debug, Serialize)]
pub struct CapitalDemandResult {
    pub demand_id: String,
    pub idempotency_key: String,
    pub strategy_id: String,
    pub destination: FundingLocationResult,
    pub observed_shortfall: String,
    pub observed_at_unix_nanos: u64,
    pub required_by_unix_nanos: u64,
    pub expires_at_unix_nanos: u64,
    pub priority: String,
    pub confidence_bps: u16,
    pub account_watermark: u64,
    pub risk_watermark: u64,
    pub launch_id: String,
    pub instance_id: String,
    pub causal_references: Vec<String>,
    pub status: String,
    pub updated_at_unix_nanos: u64,
}

#[derive(Debug, Serialize)]
pub struct CapitalAvailabilityResult {
    pub location: FundingLocationResult,
    pub readiness: String,
    pub policy_version: u64,
    pub active_objective_ids: Vec<String>,
    pub active_demand_ids: Vec<String>,
    pub funding_horizons: Vec<CapitalFundingHorizonResult>,
    pub desired_target: String,
    pub observed_available: String,
    pub effective_target: String,
    pub deficit: String,
    pub deficit_observed_since_unix_nanos: Option<u64>,
    pub cooldown_until_unix_nanos: Option<u64>,
    pub account_watermark: u64,
    pub risk_policy_version: u64,
    pub risk_watermark: u64,
    pub evaluated_at_unix_nanos: u64,
    pub reason: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct CapitalFundingHorizonResult {
    pub required_by_unix_nanos: u64,
    pub objective_ids: Vec<String>,
    pub demand_ids: Vec<String>,
    pub desired_available: String,
}

#[derive(Debug, Serialize)]
pub struct CapitalRouteResult {
    pub route_id: String,
    pub version: u64,
    pub source: FundingLocationResult,
    pub destination: FundingLocationResult,
    pub kind: String,
    pub per_operation_limit: String,
    pub daily_limit: String,
    pub required_source_authority: String,
    pub settlement_class: String,
    pub enabled: bool,
    pub earn_product_id: Option<String>,
    pub demand_guard_nanos: u64,
    pub allow_unknown_redemption_quota: bool,
}

#[derive(Debug, Serialize)]
pub struct CapitalPlanResult {
    pub plan_id: String,
    pub rebalance_decision_id: String,
    pub route_id: String,
    pub route_version: u64,
    pub route_kind: String,
    pub source: FundingLocationResult,
    pub destination: FundingLocationResult,
    pub amount: String,
    pub objective_ids: Vec<String>,
    pub demand_ids: Vec<String>,
    pub reservation_id: String,
    pub idempotency_key: String,
    pub selected_earn_product_id: Option<String>,
    pub source_account_watermark: u64,
    pub destination_account_watermark: u64,
    pub source_observed_available: String,
    pub destination_observed_available: String,
    pub redemption_account_watermark: Option<u64>,
    pub redemption_observed_available: Option<String>,
    pub earn_principal_before: String,
    pub status: String,
    pub recovery_action: String,
    pub recovery_reason: Option<String>,
    pub recovery_decided_at_unix_nanos: Option<u64>,
    pub created_at_unix_nanos: u64,
    pub expires_at_unix_nanos: u64,
}

#[derive(Debug, Serialize)]
pub struct CapitalReservationResult {
    pub reservation_id: String,
    pub plan_id: String,
    pub source: FundingLocationResult,
    pub amount: String,
    pub source_account_watermark: u64,
    pub status: String,
    pub created_at_unix_nanos: u64,
    pub expires_at_unix_nanos: u64,
}

#[derive(Debug, Serialize)]
pub struct CapitalOperationResult {
    pub operation_id: String,
    pub plan_id: String,
    pub idempotency_key: String,
    pub operation_index: u32,
    pub kind: String,
    pub status: String,
    pub participant_operation_id: Option<String>,
    pub participant_state: Option<String>,
    pub dispatch_started_at_unix_nanos: Option<u64>,
    pub attempt_count: u32,
    pub failure_reason: Option<String>,
    pub account_observation_watermark: Option<u64>,
    pub updated_at_unix_nanos: u64,
}

#[derive(Debug, Serialize)]
pub struct CapitalAlertResult {
    pub alert_id: String,
    pub plan_id: String,
    pub operation_id: Option<String>,
    pub kind: String,
    pub severity: String,
    pub recovery_action: String,
    pub message: String,
    pub opened_at_unix_nanos: u64,
}

impl ConnectedCapitalApplication {
    pub fn connect(client: CapitalClient) -> Self {
        Self { client }
    }

    pub async fn health(&self) -> Result<CapitalHealthResponse, Box<dyn std::error::Error>> {
        Ok(self.client.control().health().await?)
    }

    pub fn current(
        &self,
        capital_group_id: String,
    ) -> Result<CapitalCurrentResult, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id)?;
        current_snapshot_result(&current.read()?)
    }

    pub fn objectives(
        &self,
        capital_group_id: String,
    ) -> Result<CapitalObjectivesResult, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id.clone())?;
        objectives_snapshot_result(capital_group_id, &current.read()?)
    }

    pub fn demands(
        &self,
        capital_group_id: String,
    ) -> Result<CapitalDemandsResult, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id.clone())?;
        demands_snapshot_result(capital_group_id, &current.read()?)
    }

    pub fn availabilities(
        &self,
        capital_group_id: String,
    ) -> Result<CapitalAvailabilitiesResult, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id.clone())?;
        availabilities_snapshot_result(capital_group_id, &current.read()?)
    }

    pub fn routes(
        &self,
        capital_group_id: String,
    ) -> Result<CapitalRoutesResult, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id.clone())?;
        routes_snapshot_result(capital_group_id, &current.read()?)
    }

    pub fn plans(
        &self,
        capital_group_id: String,
    ) -> Result<CapitalPlansResult, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id.clone())?;
        plans_snapshot_result(capital_group_id, &current.read()?)
    }

    pub fn reservations(
        &self,
        capital_group_id: String,
    ) -> Result<CapitalReservationsResult, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id.clone())?;
        reservations_snapshot_result(capital_group_id, &current.read()?)
    }

    pub fn operations(
        &self,
        capital_group_id: String,
    ) -> Result<CapitalOperationsResult, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id.clone())?;
        operations_snapshot_result(capital_group_id, &current.read()?)
    }

    pub fn alerts(
        &self,
        capital_group_id: String,
    ) -> Result<CapitalAlertsResult, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id.clone())?;
        alerts_snapshot_result(capital_group_id, &current.read()?)
    }

    pub async fn query_capital_availability(
        &self,
        request: QueryCapitalAvailabilityRequest,
    ) -> Result<CapitalAvailabilityResponse, Box<dyn std::error::Error>> {
        let response = self
            .client
            .control()
            .query_capital_availability(request)
            .await?;
        Ok(response)
    }

    pub async fn publish_funding_objective(
        &self,
        request: PublishFundingObjectiveRequest,
    ) -> Result<CapitalControlResponse, Box<dyn std::error::Error>> {
        let response = self
            .client
            .control()
            .publish_funding_objective(request)
            .await?;
        Ok(response)
    }

    pub async fn observe_capital_demand(
        &self,
        request: ObserveCapitalDemandRequest,
    ) -> Result<CapitalDemandResponse, Box<dyn std::error::Error>> {
        Ok(self
            .client
            .control()
            .observe_capital_demand(request)
            .await?)
    }

    pub async fn cancel_funding_objective(
        &self,
        request: CancelFundingObjectiveRequest,
    ) -> Result<CapitalControlResponse, Box<dyn std::error::Error>> {
        let response = self
            .client
            .control()
            .cancel_funding_objective(request)
            .await?;
        Ok(response)
    }

    pub async fn reconcile_capital_plan(
        &self,
        request: ReconcileCapitalPlanRequest,
    ) -> Result<ReconcileCapitalPlanResponse, Box<dyn std::error::Error>> {
        let response = self
            .client
            .control()
            .reconcile_capital_plan(request)
            .await?;
        Ok(response)
    }
}

fn current_snapshot_result(
    snapshot: &CapitalCurrentSnapshot,
) -> Result<CapitalCurrentResult, Box<dyn std::error::Error>> {
    let metadata = snapshot.envelope_metadata();
    let view = snapshot.view()?;
    let state = view.state();
    Ok(CapitalCurrentResult {
        capital_group_id: state.capital_group_id().to_owned(),
        kind: "current",
        generation: metadata.generation,
        applied_event_sequence: metadata.applied_event_sequence,
        strategy_id: state.strategy_id().to_owned(),
        environment: state.environment().to_owned(),
        membership_version: state.membership_version(),
        event_sequence: state.event_sequence(),
        journal_sequence: state.journal_sequence(),
        summary: CapitalCurrentSummary {
            objective_count: state.objectives().len(),
            demand_count: state.demands().len(),
            policy_count: state.policies().len(),
            facts_count: state.facts().len(),
            availability_count: state.availability().len(),
            route_count: state.routes().len(),
            plan_count: state.plans().len(),
            reservation_count: state.reservations().len(),
            operation_count: state.operations().len(),
            alert_count: state.alerts().len(),
        },
        envelope_metadata: CapitalEnvelopeMetadata {
            resource_epoch: metadata.resource_epoch,
            producer_incarnation: metadata.producer_incarnation,
            generation: metadata.generation,
            applied_event_sequence: metadata.applied_event_sequence,
            published_at_unix_nanos: metadata.published_at_unix_nanos,
        },
    })
}

fn availabilities_snapshot_result(
    capital_group_id: String,
    snapshot: &CapitalCurrentSnapshot,
) -> Result<CapitalAvailabilitiesResult, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(CapitalAvailabilitiesResult {
        capital_group_id,
        availabilities: state
            .availability()
            .iter()
            .map(availability_result)
            .collect(),
    })
}

fn objectives_snapshot_result(
    capital_group_id: String,
    snapshot: &CapitalCurrentSnapshot,
) -> Result<CapitalObjectivesResult, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(CapitalObjectivesResult {
        capital_group_id,
        objectives: state.objectives().iter().map(objective_result).collect(),
    })
}

fn demands_snapshot_result(
    capital_group_id: String,
    snapshot: &CapitalCurrentSnapshot,
) -> Result<CapitalDemandsResult, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(CapitalDemandsResult {
        capital_group_id,
        demands: state.demands().iter().map(demand_result).collect(),
    })
}

fn plans_snapshot_result(
    capital_group_id: String,
    snapshot: &CapitalCurrentSnapshot,
) -> Result<CapitalPlansResult, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(CapitalPlansResult {
        capital_group_id,
        plans: state.plans().iter().map(plan_result).collect(),
    })
}

fn routes_snapshot_result(
    capital_group_id: String,
    snapshot: &CapitalCurrentSnapshot,
) -> Result<CapitalRoutesResult, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(CapitalRoutesResult {
        capital_group_id,
        routes: state.routes().iter().map(route_result).collect(),
    })
}

fn reservations_snapshot_result(
    capital_group_id: String,
    snapshot: &CapitalCurrentSnapshot,
) -> Result<CapitalReservationsResult, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(CapitalReservationsResult {
        capital_group_id,
        reservations: state
            .reservations()
            .iter()
            .map(reservation_result)
            .collect(),
    })
}

fn operations_snapshot_result(
    capital_group_id: String,
    snapshot: &CapitalCurrentSnapshot,
) -> Result<CapitalOperationsResult, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(CapitalOperationsResult {
        capital_group_id,
        operations: state.operations().iter().map(operation_result).collect(),
    })
}

fn alerts_snapshot_result(
    capital_group_id: String,
    snapshot: &CapitalCurrentSnapshot,
) -> Result<CapitalAlertsResult, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(CapitalAlertsResult {
        capital_group_id,
        alerts: state.alerts().iter().map(alert_result).collect(),
    })
}

fn objective_result(
    value: kairos_protocol::generated::kairos::capital::v_2::FundingObjective<'_>,
) -> FundingObjectiveResult {
    FundingObjectiveResult {
        objective_id: value.objective_id().to_owned(),
        version: value.version(),
        strategy_id: value.strategy_id().to_owned(),
        destination: funding_location_result(value.destination()),
        desired_available: decimal_string(value.desired_available()),
        required_by_unix_nanos: value.required_by_unix_nanos(),
        expires_at_unix_nanos: value.expires_at_unix_nanos(),
        priority: enum_name(value.priority().variant_name()),
        confidence_bps: value.confidence_bps(),
        strategy_decision_id: value.strategy_decision_id().to_owned(),
        status: enum_name(value.status().variant_name()),
        updated_at_unix_nanos: value.updated_at_unix_nanos(),
    }
}

fn demand_result(
    value: kairos_protocol::generated::kairos::capital::v_2::CapitalDemand<'_>,
) -> CapitalDemandResult {
    CapitalDemandResult {
        demand_id: value.demand_id().to_owned(),
        idempotency_key: value.idempotency_key().to_owned(),
        strategy_id: value.strategy_id().to_owned(),
        destination: funding_location_result(value.destination()),
        observed_shortfall: decimal_string(value.observed_shortfall()),
        observed_at_unix_nanos: value.observed_at_unix_nanos(),
        required_by_unix_nanos: value.required_by_unix_nanos(),
        expires_at_unix_nanos: value.expires_at_unix_nanos(),
        priority: enum_name(value.priority().variant_name()),
        confidence_bps: value.confidence_bps(),
        account_watermark: value.account_watermark(),
        risk_watermark: value.risk_watermark(),
        launch_id: value.launch_id().to_owned(),
        instance_id: value.instance_id().to_owned(),
        causal_references: string_vector(value.causal_references()),
        status: enum_name(value.status().variant_name()),
        updated_at_unix_nanos: value.updated_at_unix_nanos(),
    }
}

fn availability_result(
    value: kairos_protocol::generated::kairos::capital::v_2::CapitalAvailability<'_>,
) -> CapitalAvailabilityResult {
    CapitalAvailabilityResult {
        location: funding_location_result(value.destination()),
        readiness: enum_name(value.readiness().variant_name()),
        policy_version: value.policy_version(),
        active_objective_ids: string_vector(value.active_objective_ids()),
        active_demand_ids: string_vector(value.active_demand_ids()),
        funding_horizons: value
            .funding_horizons()
            .iter()
            .map(funding_horizon_result)
            .collect(),
        desired_target: decimal_string(value.desired_target()),
        observed_available: decimal_string(value.observed_available()),
        effective_target: decimal_string(value.effective_target()),
        deficit: decimal_string(value.deficit()),
        deficit_observed_since_unix_nanos: value.deficit_observed_since_unix_nanos(),
        cooldown_until_unix_nanos: value.cooldown_until_unix_nanos(),
        account_watermark: value.account_watermark(),
        risk_policy_version: value.risk_policy_version(),
        risk_watermark: value.risk_watermark(),
        evaluated_at_unix_nanos: value.evaluated_at_unix_nanos(),
        reason: value.reason().map(str::to_owned),
    }
}

fn funding_location_result(
    value: kairos_protocol::generated::kairos::capital::v_2::FundingLocation<'_>,
) -> FundingLocationResult {
    FundingLocationResult {
        broker: value.broker().to_owned(),
        account_id: value.account_id().to_owned(),
        segment: value.segment().to_owned(),
        asset: value.asset().to_owned(),
    }
}

fn funding_horizon_result(
    value: kairos_protocol::generated::kairos::capital::v_2::CapitalFundingHorizon<'_>,
) -> CapitalFundingHorizonResult {
    CapitalFundingHorizonResult {
        required_by_unix_nanos: value.required_by_unix_nanos(),
        objective_ids: string_vector(value.objective_ids()),
        demand_ids: string_vector(value.demand_ids()),
        desired_available: decimal_string(value.desired_available()),
    }
}

fn route_result(
    value: kairos_protocol::generated::kairos::capital::v_2::CapitalRoute<'_>,
) -> CapitalRouteResult {
    CapitalRouteResult {
        route_id: value.route_id().to_owned(),
        version: value.version(),
        source: funding_location_result(value.source()),
        destination: funding_location_result(value.destination()),
        kind: enum_name(value.kind().variant_name()),
        per_operation_limit: decimal_string(value.per_operation_limit()),
        daily_limit: decimal_string(value.daily_limit()),
        required_source_authority: value.required_source_authority().to_owned(),
        settlement_class: enum_name(value.settlement_class().variant_name()),
        enabled: value.enabled(),
        earn_product_id: value.earn_product_id().map(str::to_owned),
        demand_guard_nanos: value.demand_guard_nanos(),
        allow_unknown_redemption_quota: value.allow_unknown_redemption_quota(),
    }
}

fn plan_result(
    value: kairos_protocol::generated::kairos::capital::v_2::CapitalPlan<'_>,
) -> CapitalPlanResult {
    CapitalPlanResult {
        plan_id: value.plan_id().to_owned(),
        rebalance_decision_id: value.rebalance_decision_id().to_owned(),
        route_id: value.route_id().to_owned(),
        route_version: value.route_version(),
        route_kind: enum_name(value.route_kind().variant_name()),
        source: funding_location_result(value.source()),
        destination: funding_location_result(value.destination()),
        amount: decimal_string(value.amount()),
        objective_ids: string_vector(value.objective_ids()),
        demand_ids: string_vector(value.demand_ids()),
        reservation_id: value.reservation_id().to_owned(),
        idempotency_key: value.idempotency_key().to_owned(),
        selected_earn_product_id: value.selected_earn_product_id().map(str::to_owned),
        source_account_watermark: value.source_account_watermark(),
        destination_account_watermark: value.destination_account_watermark(),
        source_observed_available: decimal_string(value.source_observed_available()),
        destination_observed_available: decimal_string(value.destination_observed_available()),
        redemption_account_watermark: value.redemption_account_watermark(),
        redemption_observed_available: value.redemption_observed_available().map(decimal_string),
        earn_principal_before: decimal_string(value.earn_principal_before()),
        status: enum_name(value.status().variant_name()),
        recovery_action: enum_name(value.recovery_action().variant_name()),
        recovery_reason: value.recovery_reason().map(str::to_owned),
        recovery_decided_at_unix_nanos: value.recovery_decided_at_unix_nanos(),
        created_at_unix_nanos: value.created_at_unix_nanos(),
        expires_at_unix_nanos: value.expires_at_unix_nanos(),
    }
}

fn reservation_result(
    value: kairos_protocol::generated::kairos::capital::v_2::CapitalReservation<'_>,
) -> CapitalReservationResult {
    CapitalReservationResult {
        reservation_id: value.reservation_id().to_owned(),
        plan_id: value.plan_id().to_owned(),
        source: funding_location_result(value.source()),
        amount: decimal_string(value.amount()),
        source_account_watermark: value.source_account_watermark(),
        status: enum_name(value.status().variant_name()),
        created_at_unix_nanos: value.created_at_unix_nanos(),
        expires_at_unix_nanos: value.expires_at_unix_nanos(),
    }
}

fn operation_result(
    value: kairos_protocol::generated::kairos::capital::v_2::CapitalOperation<'_>,
) -> CapitalOperationResult {
    CapitalOperationResult {
        operation_id: value.operation_id().to_owned(),
        plan_id: value.plan_id().to_owned(),
        idempotency_key: value.idempotency_key().to_owned(),
        operation_index: value.operation_index(),
        kind: enum_name(value.kind().variant_name()),
        status: enum_name(value.status().variant_name()),
        participant_operation_id: value.participant_operation_id().map(str::to_owned),
        participant_state: value.participant_state().map(str::to_owned),
        dispatch_started_at_unix_nanos: value.dispatch_started_at_unix_nanos(),
        attempt_count: value.attempt_count(),
        failure_reason: value.failure_reason().map(str::to_owned),
        account_observation_watermark: value.account_observation_watermark(),
        updated_at_unix_nanos: value.updated_at_unix_nanos(),
    }
}

fn alert_result(
    value: kairos_protocol::generated::kairos::capital::v_2::CapitalAlert<'_>,
) -> CapitalAlertResult {
    CapitalAlertResult {
        alert_id: value.alert_id().to_owned(),
        plan_id: value.plan_id().to_owned(),
        operation_id: value.operation_id().map(str::to_owned),
        kind: enum_name(value.kind().variant_name()),
        severity: enum_name(value.severity().variant_name()),
        recovery_action: enum_name(value.recovery_action().variant_name()),
        message: value.message().to_owned(),
        opened_at_unix_nanos: value.opened_at_unix_nanos(),
    }
}

fn string_vector<'a>(
    values: flatbuffers::Vector<'a, flatbuffers::ForwardsUOffset<&'a str>>,
) -> Vec<String> {
    values.iter().map(str::to_owned).collect()
}

fn decimal_string(value: &kairos_protocol::generated::kairos::common::v_2::Decimal64) -> String {
    rust_decimal::Decimal::new(value.mantissa(), value.scale().into()).to_string()
}

fn enum_name(value: Option<&str>) -> String {
    value.unwrap_or("UNKNOWN").to_ascii_lowercase()
}
