use kairos_domain_types::{AccountId, Currency, Price, UnixNanos};

use crate::domain::{InstrumentId, SegmentKey};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RefreshAccount {
    pub account_id: AccountId,
    pub segments: Vec<SegmentKey>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReconcileAccount {
    pub account_id: AccountId,
    pub segments: Vec<SegmentKey>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Deserialize, serde::Serialize)]
pub struct MarkToMarket {
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub quote_asset: Currency,
    pub mark_price: Price,
    pub observed_at_unix_nanos: UnixNanos,
}
