//! Risk business boundary.
//!
//! [`RiskApplication`] is the public use-case facade. [`RiskActor`] is the
//! single owner of mutable budgets and reservations; process control and
//! publication are assembled outside the business state owner.

pub mod application;
pub mod composition;
mod domain;
mod services;

pub use application::{
    CliRiskApplication, CloseCircuit, ConnectedRiskApplication, ConnectedRiskOutput,
    ConsumeReservation, ExpireReservations, FundingRequirement, LimitView, OpenCircuit,
    PublishPolicy, ReleaseReservation, ResizeReservation, RiskApplication, RiskCliRequestKind,
    RiskClockMode, RiskCurrentView, RiskDecision, RiskError, RiskEvent, RiskSnapshot,
    RiskStandaloneOutput,
};
pub use composition::RiskHost;
pub use domain::{
    Allocation, Amount, AuthorizeRequest, CircuitScope, CircuitState, DependencyWatermarks,
    EnforcementMode, Metric, PolicyScope, ReasonCode, RequestedUsage, Reservation,
    ReservationStatus, RiskContext, RiskDomainError, RiskPolicy, TradeRiskProposal,
};
