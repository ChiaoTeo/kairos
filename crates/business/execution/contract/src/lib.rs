//! Public cross-process contract for the Execution module.

pub mod encoding;
pub mod event;
pub mod model;
pub mod plan;
pub mod query;
pub mod snapshot;
pub mod strategy_event;
pub mod transport;

pub use event::{EventEnvelope, ExecutionEventEnvelope};
pub use model::{DependencyWatermarks, ExecutionCurrentView, SnapshotWatermark};
pub use plan::{
    CompletionPolicy, ExecutionIntentLeg, ExecutionLeg, ExecutionOrderOptions, ExecutionPlan,
    FailurePolicy, HedgePolicy, IntentLifecycle, IntentType, LegLifecycle, MakerExecutionPolicy,
    PairArbitrageIntent, PairArbitrageLeg, PortfolioRebalanceTarget, SplitOrderPolicy,
};
pub use query::{
    CancelIntentCommand, CommandEnvelope, ExecutionIntentCommand, ExpireIntentCommand,
    HedgeRequirementQuery, IntentCommandEnvelope, IntentEventQuery, IntentQuery, QueryEnvelope,
    RefreshQuoteCommand, SubmitIntentPayload,
};
pub use snapshot::SnapshotEnvelope;

#[derive(Debug)]
pub enum ContractError {
    Invalid(String),
    Transport(String),
}

impl std::fmt::Display for ContractError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(value) => write!(f, "invalid execution contract data: {value}"),
            Self::Transport(value) => write!(f, "execution contract transport failed: {value}"),
        }
    }
}

impl std::error::Error for ContractError {}

pub type ContractResult<T> = Result<T, ContractError>;
