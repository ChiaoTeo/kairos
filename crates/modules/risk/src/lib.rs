//! Risk business boundary.
//!
//! [`RiskApplication`] is the public use-case facade. [`RiskActor`] is the
//! single owner of mutable budgets and reservations; process control and
//! publication are assembled outside the business state owner.

pub mod application;
pub mod composition;
pub mod domain;
mod services;

pub use application::{
    CloseCircuit, ConsumeReservation, ExpireReservations, LimitView, OpenCircuit, PublishPolicy,
    ReleaseReservation, ResizeReservation, RiskApplication, RiskClockMode, RiskCurrentView,
    RiskDecision, RiskError, RiskEvent, RiskHost, RiskSnapshot,
};
pub use domain::{
    Allocation, Amount, AuthorizeRequest, CircuitScope, CircuitState, DependencyWatermarks,
    EnforcementMode, Metric, PolicyScope, ReasonCode, Reservation, ReservationStatus, RiskContext,
    RiskPolicy,
};
