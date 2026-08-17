use serde::{Deserialize, Serialize};

use crate::{DomainTypeError, Sequence, UnixNanos};

/// Shared business-time context for cross-module commands and events.
///
/// `event_time` is the provider/replay time which determines business
/// behavior.  Processing and wall-clock timestamps belong to observability or
/// transport layers and must not replace this value in deterministic flows.
#[derive(
    Clone, Copy, Debug, Default, Eq, Ord, PartialEq, PartialOrd, Hash, Serialize, Deserialize,
)]
pub struct EventContext {
    pub event_time: UnixNanos,
    pub sequence: Sequence,
}

impl EventContext {
    pub const fn new(event_time: UnixNanos, sequence: Sequence) -> Self {
        Self {
            event_time,
            sequence,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OrderSide {
    Buy,
    Sell,
}

impl std::str::FromStr for OrderSide {
    type Err = DomainTypeError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value.trim().to_ascii_lowercase().as_str() {
            "buy" | "bid" => Ok(Self::Buy),
            "sell" | "ask" => Ok(Self::Sell),
            _ => Err(DomainTypeError::Invalid {
                type_name: "OrderSide",
                reason: "expected buy or sell",
            }),
        }
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
