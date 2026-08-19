use kairos_protocol::generated::kairos::market::v_2 as fb;

use crate::{ContractError, ContractResult};
pub type RateLatestView<'a> = fb::RateLatestView<'a>;
pub fn decode(bytes: &[u8]) -> ContractResult<RateLatestView<'_>> {
    if !fb::rate_latest_view_buffer_has_identifier(bytes) {
        return Err(ContractError::Invalid("expected RateLatestView".into()));
    }
    fb::root_as_rate_latest_view(bytes).map_err(|error| ContractError::Invalid(error.to_string()))
}
