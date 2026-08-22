mod app;
mod cli;
mod conflux;
mod connected;
pub mod contract;
mod process;

kairos_capital_contract::capital_control_rpc_conflux_actor! {
    pub trait CapitalRpcActor;
    service CapitalRpcService;
}

pub use app::{
    AuthorizeCapitalPlan, AuthorizeEarnSubscriptionPlan, BeginCapitalOperation,
    CancelFundingObjective, CapitalApplication, CapitalDemandReceipt, CapitalError, CapitalEvent,
    CapitalSnapshot, CapitalYieldCandidate, EvaluateCapitalGroup, ExpireCapitalDemands,
    ExpireCapitalPlans, ExpireFundingObjectives, FundingObjectiveReceipt,
    MarkCapitalDeliveryStarted, ObserveCapitalDemand, ObserveCapitalFacts,
    ObserveCapitalMemberAccount, ObserveCapitalSettlement, PublishFundingObjective,
    RecordCapitalParticipantStatus, RecordCapitalRecoveryRequired, RecordCapitalSubmission,
    UpdateCapitalPolicy, UpdateCapitalRoute,
};
pub use cli::{CapitalCliRequestKind, CliCapitalApplication};
pub use connected::ConnectedCapitalApplication;
pub use process::{CapitalConfluxConfig, CapitalProcess, CapitalProcessError};
