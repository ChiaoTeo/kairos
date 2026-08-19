use kairos_protocol::generated::kairos::execution::v_2 as fb;

use crate::{ContractError, ContractResult};

pub type CurrentExecutionView<'a> = fb::CurrentExecutionView<'a>;

pub fn decode(bytes: &[u8]) -> ContractResult<CurrentExecutionView<'_>> {
    if !fb::current_execution_view_buffer_has_identifier(bytes) {
        return Err(ContractError::Invalid(
            "expected ECV2 CurrentExecutionView".into(),
        ));
    }
    fb::root_as_current_execution_view(bytes)
        .map_err(|error| ContractError::Invalid(error.to_string()))
}
