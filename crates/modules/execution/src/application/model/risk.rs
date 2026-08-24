use kairos_primitives::account::{BrokerId, SegmentKey};
use kairos_primitives::decimal::Money;
use kairos_primitives::reference::{Currency, ExchangeId};

use super::SnapshotWatermark;

pub type ExecutionFundingRequirement = crate::domain::FundingRequirementEvidence;

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct RiskAuthorizationContext {
    pub account: SnapshotWatermark,
    pub market: Option<SnapshotWatermark>,
    pub market_is_fresh: bool,
    pub available_margin: Option<Money>,
    pub initial_margin_rate_bps: Option<u32>,
    pub margin_rule_id: Option<String>,
    pub exchange_id: Option<ExchangeId>,
    pub funding_broker: Option<BrokerId>,
    pub funding_segment: Option<SegmentKey>,
    pub collateral_asset: Option<Currency>,
}

#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum RiskCommandFailure {
    #[error("Risk command was not sent: {0}")]
    NotSent(String),
    #[error("Risk command was rejected: {0}")]
    Rejected(String),
    #[error("Risk deferred order for insufficient funding: shortfall={requirement:?}")]
    DeferredInsufficientFunding {
        requirement: ExecutionFundingRequirement,
    },
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
