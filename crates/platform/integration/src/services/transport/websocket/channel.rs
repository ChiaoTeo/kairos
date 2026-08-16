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

    pub(crate) enum AsyncSocketEvent {
        Message(Message),
        Error(String),
        Backpressure,
    }

    pub(crate) struct AsyncTokioSocket {
        commands: mpsc::Sender<Command>,
        events: mpsc::Receiver<AsyncSocketEvent>,
        overflowed: Arc<AtomicBool>,
        worker: Option<JoinHandle<()>>,
    }

    impl AsyncTokioSocket {
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
                                    let _ = events_sender.try_send(AsyncSocketEvent::Error(error.to_string()));
                                    break;
                                }
                            }
                            Some(Command::Pong(payload)) => {
                                if let Err(error) = sink.send(Message::Pong(payload.into())).await {
                                    let _ = events_sender.try_send(AsyncSocketEvent::Error(error.to_string()));
                                    break;
                                }
                            }
                            Some(Command::Close) | None => {
                                let _ = sink.send(Message::Close(None)).await;
                                break;
                            }
                        },
                        message = stream.next() => match message {
                            Some(Ok(message)) => match events_sender.try_send(AsyncSocketEvent::Message(message)) {
                                Ok(()) => {}
                                Err(mpsc::error::TrySendError::Full(_)) => {
                                    worker_overflowed.store(true, Ordering::Release);
                                    break;
                                }
                                Err(mpsc::error::TrySendError::Closed(_)) => break,
                            },
                            Some(Err(error)) => {
                                let _ = events_sender.try_send(AsyncSocketEvent::Error(error.to_string()));
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

        pub(crate) async fn next_event(&mut self) -> AsyncSocketEvent {
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

        fn disconnected_event(&self) -> AsyncSocketEvent {
            if self.overflowed.swap(false, Ordering::AcqRel) {
                AsyncSocketEvent::Backpressure
            } else {
                AsyncSocketEvent::Error("WebSocket worker disconnected".into())
            }
        }
    }

    impl Drop for AsyncTokioSocket {
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

        use super::{AsyncSocketEvent, AsyncTokioSocket};

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

            let mut socket = AsyncTokioSocket::connect(&format!("ws://{address}"), 8)
                .await
                .unwrap();
            match socket.next_event().await {
                AsyncSocketEvent::Message(Message::Text(text)) => {
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

            let mut socket = AsyncTokioSocket::connect(&format!("ws://{address}"), 1)
                .await
                .unwrap();
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
            assert!(matches!(
                socket.next_event().await,
                AsyncSocketEvent::Message(_)
            ));
            assert!(matches!(
                socket.next_event().await,
                AsyncSocketEvent::Backpressure
            ));
        }
    }
}

mod blocking {
    //! Small synchronous facade over an asynchronous Tokio WebSocket task.
    //!
    //! Provider application traits are currently synchronous. Keeping the socket
    //! task behind this facade allows those traits to migrate independently while
    //! ensuring network I/O is performed by `tokio-tungstenite`.

    use std::sync::{
        atomic::{AtomicBool, Ordering},
        mpsc::{self, TryRecvError},
        Arc,
    };

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
        commands: tokio::sync::mpsc::Sender<Command>,
        events: mpsc::Receiver<SocketEvent>,
        overflowed: Arc<AtomicBool>,
    }

    impl TokioSocket {
        pub(crate) fn connect(endpoint: String) -> Result<Self, String> {
            Self::connect_with_event_capacity(endpoint, 1_024)
        }

        pub(crate) fn connect_with_event_capacity(
            endpoint: String,
            event_capacity: usize,
        ) -> Result<Self, String> {
            if event_capacity == 0 {
                return Err("WebSocket event queue capacity must be positive".into());
            }
            let (commands, mut command_receiver) = tokio::sync::mpsc::channel(32);
            let (events_sender, events) = mpsc::sync_channel(event_capacity);
            let overflowed = Arc::new(AtomicBool::new(false));
            let worker_overflowed = Arc::clone(&overflowed);
            let (ready_sender, ready_receiver) = mpsc::sync_channel(1);
            std::thread::Builder::new()
            .name("kairos-websocket".into())
            .spawn(move || {
                let runtime = match tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                {
                    Ok(runtime) => runtime,
                    Err(error) => {
                        let _ = ready_sender.send(Err(error.to_string()));
                        return;
                    }
                };
                runtime.block_on(async move {
                    let socket = match tokio_tungstenite::connect_async(&endpoint).await {
                        Ok((socket, _)) => socket,
                        Err(error) => {
                            let message = error.to_string();
                            let _ = ready_sender.send(Err(message.clone()));
                            let _ = events_sender.send(SocketEvent::Error(message));
                            return;
                        }
                    };
                    let _ = ready_sender.send(Ok(()));
                    let (mut sink, mut stream) = futures_util::StreamExt::split(socket);
                    loop {
                        tokio::select! {
                            command = command_receiver.recv() => match command {
                                Some(Command::Text(text)) => {
                                    if let Err(error) = futures_util::SinkExt::send(&mut sink, Message::Text(text.into())).await {
                                        let _ = events_sender.try_send(SocketEvent::Error(error.to_string()));
                                        break;
                                    }
                                }
                                Some(Command::Pong(payload)) => {
                                    if let Err(error) = futures_util::SinkExt::send(&mut sink, Message::Pong(payload.into())).await {
                                        let _ = events_sender.try_send(SocketEvent::Error(error.to_string()));
                                        break;
                                    }
                                }
                                Some(Command::Close) | None => {
                                    let _ = futures_util::SinkExt::send(&mut sink, Message::Close(None)).await;
                                    break;
                                }
                            },
                            message = futures_util::StreamExt::next(&mut stream) => match message {
                                Some(Ok(message)) => {
                                    match events_sender.try_send(SocketEvent::Message(message)) {
                                        Ok(()) => {}
                                        Err(mpsc::TrySendError::Full(_)) => {
                                            worker_overflowed.store(true, Ordering::Release);
                                            break;
                                        }
                                        Err(mpsc::TrySendError::Disconnected(_)) => break,
                                    }
                                }
                                Some(Err(error)) => {
                                    let _ = events_sender.try_send(SocketEvent::Error(error.to_string()));
                                    break;
                                }
                                None => break,
                            },
                        }
                    }
                });
            })
            .map_err(|error| error.to_string())?;
            ready_receiver.recv().map_err(|error| error.to_string())??;
            Ok(Self {
                commands,
                events,
                overflowed,
            })
        }

        pub(crate) fn send_text(&self, text: String) -> Result<(), String> {
            self.commands
                .try_send(Command::Text(text))
                .map_err(|error| error.to_string())
        }

        pub(crate) fn send_pong(&self, payload: Vec<u8>) -> Result<(), String> {
            self.commands
                .try_send(Command::Pong(payload))
                .map_err(|error| error.to_string())
        }

        pub(crate) fn try_recv(&self) -> Result<Option<SocketEvent>, String> {
            match self.events.try_recv() {
                Ok(event) => Ok(Some(event)),
                Err(TryRecvError::Empty) => Ok(None),
                Err(TryRecvError::Disconnected) => Ok(Some(self.disconnected_event())),
            }
        }

        pub(crate) fn recv(&self) -> Result<SocketEvent, String> {
            self.events
                .recv()
                .or_else(|_| Ok(self.disconnected_event()))
        }

        pub(crate) fn recv_timeout(
            &self,
            timeout: std::time::Duration,
        ) -> Result<Option<SocketEvent>, String> {
            match self.events.recv_timeout(timeout) {
                Ok(event) => Ok(Some(event)),
                Err(mpsc::RecvTimeoutError::Timeout) => Ok(None),
                Err(mpsc::RecvTimeoutError::Disconnected) => Ok(Some(self.disconnected_event())),
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
        }
    }

    #[cfg(test)]
    mod tests {
        use super::TokioSocket;
        use futures_util::{SinkExt, StreamExt};
        use tokio::net::TcpListener;
        use tokio_tungstenite::{accept_async, tungstenite::Message};

        #[test]
        fn connection_failure_is_reported_to_the_caller() {
            let error = match TokioSocket::connect("ws://127.0.0.1:1/does-not-exist".into()) {
                Ok(_) => panic!("an unavailable endpoint must fail before provider startup"),
                Err(error) => error,
            };
            assert!(!error.is_empty());
        }

        #[test]
        fn worker_round_trips_messages_through_a_real_websocket() {
            let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
            listener.set_nonblocking(true).unwrap();
            let address = listener.local_addr().unwrap();
            let server = std::thread::spawn(move || {
                let runtime = tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                    .unwrap();
                runtime.block_on(async move {
                    let listener = TcpListener::from_std(listener).unwrap();
                    let (stream, _) = listener.accept().await.unwrap();
                    let mut socket = accept_async(stream).await.unwrap();
                    socket.send(Message::Ping(vec![1, 2].into())).await.unwrap();
                    socket
                        .send(Message::Text("server-ready".into()))
                        .await
                        .unwrap();
                    loop {
                        match socket.next().await.unwrap().unwrap() {
                            Message::Text(value) if value == "client-ready" => break,
                            Message::Pong(_) => continue,
                            _ => panic!("unexpected client websocket message"),
                        }
                    }
                });
            });

            let socket = TokioSocket::connect(format!("ws://{address}")).unwrap();
            match socket.recv().unwrap() {
                super::SocketEvent::Message(Message::Ping(payload)) => {
                    assert_eq!(payload.as_ref(), &[1, 2]);
                    socket.send_pong(payload.to_vec()).unwrap();
                }
                _ => panic!("unexpected heartbeat event"),
            }
            match socket.recv().unwrap() {
                super::SocketEvent::Message(Message::Text(value)) => {
                    assert_eq!(value, "server-ready")
                }
                super::SocketEvent::Message(other) => panic!("unexpected message: {other:?}"),
                super::SocketEvent::Error(error) => panic!("unexpected websocket error: {error}"),
                super::SocketEvent::Backpressure => panic!("unexpected websocket backpressure"),
            }
            socket.send_text("client-ready".into()).unwrap();
            server.join().unwrap();
        }

        #[test]
        fn bounded_event_queue_reports_backpressure_without_blocking_reader() {
            let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
            listener.set_nonblocking(true).unwrap();
            let address = listener.local_addr().unwrap();
            let server = std::thread::spawn(move || {
                let runtime = tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                    .unwrap();
                runtime.block_on(async move {
                    let listener = TcpListener::from_std(listener).unwrap();
                    let (stream, _) = listener.accept().await.unwrap();
                    let mut socket = accept_async(stream).await.unwrap();
                    socket.send(Message::Text("first".into())).await.unwrap();
                    socket.send(Message::Text("second".into())).await.unwrap();
                    tokio::time::sleep(std::time::Duration::from_millis(25)).await;
                });
            });

            let socket =
                TokioSocket::connect_with_event_capacity(format!("ws://{address}"), 1).unwrap();
            server.join().unwrap();
            assert!(matches!(
                socket.recv().unwrap(),
                super::SocketEvent::Message(_)
            ));
            assert!(matches!(
                socket.recv().unwrap(),
                super::SocketEvent::Backpressure
            ));
        }
    }
}

pub(crate) use asynchronous::{AsyncSocketEvent, AsyncTokioSocket};
pub(crate) use blocking::{SocketEvent, TokioSocket};
