#[derive(Debug)]
pub enum ContractError {
    Invalid(String),
    Transport(String),
    Unsupported(String),
}
impl std::fmt::Display for ContractError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(v) => write!(f, "invalid Execution contract data: {v}"),
            Self::Transport(v) => write!(f, "Execution contract transport failed: {v}"),
            Self::Unsupported(v) => write!(f, "unsupported Execution contract operation: {v}"),
        }
    }
}
impl std::error::Error for ContractError {}
pub type ContractResult<T> = Result<T, ContractError>;
