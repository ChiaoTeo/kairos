use kairos_protocol::generated::kairos::market::v_2 as fb;

use crate::{ContractError, ContractResult};

pub type FreshnessView<'a> = fb::MarketFreshnessLatestView<'a>;

pub fn decode(bytes: &[u8]) -> ContractResult<FreshnessView<'_>> {
    if !fb::market_freshness_latest_view_buffer_has_identifier(bytes) {
        return Err(ContractError::Invalid(
            "expected MarketFreshnessLatestView".into(),
        ));
    }
    fb::root_as_market_freshness_latest_view(bytes)
        .map_err(|error| ContractError::Invalid(error.to_string()))
}
