use kairos_primitives::DomainTypeError;
use kairos_primitives::decimal::Quantity;
use kairos_primitives::execution::{ExecutionRouteId, LegId};

use super::LegLifecycle;

#[derive(Debug, thiserror::Error)]
pub enum IntentError {
    #[error("invalid execution intent {field}: {source}")]
    InvalidSemantic {
        field: &'static str,
        #[source]
        source: DomainTypeError,
    },
    #[error("split quantity must be positive")]
    SplitQuantityNotPositive { total: Quantity },
    #[error("split policy quantities and child count must be positive")]
    SplitPolicyNonPositive,
    #[error("split policy minimum exceeds maximum child quantity")]
    SplitMinimumExceedsMaximum,
    #[error("split quantity arithmetic overflow during {operation}")]
    SplitOverflow { operation: &'static str },
    #[error("split policy produces an invalid child count")]
    InvalidChildCount,
    #[error("split policy minimum child quantity cannot be satisfied")]
    MinimumChildQuantityUnsatisfied,
    #[error("execution plan requires at least one leg")]
    EmptyPlan,
    #[error("execution plan contains duplicate leg {leg_id}")]
    DuplicateLeg { leg_id: LegId },
    #[error("invalid execution leg {leg_id} transition: {from:?} -> {to:?}")]
    InvalidLegTransition {
        leg_id: LegId,
        from: LegLifecycle,
        to: LegLifecycle,
    },
    #[error("passive-limit policy intervals must be positive")]
    InvalidPassiveLimitPolicy,
    #[error("TWAP requires at least two slices and a positive interval")]
    InvalidTwapPolicy,
    #[error("maker execution policy contains an invalid limit")]
    InvalidMakerPolicy,
    #[error("hedge policy contains invalid identities, duration, or retry limits")]
    InvalidHedgePolicy,
    #[error("hedge fallback execution route {route_id} is duplicated")]
    DuplicateHedgeRoute { route_id: ExecutionRouteId },
    #[error("intent {intent_id} is missing while {operation}")]
    MissingIntent {
        intent_id: String,
        operation: &'static str,
    },
    #[error("idempotency key {key} references missing intent {intent_id}")]
    MissingIdempotentIntent { key: String, intent_id: String },
    #[error("intent {intent_id} has no execution plan")]
    MissingPlan { intent_id: String },
    #[error("intent {intent_id} plan has no leg {leg_id}")]
    MissingLeg { intent_id: String, leg_id: String },
    #[error("intent {intent_id} event changed its strategy decision identity")]
    StrategyDecisionIdentityChanged { intent_id: String },
    #[error("execution intent arithmetic failed during {operation}: {source}")]
    Arithmetic {
        operation: &'static str,
        #[source]
        source: DomainTypeError,
    },
}

impl IntentError {
    pub const fn code(&self) -> &'static str {
        match self {
            Self::InvalidSemantic { .. } => "execution.intent.invalid_semantic",
            Self::SplitQuantityNotPositive { .. } => "execution.intent.split_quantity_not_positive",
            Self::SplitPolicyNonPositive => "execution.intent.split_policy_non_positive",
            Self::SplitMinimumExceedsMaximum => "execution.intent.split_minimum_exceeds_maximum",
            Self::SplitOverflow { .. } => "execution.intent.split_overflow",
            Self::InvalidChildCount => "execution.intent.invalid_child_count",
            Self::MinimumChildQuantityUnsatisfied => {
                "execution.intent.minimum_child_quantity_unsatisfied"
            },
            Self::EmptyPlan => "execution.intent.empty_plan",
            Self::DuplicateLeg { .. } => "execution.intent.duplicate_leg",
            Self::InvalidLegTransition { .. } => "execution.intent.invalid_leg_transition",
            Self::InvalidPassiveLimitPolicy => "execution.intent.invalid_passive_limit_policy",
            Self::InvalidTwapPolicy => "execution.intent.invalid_twap_policy",
            Self::InvalidMakerPolicy => "execution.intent.invalid_maker_policy",
            Self::InvalidHedgePolicy => "execution.intent.invalid_hedge_policy",
            Self::DuplicateHedgeRoute { .. } => "execution.intent.duplicate_hedge_route",
            Self::MissingIntent { .. } => "execution.intent.missing",
            Self::MissingIdempotentIntent { .. } => {
                "execution.intent.idempotency_references_missing"
            },
            Self::MissingPlan { .. } => "execution.intent.missing_plan",
            Self::MissingLeg { .. } => "execution.intent.missing_leg",
            Self::StrategyDecisionIdentityChanged { .. } => {
                "execution.intent.strategy_decision_identity_changed"
            },
            Self::Arithmetic { .. } => "execution.intent.arithmetic",
        }
    }
}
