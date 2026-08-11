//! Async Binance Spot account/balance/order/fill event capability.

use crate::application::capabilities::account_facts::ExternalAccountEvent;
use crate::application::{AsyncAccountEventSource, IntegrationError};
use crate::domain::ConnectionHealth;

use super::account::BinanceSpotAccountClient;
use super::async_user_data::BinanceSpotAsyncUserDataChannel;
use super::user_stream::parse_user_event_value;

pub(crate) struct BinanceSpotAsyncAccountEventSource {
    segment_key: String,
    product: &'static str,
    channel: BinanceSpotAsyncUserDataChannel,
}

impl BinanceSpotAsyncAccountEventSource {
    pub(crate) fn from_client_with_capacity(
        binding_id: impl Into<String>,
        segment_key: impl Into<String>,
        client: BinanceSpotAccountClient,
        websocket_endpoint: impl Into<String>,
        event_queue_capacity: usize,
    ) -> Result<Self, IntegrationError> {
        let segment_key = segment_key.into();
        if segment_key.trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "Binance Spot account stream segment key is required".into(),
            ));
        }
        Ok(Self {
            segment_key,
            product: "binance-spot",
            channel: BinanceSpotAsyncUserDataChannel::new(
                binding_id,
                client,
                websocket_endpoint,
                event_queue_capacity,
            )?,
        })
    }
}

impl AsyncAccountEventSource for BinanceSpotAsyncAccountEventSource {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.channel.connect().await
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.channel.disconnect().await
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.channel.reconnect().await
    }

    fn channel_health(&self) -> ConnectionHealth {
        self.channel.health()
    }

    async fn next_account_event(&mut self) -> Result<ExternalAccountEvent, IntegrationError> {
        loop {
            let text = self.channel.next_text().await?;
            let outer: serde_json::Value = serde_json::from_str(&text)
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?;
            let event = outer.get("event").unwrap_or(&outer);
            match event.get("e").and_then(serde_json::Value::as_str) {
                Some("eventStreamTerminated") => {
                    return Err(IntegrationError::ResyncRequired(
                        "Binance Spot user data subscription terminated".into(),
                    ));
                }
                Some("serverShutdown") => {
                    return Err(IntegrationError::Unavailable(
                        "Binance Spot WebSocket API server is shutting down".into(),
                    ));
                }
                _ => {}
            }
            if let Some(event) = parse_user_event_value(&self.segment_key, self.product, event)
                .map_err(IntegrationError::InvalidPayload)?
            {
                return Ok(event);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};

    use futures_util::{SinkExt, StreamExt};

    use crate::application::capabilities::account_facts::ExternalAccountEvent;
    use crate::application::AsyncAccountEventSource;
    use crate::services::participants::binance::spot::account::BinanceSpotAccountClient;

    use super::BinanceSpotAsyncAccountEventSource;

    #[tokio::test(flavor = "current_thread")]
    async fn account_event_awaits_provider_data_on_the_callers_runtime() {
        let time_listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let time_address = time_listener.local_addr().unwrap();
        let time_server = std::thread::spawn(move || {
            let (mut stream, _) = time_listener.accept().unwrap();
            let mut request = [0_u8; 2_048];
            let read = stream.read(&mut request).unwrap();
            assert!(String::from_utf8_lossy(&request[..read]).starts_with("GET /api/v3/time "));
            let server_time = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis() as u64;
            let body = serde_json::json!({"serverTime": server_time}).to_string();
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            )
            .unwrap();
        });

        let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
            .await
            .unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
            let request = socket.next().await.unwrap().unwrap().into_text().unwrap();
            let request: serde_json::Value = serde_json::from_str(&request).unwrap();
            socket
                .send(tokio_tungstenite::tungstenite::Message::Text(
                    serde_json::json!({
                        "id": request.get("id").unwrap(),
                        "status": 200,
                        "result": {"subscriptionId": 9}
                    })
                    .to_string()
                    .into(),
                ))
                .await
                .unwrap();
            socket
                .send(tokio_tungstenite::tungstenite::Message::Text(
                    serde_json::json!({
                        "subscriptionId": 9,
                        "event": {
                            "e": "outboundAccountPosition",
                            "E": 1_700_000_000_000_u64,
                            "B": [{"a": "USDT", "f": "12.5", "l": "0.5"}]
                        }
                    })
                    .to_string()
                    .into(),
                ))
                .await
                .unwrap();
        });

        let client =
            BinanceSpotAccountClient::new("api-key", "secret", format!("http://{time_address}"))
                .unwrap();
        let mut source = BinanceSpotAsyncAccountEventSource::from_client_with_capacity(
            "account.binance.spot",
            "spot",
            client,
            format!("ws://{address}"),
            8,
        )
        .unwrap();

        let event = source.next_account_event().await.unwrap();
        let ExternalAccountEvent::Snapshot(snapshot) = event else {
            panic!("expected normalized account snapshot event");
        };
        assert_eq!(snapshot.balances.len(), 1);
        assert_eq!(snapshot.balances[0].total.mantissa, 130);
        assert_eq!(snapshot.balances[0].total.scale, 1);
        source.disconnect_channel().await.unwrap();
        server.await.unwrap();
        time_server.join().unwrap();
    }
}
