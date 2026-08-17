use crate::application::{SnapshotWatermark, SubmitOrder};
use crate::domain::RiskReservationEvidence;
use kairos_primitives::{Money, UnixNanos};

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct RiskAuthorizationContext {
    pub account: SnapshotWatermark,
    pub market: Option<SnapshotWatermark>,
    pub market_is_fresh: bool,
    pub available_margin: Option<Money>,
}

#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum RiskCommandFailure {
    #[error("Risk command was not sent: {0}")]
    NotSent(String),
    #[error("Risk command was rejected: {0}")]
    Rejected(String),
    #[error("Risk command delivery is indeterminate: {0}")]
    Indeterminate(String),
}

impl RiskCommandFailure {
    pub fn indeterminate(message: impl Into<String>) -> Self {
        Self::Indeterminate(message.into())
    }

    pub fn may_have_been_applied(&self) -> bool {
        matches!(self, Self::Indeterminate(_))
    }
}

pub type RiskCommandResult<T> = Result<T, RiskCommandFailure>;

/// Focused command boundary for the Risk-owned reservation lifecycle.
/// Execution supplies its durable saga evidence for every follow-up command;
/// adapter-local maps are therefore never recovery state.
pub trait ExecutionRiskReservations: Send {
    fn authorize(
        &mut self,
        request: &SubmitOrder,
        context: &RiskAuthorizationContext,
    ) -> RiskCommandResult<RiskReservationEvidence>;

    fn reconcile(
        &mut self,
        evidence: &RiskReservationEvidence,
    ) -> Result<Option<RiskReservationEvidence>, String>;

    fn resize(
        &mut self,
        evidence: &RiskReservationEvidence,
        amount: Money,
        at: UnixNanos,
    ) -> RiskCommandResult<()>;

    fn release(
        &mut self,
        evidence: &RiskReservationEvidence,
        at: UnixNanos,
    ) -> RiskCommandResult<()>;

    fn consume(
        &mut self,
        evidence: &RiskReservationEvidence,
        at: UnixNanos,
    ) -> RiskCommandResult<()>;
}
