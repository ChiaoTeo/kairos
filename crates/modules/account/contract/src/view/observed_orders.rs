use crate::{ContractError, ContractResult};
use kairos_protocol::generated::kairos::account::v_2 as fb;
pub type ObservedOrdersCurrentView<'a> = fb::ObservedOrdersCurrentView<'a>;
pub fn decode(bytes: &[u8]) -> ContractResult<ObservedOrdersCurrentView<'_>> {
    if !fb::observed_orders_current_view_buffer_has_identifier(bytes) {
        return Err(ContractError::Invalid(
            "expected ObservedOrdersCurrentView".into(),
        ));
    }
    fb::root_as_observed_orders_current_view(bytes)
        .map_err(|error| ContractError::Invalid(error.to_string()))
}
