use std::pin::Pin;
use std::task::{Context, Poll};

use kairos_transport::{AeronByteSubscription, AeronEndpoint, stream_ids};

use super::CapitalEventFrame;
use crate::{ContractError, ContractResult};

pub struct CapitalEventStream {
    subscription: AeronByteSubscription,
}

impl CapitalEventStream {
    pub fn connect(endpoint: &AeronEndpoint, capacity: usize) -> ContractResult<Self> {
        if capacity == 0 {
            return Err(ContractError::Invalid(
                "event stream capacity must be positive".into(),
            ));
        }
        if endpoint.stream_id() != stream_ids::CAPITAL_EVENTS {
            return Err(ContractError::Invalid(format!(
                "Capital events require stream id {}, received {}",
                stream_ids::CAPITAL_EVENTS,
                endpoint.stream_id()
            )));
        }
        Ok(Self {
            subscription: AeronByteSubscription::connect_endpoint(endpoint)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
        })
    }
}

impl futures_core::Stream for CapitalEventStream {
    type Item = ContractResult<CapitalEventFrame>;

    fn poll_next(mut self: Pin<&mut Self>, _cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        match self.subscription.next_frame() {
            Ok(Some(frame)) => Poll::Ready(Some(Ok(CapitalEventFrame::new(frame)))),
            Ok(None) => Poll::Pending,
            Err(error) => Poll::Ready(Some(Err(ContractError::Transport(error.to_string())))),
        }
    }
}
