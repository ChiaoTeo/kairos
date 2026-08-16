use crate::{ContractError, ContractResult};
use kairos_protocol::generated::kairos::market::v_2 as fb;
pub type Ticker24hLatestView<'a> = fb::Ticker24hLatestView<'a>;
pub fn decode(bytes: &[u8]) -> ContractResult<Ticker24hLatestView<'_>> {
    if !fb::ticker_24h_latest_view_buffer_has_identifier(bytes) {
        return Err(ContractError::Invalid(
            "expected Ticker24hLatestView".into(),
        ));
    }
    fb::root_as_ticker_24h_latest_view(bytes)
        .map_err(|error| ContractError::Invalid(error.to_string()))
}
