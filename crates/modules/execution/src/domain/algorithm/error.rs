use kairos_primitives::DomainTypeError;

use super::AlgorithmDecisionSequence;
use crate::domain::IntentError;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AlgorithmInvariant {
    RunIdRequired,
    LeaderAndHedgeMustDiffer,
    MaximumUnhedgedDurationNotPositive,
    DuplicateFallbackRoute,
    PassiveLimitIntervalNotPositive,
    InvalidTwapSchedule,
    TwapSliceOutsideSchedule,
    LegTargetNotPositive,
    LegExceedsTarget,
    EmptyRun,
    TwapTargetNotPositive,
    EmptyPassiveLimitRun,
    MakerTakerTargetNotPositive,
    AlgorithmVersionNotPositive,
    MakerTakerLegRoleMismatch,
    MissingExposureLedger,
    TwapRunShapeMismatch,
    TooManyTwapSlices,
    PassiveLimitRoleMismatch,
    ActionReferencesFutureDecision,
    QualityLegMismatch,
    ReadyChildQuantityNotPositive,
    DuplicateReadyChild,
    UnknownReadyChildLeg,
    InactiveReadyChildLeg,
    ChildExceedsLegTarget,
    MissingReadyTwapChild,
    ExposureNotCovered,
    MultipleRunsForIntent,
    BenchmarkOwnerMismatch,
    FilledBenchmarkLegWithoutOrder,
    MixedOrderSides,
}

impl std::fmt::Display for AlgorithmInvariant {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(match self {
            Self::RunIdRequired => "run ID is required",
            Self::LeaderAndHedgeMustDiffer => "leader and hedge legs must differ",
            Self::MaximumUnhedgedDurationNotPositive => {
                "maximum unhedged duration must be positive"
            },
            Self::DuplicateFallbackRoute => "fallback routes must be unique",
            Self::PassiveLimitIntervalNotPositive => "passive-limit interval must be positive",
            Self::InvalidTwapSchedule => "TWAP schedule is invalid",
            Self::TwapSliceOutsideSchedule => "TWAP slice falls outside its schedule",
            Self::LegTargetNotPositive => "leg target quantity must be positive",
            Self::LegExceedsTarget => "leg progress exceeds its target quantity",
            Self::EmptyRun => "algorithm run must contain at least one leg",
            Self::TwapTargetNotPositive => "TWAP target quantity must be positive",
            Self::EmptyPassiveLimitRun => "passive-limit run must contain a leg",
            Self::MakerTakerTargetNotPositive => "maker-taker target quantity must be positive",
            Self::AlgorithmVersionNotPositive => "algorithm version must be positive",
            Self::MakerTakerLegRoleMismatch => "maker-taker leg roles do not match the policy",
            Self::MissingExposureLedger => "maker-taker run is missing its exposure ledger",
            Self::TwapRunShapeMismatch => "TWAP run shape does not match its policy",
            Self::TooManyTwapSlices => "TWAP run contains more slices than its policy",
            Self::PassiveLimitRoleMismatch => "passive-limit leg role does not match its policy",
            Self::ActionReferencesFutureDecision => "action references a future decision",
            Self::QualityLegMismatch => "execution quality references an unknown leg",
            Self::ReadyChildQuantityNotPositive => "ready child quantity must be positive",
            Self::DuplicateReadyChild => "ready child identities must be unique",
            Self::UnknownReadyChildLeg => "ready child references an unknown leg",
            Self::InactiveReadyChildLeg => "ready child references an inactive leg",
            Self::ChildExceedsLegTarget => "child quantity exceeds its leg target",
            Self::MissingReadyTwapChild => "a due TWAP slice has no ready child",
            Self::ExposureNotCovered => "maker-taker exposure is not covered",
            Self::MultipleRunsForIntent => "intent has multiple algorithm runs",
            Self::BenchmarkOwnerMismatch => "benchmark ownership does not match the run",
            Self::FilledBenchmarkLegWithoutOrder => "filled benchmark leg has no order",
            Self::MixedOrderSides => "benchmark leg contains mixed order sides",
        })
    }
}

