//! Execution plan legs and leg lifecycle.

use super::*;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum LegLifecycle {
    Pending,
    Ready,
    Executing,
    PartiallyFilled,
    Satisfied,
    Canceled,
    Failed,
    Compensating,
}

impl LegLifecycle {
    pub fn terminal(self) -> bool {
        matches!(self, Self::Satisfied | Self::Canceled | Self::Failed)
    }

    pub fn can_transition_to(self, next: Self) -> bool {
        use LegLifecycle::*;
        matches!(
            (self, next),
            (Pending, Ready)
                | (Ready, Executing)
                | (Executing, PartiallyFilled)
                | (Executing, Satisfied)
                | (Executing, Canceled)
                | (Executing, Failed)
                | (Executing, Compensating)
                | (PartiallyFilled, Executing)
                | (PartiallyFilled, Satisfied)
                | (PartiallyFilled, Canceled)
                | (PartiallyFilled, Failed)
                | (PartiallyFilled, Compensating)
                | (Compensating, Satisfied)
                | (Compensating, Failed)
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionLeg {
    pub leg_id: LegId,
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
    pub side: OrderSide,
    pub target_quantity: Quantity,
    pub order_ids: Vec<OrderId>,
    pub lifecycle: LegLifecycle,
    pub completed_quantity: Quantity,
    pub reason: String,
}

impl ExecutionLeg {
    pub fn new(
        leg_id: impl Into<String>,
        account_id: impl Into<String>,
        segment_key: impl Into<String>,
        instrument_id: impl Into<String>,
        side: OrderSide,
        target_quantity: Quantity,
    ) -> Result<Self, IntentError> {
        let value = Self {
            leg_id: LegId::new(leg_id.into()).map_err(|source| IntentError::InvalidSemantic {
                field: "leg_id",
                source,
            })?,
            account_id: AccountId::new(account_id.into()).map_err(|source| {
                IntentError::InvalidSemantic {
                    field: "account_id",
                    source,
                }
            })?,
            segment_key: SegmentKey::new(segment_key.into()).map_err(|source| {
                IntentError::InvalidSemantic {
                    field: "segment_key",
                    source,
                }
            })?,
            instrument_id: InstrumentId::new(instrument_id.into()).map_err(|source| {
                IntentError::InvalidSemantic {
                    field: "instrument_id",
                    source,
                }
            })?,
            market_id: None,
            side,
            target_quantity,
            order_ids: Vec::new(),
            lifecycle: LegLifecycle::Pending,
            completed_quantity: Quantity::ZERO,
            reason: String::new(),
        };
        Ok(value)
    }

    pub fn transition(
        &mut self,
        next: LegLifecycle,
        reason: impl Into<String>,
    ) -> Result<(), IntentError> {
        if self.lifecycle != next && !self.lifecycle.can_transition_to(next) {
            return Err(IntentError::InvalidLegTransition {
                leg_id: self.leg_id.clone(),
                from: self.lifecycle,
                to: next,
            });
        }
        self.lifecycle = next;
        self.reason = reason.into();
        Ok(())
    }
}
