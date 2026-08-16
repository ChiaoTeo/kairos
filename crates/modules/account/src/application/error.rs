#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum AccountError {
    #[error("invalid account request: {0}")]
    Invalid(String),
    #[error("account source failed: {0}")]
    Source(String),
    #[error("account persistence failed: {0}")]
    Persistence(String),
    #[error("account publication failed: {0}")]
    Publication(String),
}
