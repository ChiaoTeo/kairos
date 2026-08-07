use serde::{Deserialize, Serialize};

use super::DecimalValue;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum IntentKind {
    TargetPosition,
    EnterLong,
    EnterShort,
    ExitPosition,
    ReducePosition,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum IntentStatus {
    Created,
    Accepted,
    Planned,
    Ordering,
    PartiallyFilled,
    Satisfied,
    Rejected,
    Canceled,
    Expired,
    Failed,
}

impl IntentStatus {
    pub fn active(self) -> bool {
        matches!(
            self,
            Self::Created | Self::Accepted | Self::Planned | Self::Ordering | Self::PartiallyFilled
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Intent {
    pub intent_id: String,
    pub strategy_id: String,
    pub account_id: String,
    pub segment_key: String,
    pub instrument_id: String,
    pub kind: IntentKind,
    pub target_quantity: Option<DecimalValue>,
    pub quantity: Option<DecimalValue>,
    pub limit_price: Option<DecimalValue>,
    pub created_at_unix_nanos: u64,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IntentEvent {
    pub intent_id: String,
    pub status: IntentStatus,
    pub order_ids: Vec<String>,
    pub occurred_at_unix_nanos: u64,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IntentState {
    pub intent: Intent,
    pub status: IntentStatus,
    pub order_ids: Vec<String>,
    pub updated_at_unix_nanos: u64,
    pub reason: String,
}

impl IntentState {
    pub fn new(intent: Intent) -> Result<Self, String> {
        if intent.intent_id.trim().is_empty()
            || intent.strategy_id.trim().is_empty()
            || intent.instrument_id.trim().is_empty()
            || intent.account_id.trim().is_empty()
            || intent.segment_key.trim().is_empty()
        {
            return Err("intent identity and account scope are required".into());
        }
        Ok(Self {
            updated_at_unix_nanos: intent.created_at_unix_nanos,
            intent,
            status: IntentStatus::Created,
            order_ids: Vec::new(),
            reason: String::new(),
        })
    }
    pub fn apply(&mut self, event: IntentEvent) -> Result<(), String> {
        if event.intent_id != self.intent.intent_id {
            return Err("intent event identity mismatch".into());
        }
        if !valid_transition(self.status, event.status) {
            return Err(format!(
                "illegal intent transition: {:?} -> {:?}",
                self.status, event.status
            ));
        }
        self.status = event.status;
        self.order_ids.extend(event.order_ids);
        self.order_ids.sort();
        self.order_ids.dedup();
        self.updated_at_unix_nanos = event.occurred_at_unix_nanos;
        self.reason = event.reason;
        Ok(())
    }
}

fn valid_transition(from: IntentStatus, to: IntentStatus) -> bool {
    if from == to && matches!(from, IntentStatus::PartiallyFilled) {
        return true;
    }
    matches!(
        (from, to),
        (
            IntentStatus::Created,
            IntentStatus::Created
                | IntentStatus::Accepted
                | IntentStatus::Planned
                | IntentStatus::Rejected
                | IntentStatus::Canceled
                | IntentStatus::Failed
        ) | (
            IntentStatus::Accepted,
            IntentStatus::Planned
                | IntentStatus::Satisfied
                | IntentStatus::Rejected
                | IntentStatus::Canceled
                | IntentStatus::Failed
        ) | (
            IntentStatus::Planned,
            IntentStatus::Ordering
                | IntentStatus::Satisfied
                | IntentStatus::Rejected
                | IntentStatus::Canceled
                | IntentStatus::Failed
        ) | (
            IntentStatus::Ordering,
            IntentStatus::PartiallyFilled
                | IntentStatus::Satisfied
                | IntentStatus::Rejected
                | IntentStatus::Canceled
                | IntentStatus::Expired
                | IntentStatus::Failed
        ) | (
            IntentStatus::PartiallyFilled,
            IntentStatus::Satisfied
                | IntentStatus::Canceled
                | IntentStatus::Expired
                | IntentStatus::Failed
        )
    )
}
