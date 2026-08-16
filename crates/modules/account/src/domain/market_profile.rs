use serde::{Deserialize, Serialize};

use kairos_primitives::{Currency, UnixNanos};

use super::{AccountId, AccountModel, MarginMode, MarketId, PositionMode, Rate, SegmentKey};

/// Account-specific market terms observed from the provider.
/// Generic instrument rules remain owned by Market/Reference.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountMarketProfile {
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub market_id: MarketId,
    pub account_model: Option<AccountModel>,
    pub margin_mode: Option<MarginMode>,
    pub position_mode: Option<PositionMode>,
    pub maker_fee: Option<Rate>,
    pub taker_fee: Option<Rate>,
    pub fee_currency: Option<Currency>,
    pub fee_discount: Option<Rate>,
    pub fee_tier: Option<String>,
    pub source: String,
    pub observed_at_unix_nanos: UnixNanos,
}
