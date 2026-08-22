//! Capital connected/runtime application facade.
//!
//! This facade is used by connected CLI entry points that talk to a running
//! Capital server through the module contract or read its published current
//! view. Standalone CLI commands must use `CliCapitalApplication`.

use kairos_capital_contract::{
    CancelFundingObjectiveRequest, CapitalClient, CapitalControlRpcClient, CapitalCurrentSnapshot,
    ObserveCapitalDemandRequest, PublishFundingObjectiveRequest, QueryCapitalAvailabilityRequest,
    ReconcileCapitalPlanRequest,
};
use serde_json::{Value, json};

pub struct ConnectedCapitalApplication {
    client: CapitalClient,
}

impl ConnectedCapitalApplication {
    pub fn connect(client: CapitalClient) -> Self {
        Self { client }
    }

    pub async fn health(&self) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(self.client.control().health().await?)?)
    }

    pub fn current(&self, capital_group_id: String) -> Result<Value, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id)?;
        current_snapshot_json(&current.read()?)
    }

    pub fn objectives(
        &self,
        capital_group_id: String,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id.clone())?;
        objectives_snapshot_json(capital_group_id, &current.read()?)
    }

    pub fn demands(&self, capital_group_id: String) -> Result<Value, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id.clone())?;
        demands_snapshot_json(capital_group_id, &current.read()?)
    }

    pub fn availabilities(
        &self,
        capital_group_id: String,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id.clone())?;
        availabilities_snapshot_json(capital_group_id, &current.read()?)
    }

    pub fn routes(&self, capital_group_id: String) -> Result<Value, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id.clone())?;
        routes_snapshot_json(capital_group_id, &current.read()?)
    }

    pub fn plans(&self, capital_group_id: String) -> Result<Value, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id.clone())?;
        plans_snapshot_json(capital_group_id, &current.read()?)
    }

    pub fn reservations(
        &self,
        capital_group_id: String,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id.clone())?;
        reservations_snapshot_json(capital_group_id, &current.read()?)
    }

    pub fn operations(
        &self,
        capital_group_id: String,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id.clone())?;
        operations_snapshot_json(capital_group_id, &current.read()?)
    }

    pub fn alerts(&self, capital_group_id: String) -> Result<Value, Box<dyn std::error::Error>> {
        let current = self.client.current(capital_group_id.clone())?;
        alerts_snapshot_json(capital_group_id, &current.read()?)
    }

    pub async fn query_capital_availability(
        &self,
        request: QueryCapitalAvailabilityRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let response = self
            .client
            .control()
            .query_capital_availability(request)
            .await?;
        Ok(serde_json::to_value(response)?)
    }

    pub async fn publish_funding_objective(
        &self,
        request: PublishFundingObjectiveRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let response = self
            .client
            .control()
            .publish_funding_objective(request)
            .await?;
        Ok(serde_json::to_value(response)?)
    }

    pub async fn observe_capital_demand(
        &self,
        request: ObserveCapitalDemandRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            self.client
                .control()
                .observe_capital_demand(request)
                .await?,
        )?)
    }

    pub async fn cancel_funding_objective(
        &self,
        request: CancelFundingObjectiveRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let response = self
            .client
            .control()
            .cancel_funding_objective(request)
            .await?;
        Ok(serde_json::to_value(response)?)
    }

    pub async fn reconcile_capital_plan(
        &self,
        request: ReconcileCapitalPlanRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let response = self
            .client
            .control()
            .reconcile_capital_plan(request)
            .await?;
        Ok(serde_json::to_value(response)?)
    }
}

