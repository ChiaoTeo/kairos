use super::*;

enum EventGatewayRequest {
    Connect {
        reply: SyncSender<Result<(), IntegrationError>>,
    },
    Disconnect {
        reply: SyncSender<Result<(), IntegrationError>>,
    },
    Reconnect {
        reply: SyncSender<Result<(), IntegrationError>>,
    },
    Health {
        reply: SyncSender<ConnectionHealth>,
    },
    Next {
        reply: SyncSender<
            Result<Option<ExternalEventEnvelope<ExternalExecutionEvent>>, IntegrationError>,
        >,
    },
}

pub struct AsyncQueuedOrderEventSource {
    sender: tokio::sync::mpsc::Sender<EventGatewayRequest>,
}

pub struct AsyncEventGatewayWorker<C> {
    source: C,
    receiver: tokio::sync::mpsc::Receiver<EventGatewayRequest>,
}

impl AsyncQueuedOrderEventSource {
    pub fn channel<C>(source: C, capacity: usize) -> (Self, AsyncEventGatewayWorker<C>)
    where
        C: AsyncOrderEventSource,
    {
        let (sender, receiver) = tokio::sync::mpsc::channel(capacity.max(1));
        (
            Self { sender },
            AsyncEventGatewayWorker { source, receiver },
        )
    }

    fn send(&self, request: EventGatewayRequest) -> Result<(), IntegrationError> {
        self.sender.try_send(request).map_err(|error| match error {
            tokio::sync::mpsc::error::TrySendError::Full(_) => {
                IntegrationError::Backpressure("async event gateway queue is full".into())
            }
            tokio::sync::mpsc::error::TrySendError::Closed(_) => {
                IntegrationError::Unavailable("async event gateway is stopped".into())
            }
        })
    }
}

impl OrderEventSource for AsyncQueuedOrderEventSource {
    fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        let (reply, receiver) = std::sync::mpsc::sync_channel(1);
        self.send(EventGatewayRequest::Connect { reply })?;
        receiver.recv().map_err(|_| {
            IntegrationError::Unavailable("async event gateway did not respond".into())
        })?
    }

    fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        let (reply, receiver) = std::sync::mpsc::sync_channel(1);
        self.send(EventGatewayRequest::Disconnect { reply })?;
        receiver.recv().map_err(|_| {
            IntegrationError::Unavailable("async event gateway did not respond".into())
        })?
    }

    fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        let (reply, receiver) = std::sync::mpsc::sync_channel(1);
        self.send(EventGatewayRequest::Reconnect { reply })?;
        receiver.recv().map_err(|_| {
            IntegrationError::Unavailable("async event gateway did not respond".into())
        })?
    }

    fn channel_health(&self) -> ConnectionHealth {
        let (reply, receiver) = std::sync::mpsc::sync_channel(1);
        if self.send(EventGatewayRequest::Health { reply }).is_err() {
            return ConnectionHealth {
                lifecycle: kairos_integration::application::ConnectionLifecycle::Failed,
                healthy: false,
                authenticated: false,
                last_error: Some("async event gateway is stopped".into()),
            };
        }
        receiver.recv().unwrap_or(ConnectionHealth {
            lifecycle: kairos_integration::application::ConnectionLifecycle::Failed,
            healthy: false,
            authenticated: false,
            last_error: Some("async event gateway did not respond".into()),
        })
    }

    fn try_next_order_event(
        &mut self,
    ) -> Result<Option<ExternalEventEnvelope<ExternalExecutionEvent>>, IntegrationError> {
        let (reply, receiver) = std::sync::mpsc::sync_channel(1);
        self.send(EventGatewayRequest::Next { reply })?;
        receiver.recv().map_err(|_| {
            IntegrationError::Unavailable("async event gateway did not respond".into())
        })?
    }
}

impl<C> AsyncEventGatewayWorker<C>
where
    C: AsyncOrderEventSource,
{
    pub async fn run(mut self, mut shutdown: tokio::sync::watch::Receiver<bool>) {
        loop {
            let request = tokio::select! {
                biased;
                changed = shutdown.changed() => {
                    if changed.is_err() || *shutdown.borrow() {
                        break;
                    }
                    continue;
                }
                request = self.receiver.recv() => request,
            };
            let Some(request) = request else {
                break;
            };
            match request {
                EventGatewayRequest::Connect { reply } => {
                    let _ = reply.send(self.source.connect_channel().await);
                }
                EventGatewayRequest::Disconnect { reply } => {
                    let _ = reply.send(self.source.disconnect_channel().await);
                }
                EventGatewayRequest::Reconnect { reply } => {
                    let _ = reply.send(self.source.reconnect_channel().await);
                }
                EventGatewayRequest::Health { reply } => {
                    let _ = reply.send(self.source.channel_health());
                }
                EventGatewayRequest::Next { reply } => {
                    let result = match tokio::time::timeout(
                        Duration::from_secs(1),
                        self.source.next_order_event(),
                    )
                    .await
                    {
                        Ok(result) => result.map(Some),
                        Err(_) => Ok(None),
                    };
                    let _ = reply.send(result);
                }
            }
        }
        let _ = self.source.disconnect_channel().await;
    }
}
