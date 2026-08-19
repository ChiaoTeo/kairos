//! WebSocket channels driven either by the caller runtime or an explicit blocking facade.

mod asynchronous {
    //! Async WebSocket transport directly owned and polled by its concrete
    //! Integration connection.

    use std::collections::VecDeque;
    use std::pin::Pin;
    use std::task::{Context, Poll};

    use futures_util::{Sink, SinkExt, Stream, StreamExt};
    use tokio::net::TcpStream;
    use tokio_tungstenite::tungstenite::Message;
    use tokio_tungstenite::{MaybeTlsStream, WebSocketStream};

    pub(crate) enum SocketEvent {
        Message(Message),
        Error(String),
    }

    pub(crate) struct TokioSocket {
        stream: WebSocketStream<MaybeTlsStream<TcpStream>>,
        pending_control: VecDeque<Message>,
        control_capacity: usize,
        last_activity: tokio::time::Instant,
    }

    impl TokioSocket {
        pub(crate) async fn connect(endpoint: &str, event_capacity: usize) -> Result<Self, String> {
            if event_capacity == 0 {
                return Err("WebSocket event queue capacity must be positive".into());
            }
            let stream = tokio_tungstenite::connect_async(endpoint)
                .await
                .map_err(|error| error.to_string())?
                .0;
            Ok(Self {
                stream,
                pending_control: VecDeque::new(),
                control_capacity: event_capacity,
                last_activity: tokio::time::Instant::now(),
            })
        }

        pub(crate) async fn send_text(&mut self, text: String) -> Result<(), String> {
            let result = self
                .stream
                .send(Message::Text(text.into()))
                .await
                .map_err(|error| error.to_string());
            if result.is_ok() {
                self.last_activity = tokio::time::Instant::now();
            }
            result
        }

        pub(crate) async fn send_pong(&mut self, payload: Vec<u8>) -> Result<(), String> {
            let result = self
                .stream
                .send(Message::Pong(payload.into()))
                .await
                .map_err(|error| error.to_string());
            if result.is_ok() {
                self.last_activity = tokio::time::Instant::now();
            }
            result
        }

        pub(crate) fn poll_next_event(&mut self, cx: &mut Context<'_>) -> Poll<SocketEvent> {
            loop {
                while let Some(message) = self.pending_control.pop_front() {
                    match Pin::new(&mut self.stream).poll_ready(cx) {
                        Poll::Ready(Ok(())) => {},
                        Poll::Ready(Err(error)) => {
                            return Poll::Ready(SocketEvent::Error(error.to_string()));
                        },
                        Poll::Pending => {
                            self.pending_control.push_front(message);
                            return Poll::Pending;
                        },
                    }
                    if let Err(error) = Pin::new(&mut self.stream).start_send(message) {
                        return Poll::Ready(SocketEvent::Error(error.to_string()));
                    }
                }
                match Pin::new(&mut self.stream).poll_flush(cx) {
                    Poll::Ready(Ok(())) => {},
                    Poll::Ready(Err(error)) => {
                        return Poll::Ready(SocketEvent::Error(error.to_string()));
                    },
                    Poll::Pending => return Poll::Pending,
                }

                match Pin::new(&mut self.stream).poll_next(cx) {
                    Poll::Ready(Some(Ok(Message::Ping(payload)))) => {
                        self.last_activity = tokio::time::Instant::now();
                        if self.pending_control.len() == self.control_capacity {
                            return Poll::Ready(SocketEvent::Error(
                                "WebSocket control queue overflowed".into(),
                            ));
                        }
                        self.pending_control.push_back(Message::Pong(payload));
                    },
                    Poll::Ready(Some(Ok(message))) => {
                        self.last_activity = tokio::time::Instant::now();
                        return Poll::Ready(SocketEvent::Message(message));
                    },
                    Poll::Ready(Some(Err(error))) => {
                        return Poll::Ready(SocketEvent::Error(error.to_string()));
                    },
                    Poll::Ready(None) => {
                        return Poll::Ready(SocketEvent::Error("WebSocket stream closed".into()));
                    },
                    Poll::Pending => return Poll::Pending,
                }
            }
        }

        pub(crate) async fn next_event(&mut self) -> SocketEvent {
            loop {
                match self.stream.next().await {
                    Some(Ok(Message::Ping(payload))) => {
                        self.last_activity = tokio::time::Instant::now();
                        if let Err(error) = self.stream.send(Message::Pong(payload)).await {
                            return SocketEvent::Error(error.to_string());
                        }
                    },
                    Some(Ok(message)) => {
                        self.last_activity = tokio::time::Instant::now();
                        return SocketEvent::Message(message);
                    },
                    Some(Err(error)) => return SocketEvent::Error(error.to_string()),
                    None => return SocketEvent::Error("WebSocket stream closed".into()),
                }
            }
        }

        pub(crate) async fn close(&mut self) {
            let _ = self.stream.close(None).await;
        }

        pub(crate) fn last_activity(&self) -> tokio::time::Instant {
            self.last_activity
        }
    }

    #[cfg(test)]
    mod tests {
        use futures_util::SinkExt;
        use tokio::net::TcpListener;
        use tokio_tungstenite::accept_async;
        use tokio_tungstenite::tungstenite::Message;

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
                },
                _ => panic!("expected text event"),
            }
            socket.close().await;
            server.await.unwrap();
        }

        #[tokio::test(flavor = "current_thread")]
        async fn direct_owner_reads_all_events_without_an_intermediate_queue() {
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
            for _ in 0..3 {
                assert!(matches!(socket.next_event().await, SocketEvent::Message(_)));
            }
        }
    }
}

pub(crate) use asynchronous::{SocketEvent, TokioSocket};
