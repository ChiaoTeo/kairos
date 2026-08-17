use super::SnapshotWatermark;
use kairos_primitives::Money;

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
