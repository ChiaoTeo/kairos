use crate::{ContractError, ContractResult};
use kairos_transport::{AeronBytePublisher, AeronByteSubscription};
pub struct AccountAeronTransport;
impl AccountAeronTransport {
    pub fn publisher(
        dir: Option<&str>,
        channel: &str,
        stream_id: i32,
    ) -> ContractResult<AeronBytePublisher> {
        AeronBytePublisher::connect(dir, channel, stream_id).map_err(ContractError::Transport)
    }
    pub fn subscriber(
        dir: Option<&str>,
        channel: &str,
        stream_id: i32,
    ) -> ContractResult<AeronByteSubscription> {
        AeronByteSubscription::connect(dir, channel, stream_id).map_err(ContractError::Transport)
    }
}
