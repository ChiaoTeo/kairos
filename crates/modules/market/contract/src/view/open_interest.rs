use kairos_protocol::generated::kairos::market::v_2 as fb;

use crate::{ContractError, ContractResult};
pub type OpenInterestLatestView<'a> = fb::OpenInterestLatestView<'a>;
pub fn decode(bytes: &[u8]) -> ContractResult<OpenInterestLatestView<'_>> {
    if !fb::open_interest_latest_view_buffer_has_identifier(bytes) {
        return Err(ContractError::Invalid(
            "expected OpenInterestLatestView".into(),
        ));
    }
    fb::root_as_open_interest_latest_view(bytes)
        .map_err(|error| ContractError::Invalid(error.to_string()))
}
