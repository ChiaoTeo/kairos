use kairos_protocol::generated::kairos::market::v_2 as fb;

use crate::{ContractError, ContractResult};

pub type BarWindowView<'a> = fb::BarWindowView<'a>;

pub fn decode(bytes: &[u8]) -> ContractResult<BarWindowView<'_>> {
    if !fb::bar_window_view_buffer_has_identifier(bytes) {
        return Err(ContractError::Invalid("expected BarWindowView".into()));
    }
    fb::root_as_bar_window_view(bytes).map_err(|error| ContractError::Invalid(error.to_string()))
}
