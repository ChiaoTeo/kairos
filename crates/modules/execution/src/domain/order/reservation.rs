//! Execution-side evidence for the Risk reservation saga.

use super::*;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum RiskReservationSagaStatus {
    AuthorizePending,
    Active,
    ResizePending,
    ReleasePending,
    ConsumePending,
    Released,
    Consumed,
    Expired,
    Failed,
    Uncertain,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct FundingRequirementEvidence {
    pub required_margin: Money,
    pub available_margin: Money,
    pub shortfall: Money,
    pub margin_rule_id: String,
    pub risk_decision_id: String,
    pub risk_policy_version: u64,
    pub account_snapshot_watermark: u64,
    pub broker: String,
    pub segment: String,
    pub collateral_asset: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RiskReservationEvidence {
    pub order_id: OrderId,
    pub reservation_id: String,
    pub idempotency_key: String,
    pub account_id: AccountId,
    pub amount: Money,
    pub status: RiskReservationSagaStatus,
    pub risk_generation: u64,
    pub risk_event_sequence: u64,
    pub policy_version: u64,
    pub expires_at_unix_nanos: UnixNanos,
    pub updated_at_unix_nanos: UnixNanos,
    #[serde(default)]
    pub funding_requirement: Option<FundingRequirementEvidence>,
}
