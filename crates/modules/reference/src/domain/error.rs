//! Reference domain errors.

pub type ReferenceResult<T> = Result<T, ReferenceError>;

#[derive(Debug)]
pub enum ReferenceError {
    Configuration(String),
    Invalid(String),
    DuplicateId {
        record_kind: String,
        record_id: String,
    },
    CanonicalConflict {
        record_kind: &'static str,
        record_id: String,
        fields: Vec<&'static str>,
    },
    SyncInProgress {
        providers: Vec<String>,
    },
    Provider(String),
    Persistence(String),
    Publication(String),
}

impl std::fmt::Display for ReferenceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Configuration(value) => {
                write!(f, "invalid reference configuration: {value}")
            },
            Self::Invalid(value) => write!(f, "invalid reference data: {value}"),
            Self::DuplicateId {
                record_kind,
                record_id,
            } => write!(
                f,
                "invalid reference data: duplicate {record_kind} id: {record_id}"
            ),
            Self::CanonicalConflict {
                record_kind,
                record_id,
                fields,
            } => write!(
                f,
                "invalid reference data: canonical {record_kind} conflict for {record_id}: different {}",
                fields.join(", ")
            ),
            Self::SyncInProgress { providers } => write!(
                f,
                "reference synchronization in progress: providers without last-known-good facts: {}",
                providers.join(", ")
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
            Self::Configuration(_) => "reference.invalid_configuration",
            Self::Invalid(_) => "reference.invalid_data",
            Self::DuplicateId { .. } => "reference.duplicate_id",
            Self::CanonicalConflict { .. } => "reference.canonical_conflict",
            Self::SyncInProgress { .. } => "reference.sync_in_progress",
            Self::Provider(_) => "reference.provider_failed",
            Self::Persistence(_) => "reference.persistence_failed",
            Self::Publication(_) => "reference.publication_failed",
        }
    }

    pub fn retryable(&self) -> bool {
        matches!(
            self,
            Self::SyncInProgress { .. }
                | Self::Provider(_)
                | Self::Persistence(_)
                | Self::Publication(_)
        )
    }

    pub fn is_sync_in_progress(&self) -> bool {
        matches!(self, Self::SyncInProgress { .. })
    }

    pub fn record_identity(&self) -> Option<(&str, &str)> {
        match self {
            Self::DuplicateId {
                record_kind,
                record_id,
            } => Some((record_kind, record_id)),
            Self::CanonicalConflict {
                record_kind,
                record_id,
                ..
            } => Some((record_kind, record_id)),
            _ => None,
        }
    }

    pub fn conflict_fields(&self) -> Option<&[&'static str]> {
        match self {
            Self::CanonicalConflict { fields, .. } => Some(fields),
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

#[cfg(test)]
mod tests {
    use super::ReferenceError;

    #[test]
    fn configuration_errors_are_not_reported_as_provider_failures() {
        let error = ReferenceError::Configuration("unknown field `products`".into());

        assert_eq!(error.code(), "reference.invalid_configuration");
        assert_eq!(
            error.to_string(),
            "invalid reference configuration: unknown field `products`"
        );
        assert!(!error.retryable());
    }

    #[test]
    fn canonical_conflicts_preserve_identity_and_fields() {
        let error = ReferenceError::CanonicalConflict {
            record_kind: "instrument",
            record_id: "instrument:btc".into(),
            fields: vec!["symbol", "instrument_type"],
        };

        assert_eq!(error.code(), "reference.canonical_conflict");
        assert_eq!(
            error.record_identity(),
            Some(("instrument", "instrument:btc"))
        );
        assert_eq!(
            error.conflict_fields(),
            Some(["symbol", "instrument_type"].as_slice())
        );
        assert!(!error.retryable());
    }
}