impl AlgorithmInvariant {
    pub const fn code(self) -> &'static str {
        match self {
            Self::RunIdRequired => "execution.algorithm.invariant.run_id_required",
            Self::LeaderAndHedgeMustDiffer => "execution.algorithm.invariant.leader_hedge_distinct",
            Self::MaximumUnhedgedDurationNotPositive => {
                "execution.algorithm.invariant.unhedged_duration_positive"
            },
            Self::DuplicateFallbackRoute => {
                "execution.algorithm.invariant.duplicate_fallback_route"
            },
            Self::PassiveLimitIntervalNotPositive => {
                "execution.algorithm.invariant.passive_interval_positive"
            },
            Self::InvalidTwapSchedule => "execution.algorithm.invariant.twap_schedule",
            Self::TwapSliceOutsideSchedule => "execution.algorithm.invariant.twap_slice_schedule",
            Self::LegTargetNotPositive => "execution.algorithm.invariant.leg_target_positive",
            Self::LegExceedsTarget => "execution.algorithm.invariant.leg_exceeds_target",
            Self::EmptyRun => "execution.algorithm.invariant.empty_run",
            Self::TwapTargetNotPositive => "execution.algorithm.invariant.twap_target_positive",
            Self::EmptyPassiveLimitRun => "execution.algorithm.invariant.empty_passive_run",
            Self::MakerTakerTargetNotPositive => {
                "execution.algorithm.invariant.maker_taker_target_positive"
            },
            Self::AlgorithmVersionNotPositive => "execution.algorithm.invariant.version_positive",
            Self::MakerTakerLegRoleMismatch => "execution.algorithm.invariant.maker_taker_role",
            Self::MissingExposureLedger => "execution.algorithm.invariant.missing_exposure_ledger",
            Self::TwapRunShapeMismatch => "execution.algorithm.invariant.twap_run_shape",
            Self::TooManyTwapSlices => "execution.algorithm.invariant.too_many_twap_slices",
            Self::PassiveLimitRoleMismatch => "execution.algorithm.invariant.passive_limit_role",
            Self::ActionReferencesFutureDecision => "execution.algorithm.invariant.future_decision",
            Self::QualityLegMismatch => "execution.algorithm.invariant.quality_leg",
            Self::ReadyChildQuantityNotPositive => {
                "execution.algorithm.invariant.ready_child_quantity_positive"
            },
            Self::DuplicateReadyChild => "execution.algorithm.invariant.duplicate_ready_child",
            Self::UnknownReadyChildLeg => "execution.algorithm.invariant.unknown_ready_child_leg",
            Self::InactiveReadyChildLeg => "execution.algorithm.invariant.inactive_ready_child_leg",
            Self::ChildExceedsLegTarget => "execution.algorithm.invariant.child_exceeds_leg_target",
            Self::MissingReadyTwapChild => "execution.algorithm.invariant.missing_ready_twap_child",
            Self::ExposureNotCovered => "execution.algorithm.invariant.exposure_not_covered",
            Self::MultipleRunsForIntent => "execution.algorithm.invariant.multiple_runs_for_intent",
            Self::BenchmarkOwnerMismatch => "execution.algorithm.invariant.benchmark_owner",
            Self::FilledBenchmarkLegWithoutOrder => {
                "execution.algorithm.invariant.filled_leg_without_order"
            },
            Self::MixedOrderSides => "execution.algorithm.invariant.mixed_order_sides",
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum AlgorithmError {
    #[error("algorithm invariant violated: {invariant}")]
    Invariant { invariant: AlgorithmInvariant },
    #[error("algorithm decision expected {expected}, but current sequence is {current}")]
    StaleDecision {
        expected: AlgorithmDecisionSequence,
        current: AlgorithmDecisionSequence,
    },
    #[error("algorithm business time cannot move backwards")]
    BusinessTimeRegression,
    #[error("algorithm arithmetic overflow during {operation}")]
    Overflow { operation: &'static str },
    #[error("algorithm arithmetic failed during {operation}: {source}")]
    Arithmetic {
        operation: &'static str,
        #[source]
        source: DomainTypeError,
    },
    #[error("algorithm decision requires a {expected} specification")]
    SpecMismatch { expected: &'static str },
    #[error("algorithm leg {leg_id} is missing")]
    MissingLeg { leg_id: String },
    #[error("algorithm run is missing for intent {intent_id}")]
    MissingRun { intent_id: String },
    #[error("algorithm run {run_id} references missing intent {intent_id}")]
    MissingOwnerIntent { run_id: String, intent_id: String },
    #[error("algorithm action {action_id} is unknown")]
    UnknownAction { action_id: String },
    #[error("algorithm action {action_id} is already resolved")]
    ResolvedAction { action_id: String },
    #[error("duplicate algorithm leg {leg_id}")]
    DuplicateLeg { leg_id: String },
    #[error("duplicate algorithm action {action_id}")]
    DuplicateAction { action_id: String },
    #[error(transparent)]
    Intent(#[from] IntentError),
}

impl AlgorithmError {
    pub const fn code(&self) -> &'static str {
        match self {
            Self::Invariant { invariant } => invariant.code(),
            Self::StaleDecision { .. } => "execution.algorithm.stale_decision",
            Self::BusinessTimeRegression => "execution.algorithm.business_time_regression",
            Self::Overflow { .. } => "execution.algorithm.overflow",
            Self::Arithmetic { .. } => "execution.algorithm.arithmetic",
            Self::SpecMismatch { .. } => "execution.algorithm.spec_mismatch",
            Self::MissingLeg { .. } => "execution.algorithm.missing_leg",
            Self::MissingRun { .. } => "execution.algorithm.missing_run",
            Self::MissingOwnerIntent { .. } => "execution.algorithm.missing_owner_intent",
            Self::UnknownAction { .. } => "execution.algorithm.unknown_action",
            Self::ResolvedAction { .. } => "execution.algorithm.resolved_action",
            Self::DuplicateLeg { .. } => "execution.algorithm.duplicate_leg",
            Self::DuplicateAction { .. } => "execution.algorithm.duplicate_action",
            Self::Intent(error) => error.code(),
        }
    }

    pub(crate) const fn invariant(invariant: AlgorithmInvariant) -> Self {
        Self::Invariant { invariant }
    }
}

impl From<DomainTypeError> for AlgorithmError {
    fn from(source: DomainTypeError) -> Self {
        Self::Arithmetic {
            operation: "algorithm state aggregation",
            source,
        }
    }
}
