//! Public Execution application errors.

#[derive(Debug, thiserror::Error)]
pub enum ExecutionError {
    #[error("invalid execution request: {0}")]
    Invalid(String),
    #[error("execution gateway failed: {0}")]
    Gateway(String),
    #[error("provider rejected the execution command: {0}")]
    ProviderRejected(String),
    #[error("execution command outcome is indeterminate: {0}")]
    Indeterminate(String),
    #[error("execution persistence failed: {0}")]
    Persistence(String),
}
