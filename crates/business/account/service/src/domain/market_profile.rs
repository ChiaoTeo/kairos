use serde::{Deserialize, Serialize};

use super::{AccountId, AccountModel, Decimal, MarginMode, PositionMode, SegmentKey};

/// Account-specific market terms observed from the provider.
/// Generic instrument rules remain owned by Market/Reference.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountMarketProfile {
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub market_id: String,
    pub account_model: Option<AccountModel>,
    pub margin_mode: Option<MarginMode>,
    pub position_mode: Option<PositionMode>,
    pub maker_fee: Option<Decimal>,
    pub taker_fee: Option<Decimal>,
    pub fee_currency: Option<String>,
    pub fee_discount: Option<Decimal>,
    pub fee_tier: Option<String>,
    pub source: String,
    pub observed_at_unix_nanos: u64,
}
