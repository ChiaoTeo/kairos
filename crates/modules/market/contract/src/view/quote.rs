use kairos_protocol::generated::kairos::market::v_2 as fb;

use crate::{ContractError, ContractResult};

pub type QuoteLatestView<'a> = fb::QuoteLatestView<'a>;

pub fn decode(bytes: &[u8]) -> ContractResult<QuoteLatestView<'_>> {
    if !fb::quote_latest_view_buffer_has_identifier(bytes) {
        return Err(ContractError::Invalid("expected QuoteLatestView".into()));
    }
    fb::root_as_quote_latest_view(bytes).map_err(|error| ContractError::Invalid(error.to_string()))
}
