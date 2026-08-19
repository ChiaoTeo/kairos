use kairos_primitives::{AccountId, Currency, Price, UnixNanos};

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

impl TryFrom<kairos_account_contract::MarkToMarketRequest> for MarkToMarket {
    type Error = String;

    fn try_from(value: kairos_account_contract::MarkToMarketRequest) -> Result<Self, Self::Error> {
        Ok(Self {
            segment_key: SegmentKey::new(value.segment_key).map_err(|error| error.to_string())?,
            instrument_id: InstrumentId::new(value.instrument_id)
                .map_err(|error| error.to_string())?,
            quote_asset: Currency::new(value.quote_asset).map_err(|error| error.to_string())?,
            mark_price: Price::try_from(value.mark_price).map_err(|error| error.to_string())?,
            observed_at_unix_nanos: value.observed_at_unix_nanos.into(),
        })
    }
}
