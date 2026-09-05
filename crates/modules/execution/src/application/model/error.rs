//! Public Execution application errors.

#[derive(Debug, thiserror::Error)]
pub enum ExecutionError {
    #[error("execution admission failed: {0}")]
    Admission(#[from] crate::domain::AdmissionError),
    #[error("invalid execution order: {0}")]
    Order(#[from] crate::domain::OrderError),
    #[error("invalid execution intent: {0}")]
    Intent(#[from] crate::domain::IntentError),
    #[error("execution algorithm failed: {0}")]
    Algorithm(#[from] crate::domain::AlgorithmError),
    #[error("execution runtime failed: {0}")]
    Runtime(#[from] crate::domain::ExecutionRuntimeError),
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
