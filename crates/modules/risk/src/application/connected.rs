//! Risk connected/runtime application facade.
//!
//! This facade is used by connected CLI entry points that talk to a running
//! Risk server through the module contract or read its published view. It must
//! not be used by standalone policy/schema/dry-run commands.

use kairos_risk_contract::{
    AdvanceRiskTimeRequest, AuthorizeRequest, CloseCircuitRequest, ConsumeReservationRequest,
    OpenCircuitRequest, PublishPolicyRequest, ReleaseReservationRequest, ResizeReservationRequest,
    RiskClient, RiskControlRpcClient, RiskLatestSnapshot,
};
use serde_json::{Value, json};

pub struct ConnectedRiskApplication {
    client: RiskClient,
}

impl ConnectedRiskApplication {
    pub fn connect(client: RiskClient) -> Self {
        Self { client }
    }

    pub async fn health(&self) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(self.client.control().health().await?)?)
    }

    pub fn latest(&self, actor_id: String) -> Result<Value, Box<dyn std::error::Error>> {
        let latest = self.client.latest(actor_id.clone())?;
        let snapshot = latest.read()?;
        latest_snapshot_json(actor_id, &snapshot)
    }

    pub fn limits(&self, actor_id: String) -> Result<Value, Box<dyn std::error::Error>> {
        let latest = self.client.latest(actor_id.clone())?;
        limits_snapshot_json(actor_id, &latest.read()?)
    }

    pub fn reservations(&self, actor_id: String) -> Result<Value, Box<dyn std::error::Error>> {
        let latest = self.client.latest(actor_id.clone())?;
        reservations_snapshot_json(actor_id, &latest.read()?)
    }

    pub fn circuits(&self, actor_id: String) -> Result<Value, Box<dyn std::error::Error>> {
        let latest = self.client.latest(actor_id.clone())?;
        circuits_snapshot_json(actor_id, &latest.read()?)
    }

    pub async fn pre_trade_check(
        &self,
        request: AuthorizeRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            self.client.control().pre_trade_check(request).await?,
        )?)
    }

    pub async fn authorize_and_reserve(
        &self,
        request: AuthorizeRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            self.client.control().authorize_and_reserve(request).await?,
        )?)
    }

    pub async fn release_reservation(
        &self,
        request: ReleaseReservationRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            self.client.control().release_reservation(request).await?,
        )?)
    }

    pub async fn consume_reservation(
        &self,
        request: ConsumeReservationRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            self.client.control().consume_reservation(request).await?,
        )?)
    }

    pub async fn resize_reservation(
        &self,
        request: ResizeReservationRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            self.client.control().resize_reservation(request).await?,
        )?)
    }

    pub async fn open_circuit(
        &self,
        request: OpenCircuitRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            self.client.control().open_circuit(request).await?,
        )?)
    }

    pub async fn close_circuit(
        &self,
        request: CloseCircuitRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            self.client.control().close_circuit(request).await?,
        )?)
    }

    pub async fn publish_policy(
        &self,
        request: PublishPolicyRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            self.client.control().publish_policy(request).await?,
        )?)
    }

    pub async fn advance_time(
        &self,
        request: AdvanceRiskTimeRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            self.client.control().advance_time(request).await?,
        )?)
    }
}

