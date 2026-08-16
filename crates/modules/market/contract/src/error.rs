#[derive(Debug)]
pub enum ContractError {
    Invalid(String),
    Transport(String),
    Unsupported(String),
}

impl std::fmt::Display for ContractError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(value) => write!(f, "invalid Market contract data: {value}"),
            Self::Transport(value) => write!(f, "Market contract transport failed: {value}"),
            Self::Unsupported(value) => write!(f, "unsupported Market contract operation: {value}"),
        }
    }
}

impl std::error::Error for ContractError {}

pub type ContractResult<T> = Result<T, ContractError>;
