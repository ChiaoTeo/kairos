use kairos_transport::{AeronBytePublisher, AeronByteSubscription};

use crate::{ContractError, ContractResult};

pub struct MarketAeronTransport;

impl MarketAeronTransport {
    pub fn publisher(
        aeron_dir: Option<&str>,
        channel: &str,
        stream_id: i32,
    ) -> ContractResult<AeronBytePublisher> {
        AeronBytePublisher::connect(aeron_dir, channel, stream_id).map_err(ContractError::Transport)
    }

    pub fn subscriber(
        aeron_dir: Option<&str>,
        channel: &str,
        stream_id: i32,
    ) -> ContractResult<AeronByteSubscription> {
        AeronByteSubscription::connect(aeron_dir, channel, stream_id)
            .map_err(ContractError::Transport)
    }
}
