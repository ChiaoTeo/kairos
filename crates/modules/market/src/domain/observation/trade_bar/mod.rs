use serde::{Deserialize, Serialize};

use super::bar::Bar;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct TradeBar {
    pub bar: Bar,
}
