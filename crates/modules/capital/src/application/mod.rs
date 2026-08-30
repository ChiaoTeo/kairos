mod app;
mod cli;
mod connected;
pub mod contract;
mod process;
mod transfer;

kairos_capital_contract::capital_control_rpc_conflux_actor! {
    pub trait CapitalRpcActor;
    service CapitalRpcService;
}

pub use app::{
    AuthorizeCapitalPlan, AuthorizeEarnSubscriptionPlan, BeginCapitalOperation,
    CancelFundingObjective, CapitalApplication, CapitalDemandReceipt, CapitalError, CapitalEvent,
    CapitalSnapshot, CapitalYieldCandidate, ConfirmManualCapitalTransfer, EvaluateCapitalGroup,
    ExpireCapitalDemands, ExpireCapitalPlans, ExpireFundingObjectives, FundingObjectiveReceipt,
    ManualCapitalTransferPreview, MarkCapitalDeliveryStarted, ObserveCapitalDemand,
    ObserveCapitalFacts, ObserveCapitalMemberAccount, ObserveCapitalSettlement,
    PreviewManualCapitalTransfer, PublishFundingObjective, RecordCapitalParticipantStatus,
    RecordCapitalRecoveryRequired, RecordCapitalSubmission, UpdateCapitalPolicy,
    UpdateCapitalRoute,
};
pub use cli::{
    CapitalCliRequest, CapitalCliRequestKind, CapitalPlanResult, CapitalPreviewResult,
    CapitalStandaloneOutput, CapitalValidationResult, CliCapitalApplication,
};
pub use connected::{ConnectedCapitalApplication, ConnectedCapitalOutput};
pub use process::{CapitalConfluxConfig, CapitalProcess, CapitalProcessError};
pub(crate) use transfer::standalone_transfer_history;
pub use transfer::{
    CliCapitalTransferApplication, StandaloneCapitalOperationResult, StandaloneCapitalPlanResult,
    StandaloneCapitalSegmentBinding, StandaloneCapitalTransferBinding,
    StandaloneCapitalTransferHistoryItem, StandaloneCapitalTransferHistoryResult,
    StandaloneCapitalTransferPreviewRequest, StandaloneCapitalTransferPreviewResult,
    StandaloneCapitalTransferResult,
};
