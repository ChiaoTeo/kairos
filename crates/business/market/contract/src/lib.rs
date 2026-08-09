//! Public cross-process contract for the Market module.

pub mod encoding;
pub mod event;
pub mod model;
pub mod query;
pub mod reference;
pub mod snapshot;
pub mod transport;

pub use event::EventEnvelope;
pub use model::{MarketSnapshot, Quote};
pub use query::{CommandEnvelope, QueryEnvelope};
pub use reference::{decode_reference_changed, ReferenceChangeNotice};
pub use snapshot::{
    read_latest_quotes, read_latest_quotes_watermark, read_latest_quotes_with_watermark,
    MarketSnapshotRead, SnapshotEnvelope,
};

#[derive(Debug)]
pub enum ContractError {
    Invalid(String),
    Transport(String),
}

impl std::fmt::Display for ContractError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(value) => write!(f, "invalid market contract data: {value}"),
            Self::Transport(value) => write!(f, "market contract transport failed: {value}"),
        }
    }
}

impl std::error::Error for ContractError {}

pub type ContractResult<T> = Result<T, ContractError>;
