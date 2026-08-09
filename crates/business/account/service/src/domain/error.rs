#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum AccountDomainError {
    #[error("{field} is required")]
    Required { field: &'static str },
    #[error("invalid {field}: {reason}")]
    Invalid {
        field: &'static str,
        reason: &'static str,
    },
    #[error("account segment mismatch: expected {expected}, observed {observed}")]
    SegmentMismatch { expected: String, observed: String },
    #[error("account identity mismatch")]
    AccountMismatch,
    #[error("decimal scale {from} cannot be represented exactly at scale {to}")]
    InexactRescale { from: u8, to: u8 },
    #[error("decimal operation overflowed")]
    DecimalOverflow,
    #[error("event has already been applied")]
    DuplicateEvent,
    #[error("event observation is older than the current account watermark")]
    StaleObservation,
    #[error("invalid account transition: {0}")]
    InvalidTransition(String),
}
