use std::pin::Pin;
use std::task::{Context, Poll};

use kairos_transport::{AeronByteSubscription, AeronEndpoint};

use super::frame::ExecutionEventFrame;
use crate::{ContractError, ContractResult};
pub struct ExecutionEventStream {
    subscription: AeronByteSubscription,
}
impl ExecutionEventStream {
    pub fn connect(endpoint: &AeronEndpoint, capacity: usize) -> ContractResult<Self> {
        if capacity == 0 {
            return Err(ContractError::Invalid(
                "event stream capacity must be positive".into(),
            ));
        }
        super::publisher::validate_endpoint(endpoint)?;
        Ok(Self {
            subscription: AeronByteSubscription::connect_endpoint(endpoint)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
        })
    }
}
impl futures_core::Stream for ExecutionEventStream {
    type Item = ContractResult<ExecutionEventFrame>;
    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        let _ = cx;
        match self.subscription.next_frame() {
            Ok(Some(frame)) => Poll::Ready(Some(Ok(ExecutionEventFrame::new(frame)))),
            Ok(None) => Poll::Pending,
            Err(error) => Poll::Ready(Some(Err(ContractError::Transport(error.to_string())))),
        }
    }
}
