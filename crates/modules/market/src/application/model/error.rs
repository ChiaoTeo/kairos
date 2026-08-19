#[derive(Debug, PartialEq, Eq)]
pub enum MarketError {
    Invalid(String),
    InvalidSubscription(String),
    NotFound(String),
    SourceUnavailable(String),
    Authentication(String),
    Unsupported(String),
    QueueOverflow(String),
    SequenceGap(String),
    Recovery(String),
    StaleEpoch(String),
    ShutdownIncomplete(String),
}

impl std::fmt::Display for MarketError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(value) => write!(formatter, "invalid market request: {value}"),
            Self::InvalidSubscription(value) => {
                write!(formatter, "invalid market subscription: {value}")
            },
            Self::NotFound(value) => write!(formatter, "market not found: {value}"),
            Self::SourceUnavailable(value) => {
                write!(formatter, "market source unavailable: {value}")
            },
            Self::Authentication(value) => {
                write!(formatter, "market source authentication failed: {value}")
            },
            Self::Unsupported(value) => {
                write!(formatter, "market capability is unsupported: {value}")
            },
            Self::QueueOverflow(value) => write!(formatter, "market queue overflow: {value}"),
            Self::SequenceGap(value) => write!(formatter, "market sequence gap: {value}"),
            Self::Recovery(value) => write!(formatter, "market recovery failed: {value}"),
            Self::StaleEpoch(value) => write!(formatter, "stale market source epoch: {value}"),
            Self::ShutdownIncomplete(value) => {
                write!(formatter, "market shutdown incomplete: {value}")
            },
        }
    }
}

impl std::error::Error for MarketError {}
