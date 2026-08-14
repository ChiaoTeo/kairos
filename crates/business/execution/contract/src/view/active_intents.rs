use crate::{ContractError, ContractResult};
use kairos_protocol::generated::kairos::execution::v_2 as fb;
pub type ActiveIntentsView<'a> = fb::ActiveIntentsView<'a>;
pub fn decode(bytes: &[u8]) -> ContractResult<ActiveIntentsView<'_>> {
    if !fb::active_intents_view_buffer_has_identifier(bytes) {
        return Err(ContractError::Invalid("expected ActiveIntentsView".into()));
    }
    fb::root_as_active_intents_view(bytes)
        .map_err(|error| ContractError::Invalid(error.to_string()))
}
