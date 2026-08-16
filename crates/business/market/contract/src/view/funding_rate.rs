use crate::{ContractError, ContractResult};
use kairos_protocol::generated::kairos::market::v_2 as fb;
pub type FundingRateLatestView<'a> = fb::FundingRateLatestView<'a>;
pub fn decode(bytes: &[u8]) -> ContractResult<FundingRateLatestView<'_>> {
    if !fb::funding_rate_latest_view_buffer_has_identifier(bytes) {
        return Err(ContractError::Invalid(
            "expected FundingRateLatestView".into(),
        ));
    }
    fb::root_as_funding_rate_latest_view(bytes)
        .map_err(|error| ContractError::Invalid(error.to_string()))
}
