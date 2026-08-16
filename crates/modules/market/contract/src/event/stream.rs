use std::pin::Pin;
use std::task::{Context, Poll};

use kairos_transport::AeronByteSubscription;
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;

use crate::{ContractError, ContractResult};

use super::frame::MarketEventFrame;

pub struct MarketEventStream {
    inner: ReceiverStream<ContractResult<MarketEventFrame>>,
}

impl MarketEventStream {
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
            .name("kairos-market-event-stream".into())
            .spawn(move || {
                let mut subscription =
                    match AeronByteSubscription::connect(aeron_dir.as_deref(), &channel, stream_id)
                    {
                        Ok(subscription) => subscription,
                        Err(error) => {
                            let _ = sender.blocking_send(Err(ContractError::Transport(error)));
                            return;
                        }
                    };
                loop {
                    match subscription.next_frame() {
                        Ok(Some(frame)) => {
                            if sender
                                .blocking_send(Ok(MarketEventFrame::new(frame)))
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

impl futures_core::Stream for MarketEventStream {
    type Item = ContractResult<MarketEventFrame>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        Pin::new(&mut self.inner).poll_next(cx)
    }
}
