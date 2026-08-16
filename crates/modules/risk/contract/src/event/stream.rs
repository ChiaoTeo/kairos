use super::frame::RiskEventFrame;
use crate::{ContractError, ContractResult};
use kairos_transport::AeronByteSubscription;
use std::{
    pin::Pin,
    task::{Context, Poll},
};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
pub struct RiskEventStream {
    inner: ReceiverStream<ContractResult<RiskEventFrame>>,
}
impl RiskEventStream {
    pub fn connect(
        dir: Option<&str>,
        channel: &str,
        stream_id: i32,
        capacity: usize,
    ) -> ContractResult<Self> {
        if capacity == 0 {
            return Err(ContractError::Invalid(
                "event stream capacity must be positive".into(),
            ));
        }
        let (s, r) = mpsc::channel(capacity);
        let d = dir.map(str::to_owned);
        let c = channel.to_owned();
        std::thread::Builder::new()
            .name("kairos-risk-event-stream".into())
            .spawn(move || {
                let mut sub = match AeronByteSubscription::connect(d.as_deref(), &c, stream_id) {
                    Ok(v) => v,
                    Err(e) => {
                        let _ = s.blocking_send(Err(ContractError::Transport(e)));
                        return;
                    }
                };
                loop {
                    match sub.next_frame() {
                        Ok(Some(v)) => {
                            if s.blocking_send(Ok(RiskEventFrame::new(v))).is_err() {
                                break;
                            }
                        }
                        Ok(None) => std::thread::yield_now(),
                        Err(e) => {
                            let _ = s.blocking_send(Err(ContractError::Transport(e)));
                            break;
                        }
                    }
                }
            })
            .map_err(|e| ContractError::Transport(e.to_string()))?;
        Ok(Self {
            inner: ReceiverStream::new(r),
        })
    }
}
impl futures_core::Stream for RiskEventStream {
    type Item = ContractResult<RiskEventFrame>;
    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        Pin::new(&mut self.inner).poll_next(cx)
    }
}
