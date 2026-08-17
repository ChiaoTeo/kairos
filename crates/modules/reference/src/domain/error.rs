//! Reference domain errors.

pub type ReferenceResult<T> = Result<T, ReferenceError>;

#[derive(Debug)]
pub enum ReferenceError {
    Invalid(String),
    DuplicateId {
        record_kind: String,
        record_id: String,
    },
    Provider(String),
    Persistence(String),
    Publication(String),
}

impl std::fmt::Display for ReferenceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(value) => write!(f, "invalid reference data: {value}"),
            Self::DuplicateId {
                record_kind,
                record_id,
            } => write!(
                f,
                "invalid reference data: duplicate {record_kind} id: {record_id}"
            ),
            Self::Provider(value) => write!(f, "reference provider failed: {value}"),
            Self::Persistence(value) => write!(f, "reference persistence failed: {value}"),
            Self::Publication(value) => write!(f, "reference publication failed: {value}"),
        }
    }
}

impl ReferenceError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::Invalid(_) => "reference.invalid_data",
            Self::DuplicateId { .. } => "reference.duplicate_id",
            Self::Provider(_) => "reference.provider_failed",
            Self::Persistence(_) => "reference.persistence_failed",
            Self::Publication(_) => "reference.publication_failed",
        }
    }

    pub fn retryable(&self) -> bool {
        matches!(
            self,
            Self::Provider(_) | Self::Persistence(_) | Self::Publication(_)
        )
    }

    pub fn record_identity(&self) -> Option<(&str, &str)> {
        match self {
            Self::DuplicateId {
                record_kind,
                record_id,
            } => Some((record_kind, record_id)),
            _ => None,
        }
    }
}

impl std::error::Error for ReferenceError {}

impl From<kairos_primitives::DomainTypeError> for ReferenceError {
    fn from(error: kairos_primitives::DomainTypeError) -> Self {
        Self::Invalid(error.to_string())
    }
}
