use crate::{ContractError, ContractResult};
use kairos_protocol::generated::kairos::market::v_2 as fb;

pub type MarkPriceLatestView<'a> = fb::MarkPriceLatestView<'a>;

pub fn decode(bytes: &[u8]) -> ContractResult<MarkPriceLatestView<'_>> {
    if !fb::mark_price_latest_view_buffer_has_identifier(bytes) {
        return Err(ContractError::Invalid(
            "expected MarkPriceLatestView".into(),
        ));
    }
    fb::root_as_mark_price_latest_view(bytes)
        .map_err(|error| ContractError::Invalid(error.to_string()))
}
