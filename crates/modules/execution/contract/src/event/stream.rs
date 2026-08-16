use super::frame::ExecutionEventFrame;
use crate::{ContractError, ContractResult};
use kairos_transport::AeronByteSubscription;
use std::pin::Pin;
use std::task::{Context, Poll};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
pub struct ExecutionEventStream {
    inner: ReceiverStream<ContractResult<ExecutionEventFrame>>,
}
impl ExecutionEventStream {
    pub fn connect(
        aeron_dir: Option<&str>,
        channel: &str,
        stream_id: i32,
        capacity: usize,
    ) -> ContractResult<Self> {
        if capacity == 0 {
            return Err(ContractError::Invalid(
                "event stream capacity must be positive".into(),
            ));
        }
        let (sender, receiver) = mpsc::channel(capacity);
        let aeron_dir = aeron_dir.map(str::to_owned);
        let channel = channel.to_owned();
        std::thread::Builder::new()
            .name("kairos-execution-event-stream".into())
            .spawn(move || {
                let mut subscription =
                    match AeronByteSubscription::connect(aeron_dir.as_deref(), &channel, stream_id)
                    {
                        Ok(value) => value,
                        Err(error) => {
                            let _ = sender.blocking_send(Err(ContractError::Transport(error)));
                            return;
                        }
                    };
                loop {
                    match subscription.next_frame() {
                        Ok(Some(frame)) => {
                            if sender
                                .blocking_send(Ok(ExecutionEventFrame::new(frame)))
                                .is_err()
                            {
                                break;
                            }
                        }
                        Ok(None) => std::thread::yield_now(),
                        Err(error) => {
                            let _ = sender.blocking_send(Err(ContractError::Transport(error)));
                            break;
                        }
                    }
                }
            })
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self {
            inner: ReceiverStream::new(receiver),
        })
    }
}
impl futures_core::Stream for ExecutionEventStream {
    type Item = ContractResult<ExecutionEventFrame>;
    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        Pin::new(&mut self.inner).poll_next(cx)
    }
}
