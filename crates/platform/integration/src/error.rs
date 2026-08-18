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
    #[error("integration authentication failed: {0}")]
    Authentication(String),
    #[error("integration authorization failed: {0}")]
    Authorization(String),
    #[error("integration entitlement is missing: {0}")]
    Entitlement(String),
    #[error("integration request is rate limited: {0}")]
    RateLimited(String),
    #[error("integration transport failed: {0}")]
    Transport(String),
    #[error("participant payload is invalid: {0}")]
    InvalidPayload(String),
    #[error("integration sequence gap: {0}")]
    SequenceGap(String),
    #[error("integration resynchronization is required: {0}")]
    ResyncRequired(String),
    #[error("integration backpressure limit was reached: {0}")]
    Backpressure(String),
    #[error("integration capability is unavailable: {0}")]
    Unavailable(String),
}

pub type CommandResult<T> = Result<crate::domain::CommandOutcome<T>, IntegrationError>;
