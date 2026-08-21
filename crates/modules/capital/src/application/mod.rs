mod conflux;
pub mod contract;
mod process;
mod service;

kairos_capital_contract::capital_control_rpc_conflux_actor! {
    pub trait CapitalRpcActor;
    service CapitalRpcService;
}

pub use process::{CapitalConfluxConfig, CapitalProcess, CapitalProcessError};
pub use service::{
    AuthorizeCapitalPlan, AuthorizeEarnSubscriptionPlan, BeginCapitalOperation,
    CancelFundingObjective, CapitalApplication, CapitalDemandReceipt, CapitalError, CapitalEvent,
    CapitalSnapshot, CapitalYieldCandidate, EvaluateCapitalGroup, ExpireCapitalDemands,
    ExpireCapitalPlans, ExpireFundingObjectives, FundingObjectiveReceipt,
    MarkCapitalDeliveryStarted, ObserveCapitalDemand, ObserveCapitalFacts,
    ObserveCapitalMemberAccount, ObserveCapitalSettlement, PublishFundingObjective,
    RecordCapitalParticipantStatus, RecordCapitalRecoveryRequired, RecordCapitalSubmission,
    UpdateCapitalPolicy, UpdateCapitalRoute,
};