fn latest_snapshot_json(
    actor_id: String,
    snapshot: &RiskLatestSnapshot,
) -> Result<Value, Box<dyn std::error::Error>> {
    let metadata = snapshot.envelope_metadata();
    let view = snapshot.view()?;
    let state = view.state();
    let limits = state.limits().iter().map(limit_json).collect::<Vec<_>>();
    let active_reservations = state
        .active_reservations()
        .iter()
        .map(reservation_json)
        .collect::<Vec<_>>();
    let circuits = state
        .circuits()
        .iter()
        .map(circuit_json)
        .collect::<Vec<_>>();
    let open_circuit_count = circuits
        .iter()
        .filter(|value| value["status"] == "open")
        .count();
    Ok(json!({
        "actor_id": actor_id,
        "kind": "latest",
        "generation": snapshot.generation(),
        "policy_version": state.policy_version(),
        "limits": limits,
        "active_reservations": active_reservations,
        "circuits": circuits,
        "summary": {
            "limit_count": limits.len(),
            "active_reservation_count": active_reservations.len(),
            "open_circuit_count": open_circuit_count,
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

fn limits_snapshot_json(
    actor_id: String,
    snapshot: &RiskLatestSnapshot,
) -> Result<Value, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(json!({
        "actor_id": actor_id,
        "limits": state.limits().iter().map(limit_json).collect::<Vec<_>>(),
    }))
}

fn reservations_snapshot_json(
    actor_id: String,
    snapshot: &RiskLatestSnapshot,
) -> Result<Value, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(json!({
        "actor_id": actor_id,
        "active_reservations": state.active_reservations().iter().map(reservation_json).collect::<Vec<_>>(),
    }))
}

fn circuits_snapshot_json(
    actor_id: String,
    snapshot: &RiskLatestSnapshot,
) -> Result<Value, Box<dyn std::error::Error>> {
    let view = snapshot.view()?;
    let state = view.state();
    Ok(json!({
        "actor_id": actor_id,
        "circuits": state.circuits().iter().map(circuit_json).collect::<Vec<_>>(),
    }))
}

fn limit_json(value: kairos_protocol::generated::kairos::risk::v_2::LimitUsage<'_>) -> Value {
    json!({
        "policy": policy_json(value.policy()),
        "used": decimal_json(value.used()),
        "reserved": decimal_json(value.reserved()),
        "available": decimal_json(value.available()),
    })
}

fn policy_json(value: kairos_protocol::generated::kairos::risk::v_2::RiskPolicy<'_>) -> Value {
    json!({
        "policy_id": value.policy_id(),
        "version": value.version(),
        "scope": policy_scope_json(value.scope()),
        "metric": enum_name(value.metric().variant_name()),
        "limit": decimal_json(value.limit()),
        "enforcement": enum_name(value.enforcement().variant_name()),
        "valid_from_unix_nanos": value.valid_from_unix_nanos(),
        "valid_until_unix_nanos": value.valid_until_unix_nanos(),
        "window_nanos": value.window_nanos(),
    })
}

fn reservation_json(
    value: kairos_protocol::generated::kairos::risk::v_2::Reservation<'_>,
) -> Value {
    json!({
        "reservation_id": value.reservation_id(),
        "request_id": value.request_id(),
        "account_id": value.account_id(),
        "strategy_id": value.strategy_id(),
        "instrument_id": value.instrument_id(),
        "idempotency_key": value.idempotency_key(),
        "requested_usages": value.requested_usages().iter().map(risk_usage_json).collect::<Vec<_>>(),
        "allocations": value.allocations().iter().map(allocation_json).collect::<Vec<_>>(),
        "status": enum_name(value.status().variant_name()),
        "created_at_unix_nanos": value.created_at_unix_nanos(),
        "updated_at_unix_nanos": value.updated_at_unix_nanos(),
        "expires_at_unix_nanos": value.expires_at_unix_nanos(),
        "policy_version": value.policy_version(),
    })
}

fn risk_usage_json(value: kairos_protocol::generated::kairos::risk::v_2::RiskUsage<'_>) -> Value {
    json!({
        "metric": enum_name(value.metric().variant_name()),
        "amount": decimal_json(value.amount()),
    })
}

fn allocation_json(value: kairos_protocol::generated::kairos::risk::v_2::Allocation<'_>) -> Value {
    json!({
        "policy_id": value.policy_id(),
        "metric": enum_name(value.metric().variant_name()),
        "amount": decimal_json(value.amount()),
    })
}

fn circuit_json(value: kairos_protocol::generated::kairos::risk::v_2::CircuitState<'_>) -> Value {
    json!({
        "circuit_id": value.circuit_id(),
        "scope": circuit_scope_json(value.scope()),
        "status": enum_name(value.status().variant_name()),
        "opened_at_unix_nanos": value.opened_at_unix_nanos(),
        "reset_at_unix_nanos": value.reset_at_unix_nanos(),
        "reason": value.reason(),
    })
}

fn policy_scope_json(
    value: kairos_protocol::generated::kairos::risk::v_2::PolicyScope<'_>,
) -> Value {
    json!({
        "account_id": value.account_id(),
        "strategy_id": value.strategy_id(),
        "instrument_id": value.instrument_id(),
        "exchange_id": value.exchange_id(),
    })
}

fn circuit_scope_json(
    value: kairos_protocol::generated::kairos::risk::v_2::CircuitScope<'_>,
) -> Value {
    json!({
        "account_id": value.account_id(),
        "strategy_id": value.strategy_id(),
        "exchange_id": value.exchange_id(),
    })
}

fn decimal_json(value: &kairos_protocol::generated::kairos::common::v_2::Decimal64) -> String {
    rust_decimal::Decimal::new(value.mantissa(), value.scale().into()).to_string()
}

fn enum_name(value: Option<&str>) -> String {
    value.unwrap_or("UNKNOWN").to_ascii_lowercase()
}
