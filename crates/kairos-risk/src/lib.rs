//! Risk business boundary.
//!
//! [`RiskApplication`] is the public use-case facade. [`RiskActor`] is the
//! single owner of mutable budgets and reservations; process control and
//! publication are injected through application-owned protocols.

pub mod application;
pub mod composition;
pub mod domain;
mod services;

pub use application::{
    AssessRisk, ConfigureBudgets, ConsumeReservation, ReleaseReservation, ReserveRisk,
    RiskApplication, RiskAssessment, RiskError, RiskEvent, RiskSnapshot,
};
pub use domain::{Amount, Budget, BudgetRef, Metric, Reservation, ReservationStatus, Usage};
pub use services::process::RiskProcess;
