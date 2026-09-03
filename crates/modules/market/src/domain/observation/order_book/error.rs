use kairos_primitives::time::Sequence;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum OrderBookError {
    InvalidSemantic {
        identity: &'static str,
        source: kairos_primitives::DomainTypeError,
    },
    IdentityRequired,
    LevelQuantityRequired,
    SnapshotRequired,
    DeltaIdentityMismatch,
    InvalidSequenceRange,
    SequenceGap {
        expected: Sequence,
        received: Sequence,
    },
    StaleDelta {
        expected: Sequence,
        received: Sequence,
    },
}

impl OrderBookError {
    pub const fn code(&self) -> &'static str {
        match self {
            Self::InvalidSemantic { .. } => "market.order_book.invalid_semantic",
            Self::IdentityRequired => "market.order_book.identity_required",
            Self::LevelQuantityRequired => "market.order_book.level_quantity_required",
            Self::SnapshotRequired => "market.order_book.snapshot_required",
            Self::DeltaIdentityMismatch => "market.order_book.delta_identity_mismatch",
            Self::InvalidSequenceRange => "market.order_book.invalid_sequence_range",
            Self::SequenceGap { .. } => "market.order_book.sequence_gap",
            Self::StaleDelta { .. } => "market.order_book.stale_delta",
        }
    }
}

impl std::fmt::Display for OrderBookError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidSemantic { identity, source } => {
                write!(formatter, "invalid {identity}: {source}")
            },
            Self::IdentityRequired => formatter.write_str("order book identity is required"),
            Self::LevelQuantityRequired => {
                formatter.write_str("order book level requires price and quantity")
            },
            Self::SnapshotRequired => {
                formatter.write_str("order book is not synchronized; snapshot is required")
            },
            Self::DeltaIdentityMismatch => {
                formatter.write_str("order book delta identity does not match snapshot")
            },
            Self::InvalidSequenceRange => {
                formatter.write_str("order book delta has invalid sequence range")
            },
            Self::SequenceGap { expected, received } => write!(
                formatter,
                "order book sequence gap: expected {expected}, got {received}"
            ),
            Self::StaleDelta { expected, received } => write!(
                formatter,
                "stale order book delta: expected through {expected}, got {received}"
            ),
        }
    }
}

impl std::error::Error for OrderBookError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::InvalidSemantic { source, .. } => Some(source),
            _ => None,
        }
    }
}