fn current_snapshot_json(
    snapshot: &CapitalCurrentSnapshot,
) -> Result<Value, Box<dyn std::error::Error>> {
    let metadata = snapshot.envelope_metadata();
    let view = snapshot.view()?;
    let state = view.state();
    Ok(json!({
        "capital_group_id": state.capital_group_id(),
        "kind": "current",
        "generation": metadata.generation,
        "applied_event_sequence": metadata.applied_event_sequence,
        "strategy_id": state.strategy_id(),
        "environment": state.environment(),
        "membership_version": state.membership_version(),
        "event_sequence": state.event_sequence(),
        "journal_sequence": state.journal_sequence(),
        "summary": {
            "objective_count": state.objectives().len(),
            "demand_count": state.demands().len(),
            "policy_count": state.policies().len(),
            "facts_count": state.facts().len(),
            "availability_count": state.availability().len(),
            "route_count": state.routes().len(),
            "plan_count": state.plans().len(),
            "reservation_count": state.reservations().len(),
            "operation_count": state.operations().len(),
            "alert_count": state.alerts().len(),
        },
        "envelope_metadata": {
            "resource_epoch": metadata.resource_epoch,
            "producer_incarnation": metadata.producer_incarnation,
            "generation": metadata.generation,
            "applied_event_sequence": metadata.applied_event_sequence,
            "published_at_unix_nanos": metadata.published_at_unix_nanos,
        }
    }))
}

fn availabilities_snapshot_json(
    capital_group_id: String,
    snapshot: &CapitalCurrentSnapshot,
) -> Result<Value, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(json!({
        "capital_group_id": capital_group_id,
        "availabilities": state.availability().iter().map(availability_json).collect::<Vec<_>>(),
    }))
}

fn objectives_snapshot_json(
    capital_group_id: String,
    snapshot: &CapitalCurrentSnapshot,
) -> Result<Value, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(json!({
        "capital_group_id": capital_group_id,
        "objectives": state.objectives().iter().map(objective_json).collect::<Vec<_>>(),
    }))
}

fn demands_snapshot_json(
    capital_group_id: String,
    snapshot: &CapitalCurrentSnapshot,
) -> Result<Value, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(json!({
        "capital_group_id": capital_group_id,
        "demands": state.demands().iter().map(demand_json).collect::<Vec<_>>(),
    }))
}

fn plans_snapshot_json(
    capital_group_id: String,
    snapshot: &CapitalCurrentSnapshot,
) -> Result<Value, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(json!({
        "capital_group_id": capital_group_id,
        "plans": state.plans().iter().map(plan_json).collect::<Vec<_>>(),
    }))
}

fn routes_snapshot_json(
    capital_group_id: String,
    snapshot: &CapitalCurrentSnapshot,
) -> Result<Value, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(json!({
        "capital_group_id": capital_group_id,
        "routes": state.routes().iter().map(route_json).collect::<Vec<_>>(),
    }))
}

fn reservations_snapshot_json(
    capital_group_id: String,
    snapshot: &CapitalCurrentSnapshot,
) -> Result<Value, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(json!({
        "capital_group_id": capital_group_id,
        "reservations": state.reservations().iter().map(reservation_json).collect::<Vec<_>>(),
    }))
}

fn operations_snapshot_json(
    capital_group_id: String,
    snapshot: &CapitalCurrentSnapshot,
) -> Result<Value, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(json!({
        "capital_group_id": capital_group_id,
        "operations": state.operations().iter().map(operation_json).collect::<Vec<_>>(),
    }))
}

fn alerts_snapshot_json(
    capital_group_id: String,
    snapshot: &CapitalCurrentSnapshot,
) -> Result<Value, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(json!({
        "capital_group_id": capital_group_id,
        "alerts": state.alerts().iter().map(alert_json).collect::<Vec<_>>(),
    }))
}

fn objective_json(
    value: kairos_protocol::generated::kairos::capital::v_2::FundingObjective<'_>,
) -> Value {
    json!({
        "objective_id": value.objective_id(),
        "version": value.version(),
        "strategy_id": value.strategy_id(),
        "destination": funding_location_json(value.destination()),
        "desired_available": decimal_json(value.desired_available()),
        "required_by_unix_nanos": value.required_by_unix_nanos(),
        "expires_at_unix_nanos": value.expires_at_unix_nanos(),
        "priority": enum_name(value.priority().variant_name()),
        "confidence_bps": value.confidence_bps(),
        "strategy_decision_id": value.strategy_decision_id(),
        "status": enum_name(value.status().variant_name()),
        "updated_at_unix_nanos": value.updated_at_unix_nanos(),
    })
}

