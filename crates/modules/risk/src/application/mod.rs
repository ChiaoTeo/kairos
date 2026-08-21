mod conflux;
pub(crate) mod contract;
mod service;

kairos_risk_contract::risk_control_rpc_conflux_actor! {
    pub trait RiskRpcActor;
    service RiskRpcService;
}

pub use service::{
    CloseCircuit, ConsumeReservation, ExpireReservations, FundingRequirement, LimitView,
    OpenCircuit, PublishPolicy, ReleaseReservation, ResizeReservation, RiskApplication,
    RiskClockMode, RiskCurrentView, RiskDecision, RiskError, RiskEvent, RiskSnapshot,
};
