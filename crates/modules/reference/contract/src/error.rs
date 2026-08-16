//! Errors owned by the Reference cross-process contract.

pub type ContractResult<T> = Result<T, ContractError>;

#[derive(Debug)]
pub enum ContractError {
    Invalid(String),
    Transport(String),
}

impl std::fmt::Display for ContractError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(message) => write!(f, "invalid reference contract data: {message}"),
            Self::Transport(message) => write!(f, "reference contract transport failed: {message}"),
        }
    }
}

impl std::error::Error for ContractError {}
