use kairos_protocol::generated::kairos::execution::v_2 as fb;

use crate::{ContractError, ContractResult};
pub type ActiveOrdersView<'a> = fb::ActiveOrdersView<'a>;
pub fn decode(bytes: &[u8]) -> ContractResult<ActiveOrdersView<'_>> {
    if !fb::active_orders_view_buffer_has_identifier(bytes) {
        return Err(ContractError::Invalid("expected ActiveOrdersView".into()));
    }
    fb::root_as_active_orders_view(bytes).map_err(|error| ContractError::Invalid(error.to_string()))
}
