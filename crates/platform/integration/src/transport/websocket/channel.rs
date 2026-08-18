//! WebSocket channels driven either by the caller runtime or an explicit blocking facade.

mod asynchronous {
    //! Async WebSocket transport driven by a business-owned Tokio runtime.
    //!
    //! Unlike the legacy blocking facade, this type never creates a thread or a
    //! runtime. The async caller's current runtime owns task scheduling and
    //! shutdown, like reqwest's default async client.

    use std::sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    };

    use futures_util::{SinkExt, StreamExt};
    use tokio::sync::mpsc;
    use tokio::task::JoinHandle;
    use tokio_tungstenite::tungstenite::Message;

    enum Command {
        Text(String),
        Pong(Vec<u8>),
        Close,
    }

    pub(crate) enum SocketEvent {
        Message(Message),
        Error(String),
        Backpressure,
    }

    pub(crate) struct TokioSocket {
        commands: mpsc::Sender<Command>,
        events: mpsc::Receiver<SocketEvent>,
        overflowed: Arc<AtomicBool>,
        worker: Option<JoinHandle<()>>,
    }

    impl TokioSocket {
        pub(crate) async fn connect(endpoint: &str, event_capacity: usize) -> Result<Self, String> {
            if event_capacity == 0 {
                return Err("WebSocket event queue capacity must be positive".into());
            }
            let socket = tokio_tungstenite::connect_async(endpoint)
                .await
                .map_err(|error| error.to_string())?
                .0;
            let (commands, mut command_receiver) = mpsc::channel(32);
            let (events_sender, events) = mpsc::channel(event_capacity);
            let overflowed = Arc::new(AtomicBool::new(false));
            let worker_overflowed = Arc::clone(&overflowed);
            let worker = tokio::spawn(async move {
                let (mut sink, mut stream) = socket.split();
                loop {
                    tokio::select! {
                        command = command_receiver.recv() => match command {
                            Some(Command::Text(text)) => {
                                if let Err(error) = sink.send(Message::Text(text.into())).await {
                                    let _ = events_sender.try_send(SocketEvent::Error(error.to_string()));
                                    break;
                                }
                            }
                            Some(Command::Pong(payload)) => {
                                if let Err(error) = sink.send(Message::Pong(payload.into())).await {
                                    let _ = events_sender.try_send(SocketEvent::Error(error.to_string()));
                                    break;
                                }
                            }
                            Some(Command::Close) | None => {
                                let _ = sink.send(Message::Close(None)).await;
                                break;
                            }
                        },
                        message = stream.next() => match message {
                            Some(Ok(message)) => match events_sender.try_send(SocketEvent::Message(message)) {
                                Ok(()) => {}
                                Err(mpsc::error::TrySendError::Full(_)) => {
                                    worker_overflowed.store(true, Ordering::Release);
                                    break;
                                }
                                Err(mpsc::error::TrySendError::Closed(_)) => break,
                            },
                            Some(Err(error)) => {
                                let _ = events_sender.try_send(SocketEvent::Error(error.to_string()));
                                break;
                            }
                            None => break,
                        },
                    }
                }
            });
            Ok(Self {
                commands,
                events,
                overflowed,
                worker: Some(worker),
            })
        }

        pub(crate) async fn send_text(&self, text: String) -> Result<(), String> {
            self.commands
                .send(Command::Text(text))
                .await
                .map_err(|error| error.to_string())
        }

        pub(crate) async fn send_pong(&self, payload: Vec<u8>) -> Result<(), String> {
            self.commands
                .send(Command::Pong(payload))
                .await
                .map_err(|error| error.to_string())
        }

        pub(crate) async fn next_event(&mut self) -> SocketEvent {
            self.events
                .recv()
                .await
                .unwrap_or_else(|| self.disconnected_event())
        }

        pub(crate) async fn close(&mut self) {
            let _ = self.commands.send(Command::Close).await;
            if let Some(worker) = self.worker.take() {
                let _ = worker.await;
            }
        }

        fn disconnected_event(&self) -> SocketEvent {
            if self.overflowed.swap(false, Ordering::AcqRel) {
                SocketEvent::Backpressure
            } else {
                SocketEvent::Error("WebSocket worker disconnected".into())
            }
        }
    }

    impl Drop for TokioSocket {
        fn drop(&mut self) {
            let _ = self.commands.try_send(Command::Close);
            if let Some(worker) = self.worker.take() {
                worker.abort();
            }
        }
    }

    #[cfg(test)]
    mod tests {
        use futures_util::SinkExt;
        use tokio::net::TcpListener;
        use tokio_tungstenite::{accept_async, tungstenite::Message};

        use super::{SocketEvent, TokioSocket};

        #[tokio::test(flavor = "current_thread")]
        async fn socket_uses_the_callers_current_thread_runtime() {
            let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
            let address = listener.local_addr().unwrap();
            let server = tokio::spawn(async move {
                let (stream, _) = listener.accept().await.unwrap();
                let mut socket = accept_async(stream).await.unwrap();
                socket
                    .send(Message::Text("shared-runtime".into()))
                    .await
                    .unwrap();
            });

            let mut socket = TokioSocket::connect(&format!("ws://{address}"), 8)
                .await
                .unwrap();
            match socket.next_event().await {
                SocketEvent::Message(Message::Text(text)) => {
                    assert_eq!(text, "shared-runtime")
                }
                _ => panic!("expected text event"),
            }
            socket.close().await;
            server.await.unwrap();
        }

        #[tokio::test(flavor = "current_thread")]
        async fn queue_overflow_is_reported_after_buffered_events_are_drained() {
            let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
            let address = listener.local_addr().unwrap();
            tokio::spawn(async move {
                let (stream, _) = listener.accept().await.unwrap();
                let mut socket = accept_async(stream).await.unwrap();
                socket.send(Message::Text("one".into())).await.unwrap();
                socket.send(Message::Text("two".into())).await.unwrap();
                socket.send(Message::Text("three".into())).await.unwrap();
            });

            let mut socket = TokioSocket::connect(&format!("ws://{address}"), 1)
                .await
                .unwrap();
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
            assert!(matches!(socket.next_event().await, SocketEvent::Message(_)));
            assert!(matches!(
                socket.next_event().await,
                SocketEvent::Backpressure
            ));
        }
    }
}

pub(crate) use asynchronous::{SocketEvent, TokioSocket};
