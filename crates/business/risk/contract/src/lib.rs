//! Public cross-process contract for the Risk module.

pub mod client;
pub mod encoding;
pub mod event;
pub mod model;
pub mod query;
pub mod snapshot;
pub mod transport;

pub use event::EventEnvelope;
pub use model::{RiskEvent, RiskSnapshot};
pub use query::{CommandEnvelope, QueryEnvelope};
pub use snapshot::SnapshotEnvelope;

#[derive(Debug)]
pub enum ContractError {
    Invalid(String),
    Transport(String),
}

impl std::fmt::Display for ContractError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(value) => write!(f, "invalid risk contract data: {value}"),
            Self::Transport(value) => write!(f, "risk contract transport failed: {value}"),
        }
    }
}

impl std::error::Error for ContractError {}

pub type ContractResult<T> = Result<T, ContractError>;
