//! Reference domain errors.

pub type ReferenceResult<T> = Result<T, ReferenceError>;

#[derive(Debug)]
pub enum ReferenceError {
    Invalid(String),
    Provider(String),
    Persistence(String),
    Publication(String),
}

impl std::fmt::Display for ReferenceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(value) => write!(f, "invalid reference data: {value}"),
            Self::Provider(value) => write!(f, "reference provider failed: {value}"),
            Self::Persistence(value) => write!(f, "reference persistence failed: {value}"),
            Self::Publication(value) => write!(f, "reference publication failed: {value}"),
        }
    }
}

impl std::error::Error for ReferenceError {}
