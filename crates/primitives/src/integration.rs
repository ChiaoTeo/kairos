use serde::{Deserialize, Serialize};

use crate::DomainTypeError;
use crate::text::text_type;

text_type!(ProviderSymbol);
text_type!(ParticipantSymbol);
text_type!(ParticipantId);
text_type!(ProviderId);
text_type!(ProviderProductCode);
text_type!(RemoteOrderId);

impl Default for ProviderId {
    fn default() -> Self {
        Self::new("provider:unknown").expect("canonical default provider is valid")
    }
}

impl Default for ProviderProductCode {
    fn default() -> Self {
        Self::new("unknown").expect("canonical default provider product is valid")
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OrderStatus {
    Pending,
    Acknowledged,
    Accepted,
    PartiallyFilled,
    Filled,
    Canceled,
    Rejected,
    Expired,
    Unknown,
}

impl std::str::FromStr for OrderStatus {
    type Err = DomainTypeError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value.trim().to_ascii_lowercase().as_str() {
            "pending" => Ok(Self::Pending),
            "acknowledged" | "new" | "open" => Ok(Self::Acknowledged),
            "accepted" => Ok(Self::Accepted),
            "partially_filled" | "partial" => Ok(Self::PartiallyFilled),
            "filled" => Ok(Self::Filled),
            "canceled" | "cancelled" => Ok(Self::Canceled),
            "rejected" => Ok(Self::Rejected),
            "expired" => Ok(Self::Expired),
            "unknown" => Ok(Self::Unknown),
            _ => Err(DomainTypeError::Invalid {
                type_name: "OrderStatus",
                reason: "unrecognized order status",
            }),
        }
    }
}
