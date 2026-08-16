use kairos_primitives::{AccountId, DurationNanos, MarketId, Symbol, UnixNanos};

use crate::domain::SegmentKey;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountQuery {
    pub account_id: AccountId,
    pub segments: Vec<SegmentKey>,
    pub max_age_seconds: Option<DurationNanos>,
    pub now_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct AccountDataQuery {
    pub account_id: Option<AccountId>,
    pub segments: Vec<SegmentKey>,
    pub symbol: Option<Symbol>,
    pub include_zero: bool,
    pub limit: Option<usize>,
    pub page: Option<usize>,
    pub page_size: Option<usize>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct AccountMarketProfileRequest {
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub market_id: MarketId,
    pub source_symbol: Symbol,
    pub market_data_access_id: Option<String>,
}