fn demand_json(
    value: kairos_protocol::generated::kairos::capital::v_2::CapitalDemand<'_>,
) -> Value {
    json!({
        "demand_id": value.demand_id(),
        "idempotency_key": value.idempotency_key(),
        "strategy_id": value.strategy_id(),
        "destination": funding_location_json(value.destination()),
        "observed_shortfall": decimal_json(value.observed_shortfall()),
        "observed_at_unix_nanos": value.observed_at_unix_nanos(),
        "required_by_unix_nanos": value.required_by_unix_nanos(),
        "expires_at_unix_nanos": value.expires_at_unix_nanos(),
        "priority": enum_name(value.priority().variant_name()),
        "confidence_bps": value.confidence_bps(),
        "account_watermark": value.account_watermark(),
        "risk_watermark": value.risk_watermark(),
        "launch_id": value.launch_id(),
        "instance_id": value.instance_id(),
        "causal_references": string_vector(value.causal_references()),
        "status": enum_name(value.status().variant_name()),
        "updated_at_unix_nanos": value.updated_at_unix_nanos(),
    })
}

fn availability_json(
    value: kairos_protocol::generated::kairos::capital::v_2::CapitalAvailability<'_>,
) -> Value {
    json!({
        "location": funding_location_json(value.destination()),
        "readiness": enum_name(value.readiness().variant_name()),
        "policy_version": value.policy_version(),
        "active_objective_ids": string_vector(value.active_objective_ids()),
        "active_demand_ids": string_vector(value.active_demand_ids()),
        "funding_horizons": value.funding_horizons().iter().map(funding_horizon_json).collect::<Vec<_>>(),
        "desired_target": decimal_json(value.desired_target()),
        "observed_available": decimal_json(value.observed_available()),
        "effective_target": decimal_json(value.effective_target()),
        "deficit": decimal_json(value.deficit()),
        "deficit_observed_since_unix_nanos": value.deficit_observed_since_unix_nanos(),
        "cooldown_until_unix_nanos": value.cooldown_until_unix_nanos(),
        "account_watermark": value.account_watermark(),
        "risk_policy_version": value.risk_policy_version(),
        "risk_watermark": value.risk_watermark(),
        "evaluated_at_unix_nanos": value.evaluated_at_unix_nanos(),
        "reason": value.reason(),
    })
}

fn funding_location_json(
    value: kairos_protocol::generated::kairos::capital::v_2::FundingLocation<'_>,
) -> Value {
    json!({
        "broker": value.broker(),
        "account_id": value.account_id(),
        "segment": value.segment(),
        "asset": value.asset(),
    })
}

fn funding_horizon_json(
    value: kairos_protocol::generated::kairos::capital::v_2::CapitalFundingHorizon<'_>,
) -> Value {
    json!({
        "required_by_unix_nanos": value.required_by_unix_nanos(),
        "objective_ids": string_vector(value.objective_ids()),
        "demand_ids": string_vector(value.demand_ids()),
        "desired_available": decimal_json(value.desired_available()),
    })
}

fn route_json(value: kairos_protocol::generated::kairos::capital::v_2::CapitalRoute<'_>) -> Value {
    json!({
        "route_id": value.route_id(),
        "version": value.version(),
        "source": funding_location_json(value.source()),
        "destination": funding_location_json(value.destination()),
        "kind": enum_name(value.kind().variant_name()),
        "per_operation_limit": decimal_json(value.per_operation_limit()),
        "daily_limit": decimal_json(value.daily_limit()),
        "required_source_authority": value.required_source_authority(),
        "settlement_class": enum_name(value.settlement_class().variant_name()),
        "enabled": value.enabled(),
        "earn_product_id": value.earn_product_id(),
        "demand_guard_nanos": value.demand_guard_nanos(),
        "allow_unknown_redemption_quota": value.allow_unknown_redemption_quota(),
    })
}

