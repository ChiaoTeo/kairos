use kairos_transport::{stream_ids, AeronBytePublisher, AeronEndpoint};

use crate::{ContractError, ContractResult};

pub struct ExecutionEventPublisher {
    publisher: AeronBytePublisher,
}

impl ExecutionEventPublisher {
    pub fn connect(endpoint: &AeronEndpoint) -> ContractResult<Self> {
        validate_endpoint(endpoint)?;
        Ok(Self {
            publisher: AeronBytePublisher::connect_endpoint(endpoint)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
        })
    }

    pub fn publish(&self, payload: &[u8]) -> ContractResult<()> {
        super::decode_event(payload)?;
        self.publisher
            .publish(payload)
            .map(|_| ())
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}

pub(super) fn validate_endpoint(endpoint: &AeronEndpoint) -> ContractResult<()> {
    if endpoint.stream_id() != stream_ids::EXECUTION_EVENTS {
        return Err(ContractError::Invalid(format!(
            "Execution events require stream id {}, received {}",
            stream_ids::EXECUTION_EVENTS,
            endpoint.stream_id()
        )));
    }
    Ok(())
}
