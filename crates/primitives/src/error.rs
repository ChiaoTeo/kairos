use std::fmt;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum DomainTypeError {
    Empty {
        type_name: &'static str,
    },
    Whitespace {
        type_name: &'static str,
    },
    Invalid {
        type_name: &'static str,
        reason: &'static str,
    },
    NonPositive {
        type_name: &'static str,
    },
}

impl fmt::Display for DomainTypeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Empty { type_name } => write!(f, "{type_name} cannot be empty"),
            Self::Whitespace { type_name } => {
                write!(
                    f,
                    "{type_name} cannot contain leading or trailing whitespace"
                )
            },
            Self::Invalid { type_name, reason } => write!(f, "invalid {type_name}: {reason}"),
            Self::NonPositive { type_name } => write!(f, "{type_name} must be positive"),
        }
    }
}

impl std::error::Error for DomainTypeError {}

impl From<DomainTypeError> for String {
    fn from(error: DomainTypeError) -> Self {
        error.to_string()
    }
}
