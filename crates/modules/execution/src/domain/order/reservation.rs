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
    pub risk_decision_id: kairos_primitives::risk::DecisionId,
    pub risk_policy_version: kairos_primitives::time::Generation,
    pub account_snapshot_watermark: UnixNanos,
    pub broker: kairos_primitives::account::BrokerId,
    pub segment: SegmentKey,
    pub collateral_asset: kairos_primitives::reference::Currency,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RiskReservationEvidence {
    pub order_id: OrderId,
    pub reservation_id: kairos_primitives::risk::ReservationId,
    pub idempotency_key: kairos_primitives::runtime::IdempotencyKey,
    pub account_id: AccountId,
    pub amount: Money,
    pub status: RiskReservationSagaStatus,
    pub risk_generation: kairos_primitives::time::Generation,
    pub risk_event_sequence: kairos_primitives::time::Sequence,
    pub policy_version: kairos_primitives::time::Generation,
    pub expires_at_unix_nanos: UnixNanos,
    pub updated_at_unix_nanos: UnixNanos,
    #[serde(default)]
    pub funding_requirement: Option<FundingRequirementEvidence>,
}