fn plan_json(value: kairos_protocol::generated::kairos::capital::v_2::CapitalPlan<'_>) -> Value {
    json!({
        "plan_id": value.plan_id(),
        "rebalance_decision_id": value.rebalance_decision_id(),
        "route_id": value.route_id(),
        "route_version": value.route_version(),
        "route_kind": enum_name(value.route_kind().variant_name()),
        "source": funding_location_json(value.source()),
        "destination": funding_location_json(value.destination()),
        "amount": decimal_json(value.amount()),
        "objective_ids": string_vector(value.objective_ids()),
        "demand_ids": string_vector(value.demand_ids()),
        "reservation_id": value.reservation_id(),
        "idempotency_key": value.idempotency_key(),
        "selected_earn_product_id": value.selected_earn_product_id(),
        "source_account_watermark": value.source_account_watermark(),
        "destination_account_watermark": value.destination_account_watermark(),
        "source_observed_available": decimal_json(value.source_observed_available()),
        "destination_observed_available": decimal_json(value.destination_observed_available()),
        "redemption_account_watermark": value.redemption_account_watermark(),
        "redemption_observed_available": value.redemption_observed_available().map(decimal_json),
        "earn_principal_before": decimal_json(value.earn_principal_before()),
        "status": enum_name(value.status().variant_name()),
        "recovery_action": enum_name(value.recovery_action().variant_name()),
        "recovery_reason": value.recovery_reason(),
        "recovery_decided_at_unix_nanos": value.recovery_decided_at_unix_nanos(),
        "created_at_unix_nanos": value.created_at_unix_nanos(),
        "expires_at_unix_nanos": value.expires_at_unix_nanos(),
    })
}

fn reservation_json(
    value: kairos_protocol::generated::kairos::capital::v_2::CapitalReservation<'_>,
) -> Value {
    json!({
        "reservation_id": value.reservation_id(),
        "plan_id": value.plan_id(),
        "source": funding_location_json(value.source()),
        "amount": decimal_json(value.amount()),
        "source_account_watermark": value.source_account_watermark(),
        "status": enum_name(value.status().variant_name()),
        "created_at_unix_nanos": value.created_at_unix_nanos(),
        "expires_at_unix_nanos": value.expires_at_unix_nanos(),
    })
}

fn operation_json(
    value: kairos_protocol::generated::kairos::capital::v_2::CapitalOperation<'_>,
) -> Value {
    json!({
        "operation_id": value.operation_id(),
        "plan_id": value.plan_id(),
        "idempotency_key": value.idempotency_key(),
        "operation_index": value.operation_index(),
        "kind": enum_name(value.kind().variant_name()),
        "status": enum_name(value.status().variant_name()),
        "participant_operation_id": value.participant_operation_id(),
        "participant_state": value.participant_state(),
        "dispatch_started_at_unix_nanos": value.dispatch_started_at_unix_nanos(),
        "attempt_count": value.attempt_count(),
        "failure_reason": value.failure_reason(),
        "account_observation_watermark": value.account_observation_watermark(),
        "updated_at_unix_nanos": value.updated_at_unix_nanos(),
    })
}

fn alert_json(value: kairos_protocol::generated::kairos::capital::v_2::CapitalAlert<'_>) -> Value {
    json!({
        "alert_id": value.alert_id(),
        "plan_id": value.plan_id(),
        "operation_id": value.operation_id(),
        "kind": enum_name(value.kind().variant_name()),
        "severity": enum_name(value.severity().variant_name()),
        "recovery_action": enum_name(value.recovery_action().variant_name()),
        "message": value.message(),
        "opened_at_unix_nanos": value.opened_at_unix_nanos(),
    })
}

fn string_vector<'a>(
    values: flatbuffers::Vector<'a, flatbuffers::ForwardsUOffset<&'a str>>,
) -> Vec<&'a str> {
    values.iter().collect()
}

fn decimal_json(value: &kairos_protocol::generated::kairos::common::v_2::Decimal64) -> String {
    rust_decimal::Decimal::new(value.mantissa(), value.scale().into()).to_string()
}

fn enum_name(value: Option<&str>) -> String {
    value.unwrap_or("UNKNOWN").to_ascii_lowercase()
}
