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
    #[error("event has already been applied")]
    DuplicateEvent,
    #[error("event observation is older than the current account watermark")]
    StaleObservation,
    #[error("invalid account transition: {0}")]
    InvalidTransition(String),
}

impl From<kairos_primitives::DomainTypeError> for AccountDomainError {
    fn from(error: kairos_primitives::DomainTypeError) -> Self {
        match error {
            kairos_primitives::DomainTypeError::Empty { .. } => Self::Required {
                field: "account_id",
            },
            kairos_primitives::DomainTypeError::Whitespace { .. } => Self::Invalid {
                field: "account_id",
                reason: "leading or trailing whitespace is not allowed",
            },
            kairos_primitives::DomainTypeError::Invalid { reason, .. } => Self::Invalid {
                field: "account_id",
                reason,
            },
            kairos_primitives::DomainTypeError::NonPositive { .. } => Self::Invalid {
                field: "account_id",
                reason: "value must be positive",
            },
        }
    }
}
