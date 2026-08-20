use kairos_primitives::decimal::{Price, Quantity};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PriceLevel {
    pub price: Price,
    pub quantity: Quantity,
}
