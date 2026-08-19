use kairos_protocol::generated::kairos::market::v_2 as fb;

use crate::{ContractError, ContractResult};

pub type IndexPriceLatestView<'a> = fb::IndexPriceLatestView<'a>;

pub fn decode(bytes: &[u8]) -> ContractResult<IndexPriceLatestView<'_>> {
    if !fb::index_price_latest_view_buffer_has_identifier(bytes) {
        return Err(ContractError::Invalid(
            "expected IndexPriceLatestView".into(),
        ));
    }
    fb::root_as_index_price_latest_view(bytes)
        .map_err(|error| ContractError::Invalid(error.to_string()))
}
