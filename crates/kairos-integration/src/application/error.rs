use thiserror::Error;

/// Errors crossing the integration application boundary.
#[derive(Debug, Error)]
pub enum IntegrationError {
    #[error("invalid integration request: {0}")]
    InvalidRequest(String),
    #[error("integration connection is not ready")]
    NotReady,
    #[error("integration connection does not support this operation")]
    UnsupportedOperation,
    #[error("integration transport failed: {0}")]
    Transport(String),
    #[error("provider payload is invalid: {0}")]
    InvalidPayload(String),
}
