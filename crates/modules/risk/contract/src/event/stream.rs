use super::frame::RiskEventFrame;
use crate::{ContractError, ContractResult};
use kairos_transport::{stream_ids, AeronByteSubscription, AeronEndpoint};
use std::{
    pin::Pin,
    task::{Context, Poll},
};
pub struct RiskEventStream {
    subscription: AeronByteSubscription,
}
impl RiskEventStream {
    pub fn connect(endpoint: &AeronEndpoint, capacity: usize) -> ContractResult<Self> {
        if capacity == 0 {
            return Err(ContractError::Invalid(
                "event stream capacity must be positive".into(),
            ));
        }
        if endpoint.stream_id() != stream_ids::RISK_EVENTS {
            return Err(ContractError::Invalid(format!(
                "Risk events require stream id {}, received {}",
                stream_ids::RISK_EVENTS,
                endpoint.stream_id()
            )));
        }
        Ok(Self {
            subscription: AeronByteSubscription::connect_endpoint(endpoint)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
        })
    }
}
impl futures_core::Stream for RiskEventStream {
    type Item = ContractResult<RiskEventFrame>;
    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        let _ = cx;
        match self.subscription.next_frame() {
            Ok(Some(frame)) => Poll::Ready(Some(Ok(RiskEventFrame::new(frame)))),
            Ok(None) => Poll::Pending,
            Err(error) => Poll::Ready(Some(Err(ContractError::Transport(error.to_string())))),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_another_modules_stream_before_connecting() {
        let endpoint = AeronEndpoint::from_parts(
            None,
            "aeron:ipc",
            kairos_transport::stream_ids::MARKET_EVENTS,
        )
        .unwrap();
        assert!(matches!(
            RiskEventStream::connect(&endpoint, 1),
            Err(ContractError::Invalid(_))
        ));
    }
}
