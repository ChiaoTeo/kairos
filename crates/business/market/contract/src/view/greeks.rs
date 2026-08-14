use kairos_protocol::generated::kairos::market::v_2 as fb;

use crate::{ContractError, ContractResult};

pub type GreeksLatestView<'a> = fb::GreeksLatestView<'a>;

pub fn decode(bytes: &[u8]) -> ContractResult<GreeksLatestView<'_>> {
    if !fb::greeks_latest_view_buffer_has_identifier(bytes) {
        return Err(ContractError::Invalid("expected GreeksLatestView".into()));
    }
    fb::root_as_greeks_latest_view(bytes).map_err(|error| ContractError::Invalid(error.to_string()))
}
