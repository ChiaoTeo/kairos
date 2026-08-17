//! Concrete Risk reservation adapter and its bounded worker projection.

mod adapter;
mod simulated;
mod worker;

pub use adapter::SocketExecutionRiskReservations;
pub use simulated::{SimulatedRiskBehavior, SimulatedRiskReconciliation};
pub use worker::QueuedExecutionRiskReservations;
