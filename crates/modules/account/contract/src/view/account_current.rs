use kairos_protocol::generated::kairos::account::v_2 as fb;

use crate::{ContractError, ContractResult};
pub type AccountCurrentView<'a> = fb::AccountCurrentView<'a>;
pub fn decode(bytes: &[u8]) -> ContractResult<AccountCurrentView<'_>> {
    if !fb::account_current_view_buffer_has_identifier(bytes) {
        return Err(ContractError::Invalid("expected AccountCurrentView".into()));
    }
    fb::root_as_account_current_view(bytes)
        .map_err(|error| ContractError::Invalid(error.to_string()))
}
