//! Intent identity, lifecycle, and completion/failure semantics.

use super::*;

/// Business-level intent kinds.  An intent describes an outcome; exchange orders
/// remain an Execution implementation detail.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum IntentType {
    SingleOrder,
    #[default]
    TargetPosition,
    PairArbitrage,
    OptionSpread,
    PortfolioRebalance,
    QuoteProvisioning,
    Hedge,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum CompletionPolicy {
    #[default]
    AllLegsSatisfied,
    AllOrNothing,
    BestEffort,
    HedgeWithinTolerance,
    TargetQuantityReached,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum FailurePolicy {
    #[default]
    CancelRemaining,
    ContinueOtherLegs,
    Compensate,
    PauseForManualIntervention,
    MarkReconciliationRequired,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum IntentLifecycle {
    Accepted,
    Planning,
    Planned,
    Executing,
    PartiallyFilled,
    Satisfied,
    Rejected,
    CancelRequested,
    Canceled,
    Expired,
    Failed,
    Compensating,
    ReconciliationRequired,
}

impl IntentLifecycle {
    pub fn terminal(self) -> bool {
        matches!(
            self,
            Self::Satisfied
                | Self::Rejected
                | Self::Canceled
                | Self::Expired
                | Self::Failed
                | Self::ReconciliationRequired
        )
    }

    pub fn can_transition_to(self, next: Self) -> bool {
        use IntentLifecycle::*;
        matches!(
            (self, next),
            (Accepted, Planning)
                | (Accepted, Rejected)
                | (Planning, Planned)
                | (Planning, Rejected)
                | (Planned, Executing)
                | (Planned, Rejected)
                | (Executing, PartiallyFilled)
                | (Executing, Satisfied)
                | (Executing, CancelRequested)
                | (Executing, Expired)
                | (Executing, Failed)
                | (Executing, Compensating)
                | (PartiallyFilled, Executing)
                | (PartiallyFilled, Satisfied)
                | (PartiallyFilled, CancelRequested)
                | (PartiallyFilled, Expired)
                | (PartiallyFilled, Failed)
                | (PartiallyFilled, Compensating)
                | (CancelRequested, Canceled)
                | (CancelRequested, PartiallyFilled)
                | (CancelRequested, Compensating)
                | (Compensating, Satisfied)
                | (Compensating, Failed)
                | (Compensating, ReconciliationRequired)
                | (_, ReconciliationRequired)
        )
    }
}
