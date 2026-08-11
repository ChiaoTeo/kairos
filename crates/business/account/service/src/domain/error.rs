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

impl From<kairos_domain_types::DomainTypeError> for AccountDomainError {
    fn from(error: kairos_domain_types::DomainTypeError) -> Self {
        match error {
            kairos_domain_types::DomainTypeError::Empty { .. } => Self::Required {
                field: "account_id",
            },
            kairos_domain_types::DomainTypeError::Whitespace { .. } => Self::Invalid {
                field: "account_id",
                reason: "leading or trailing whitespace is not allowed",
            },
            kairos_domain_types::DomainTypeError::Invalid { reason, .. } => Self::Invalid {
                field: "account_id",
                reason,
            },
            kairos_domain_types::DomainTypeError::NonPositive { .. } => Self::Invalid {
                field: "account_id",
                reason: "value must be positive",
            },
        }
    }
}
