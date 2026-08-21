use serde::{Deserialize, Serialize};

use crate::DomainTypeError;
use crate::text::text_type;

text_type!(ExecutionRouteId);
text_type!(OrderId);
text_type!(ClientOrderId);
text_type!(IntentId);
text_type!(PlanId);
text_type!(LegId);
text_type!(FillId);
text_type!(OrderOptionCode);
text_type!(OrderEntrySymbol);

impl Default for ExecutionRouteId {
    fn default() -> Self {
        Self::new("route:unresolved").expect("legacy unresolved route identity is valid")
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OrderSide {
    Buy,
    Sell,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OrderType {
    #[serde(alias = "Market")]
    Market,
    #[serde(alias = "Limit")]
    Limit,
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
