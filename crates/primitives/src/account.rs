use serde::{Deserialize, Serialize};

use crate::DomainTypeError;
use crate::text::text_type;

text_type!(AccountId);
text_type!(BrokerId);
text_type!(SegmentKey);

/// Canonical position identity within one account segment. `Net` is used by
/// one-way accounts; hedge-mode accounts keep independent long and short rows.
#[derive(
    Clone, Copy, Debug, Default, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize,
)]
#[serde(rename_all = "snake_case")]
pub enum PositionSide {
    #[default]
    Net,
    Long,
    Short,
}

impl PositionSide {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Net => "net",
            Self::Long => "long",
            Self::Short => "short",
        }
    }
}

impl std::str::FromStr for PositionSide {
    type Err = DomainTypeError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value.trim().to_ascii_lowercase().as_str() {
            "net" | "both" => Ok(Self::Net),
            "long" => Ok(Self::Long),
            "short" => Ok(Self::Short),
            _ => Err(DomainTypeError::Invalid {
                type_name: "PositionSide",
                reason: "expected net, long, or short",
            }),
        }
    }
}
