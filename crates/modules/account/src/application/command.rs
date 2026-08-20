use kairos_primitives::account::AccountId;
use kairos_primitives::decimal::Price;
use kairos_primitives::reference::Currency;
use kairos_primitives::time::UnixNanos;

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

impl From<kairos_account_contract::MarkToMarketRequest> for MarkToMarket {
    fn from(value: kairos_account_contract::MarkToMarketRequest) -> Self {
        Self {
            segment_key: value.segment_key,
            instrument_id: value.instrument_id,
            quote_asset: value.quote_asset,
            mark_price: value.mark_price,
            observed_at_unix_nanos: value.observed_at_unix_nanos,
        }
    }
}
