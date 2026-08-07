//! Account-owned query types for venue-derived market capabilities.

use crate::domain::AccountModel;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountMarketProfileRequest {
    pub account_id: String,
    pub segment_key: String,
    pub market_id: String,
    pub source_symbol: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountMarketProfile {
    pub account_id: String,
    pub segment_key: String,
    pub market_id: String,
    pub account_model: Option<AccountModel>,
    pub margin_mode: Option<String>,
    pub position_mode: Option<String>,
    pub maker_fee: Option<crate::domain::DecimalValue>,
    pub taker_fee: Option<crate::domain::DecimalValue>,
    pub fee_currency: Option<String>,
    pub fee_discount: Option<crate::domain::DecimalValue>,
    pub fee_tier: Option<String>,
    pub source: String,
    pub observed_at_unix_nanos: u64,
}
