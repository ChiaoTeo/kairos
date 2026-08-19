use kairos_transport::{AeronBytePublisher, AeronEndpoint, stream_ids};

use crate::{ContractError, ContractResult};

pub struct MarketEventPublisher {
    publisher: AeronBytePublisher,
}

impl MarketEventPublisher {
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
    if endpoint.stream_id() != stream_ids::MARKET_EVENTS {
        return Err(ContractError::Invalid(format!(
            "Market events require stream id {}, received {}",
            stream_ids::MARKET_EVENTS,
            endpoint.stream_id()
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_another_modules_stream() {
        let endpoint =
            AeronEndpoint::from_parts(None, "aeron:ipc", stream_ids::RISK_EVENTS).unwrap();
        assert!(matches!(
            MarketEventPublisher::connect(&endpoint),
            Err(ContractError::Invalid(_))
        ));
    }
}
