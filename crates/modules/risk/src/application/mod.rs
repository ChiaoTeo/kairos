mod app;
mod cli;
mod conflux;
mod connected;
pub(crate) mod contract;

kairos_risk_contract::risk_control_rpc_conflux_actor! {
    pub trait RiskRpcActor;
    service RiskRpcService;
}

pub use app::{
    CloseCircuit, ConsumeReservation, ExpireReservations, FundingRequirement, LimitView,
    OpenCircuit, PublishPolicy, ReleaseReservation, ResizeReservation, RiskApplication,
    RiskClockMode, RiskCurrentView, RiskDecision, RiskError, RiskEvent, RiskSnapshot,
};
pub use cli::{CliRiskApplication, RiskCliRequestKind};
pub use connected::ConnectedRiskApplication;
