use kairos_protocol::generated::kairos::market::v_2 as fb;

use crate::{ContractError, ContractResult};

pub type OrderBookLatestView<'a> = fb::OrderBookLatestView<'a>;

pub fn decode(bytes: &[u8]) -> ContractResult<OrderBookLatestView<'_>> {
    if !fb::order_book_latest_view_buffer_has_identifier(bytes) {
        return Err(ContractError::Invalid(
            "expected OrderBookLatestView".into(),
        ));
    }
    fb::root_as_order_book_latest_view(bytes)
        .map_err(|error| ContractError::Invalid(error.to_string()))
}
