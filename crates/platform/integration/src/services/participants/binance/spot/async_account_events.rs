//! Async Binance Spot account/balance/order/fill event capability.

use crate::application::capabilities::account_facts::{
    ExternalAccountEvent, ExternalAccountEventEnvelope,
};
use crate::application::{AsyncAccountEventSource, ExternalEventEnvelope, IntegrationError};
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

    async fn next_account_event(
        &mut self,
    ) -> Result<ExternalAccountEventEnvelope, IntegrationError> {
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
            if let Some(payload) = parse_user_event_value(&self.segment_key, self.product, event)
                .map_err(IntegrationError::InvalidPayload)?
            {
                let observed_at_unix_nanos = account_event_time(&payload);
                return Ok(ExternalEventEnvelope {
                    participant: crate::domain::ParticipantRef::new(
                        crate::domain::ParticipantKind::Exchange,
                        "binance",
                    )
                    .expect("static Binance participant is valid"),
                    binding_id: self.channel.binding_id().to_owned(),
                    channel_id: "binance-spot-user-data".into(),
                    channel_epoch: self.channel.channel_epoch(),
                    provider_event_id: provider_event_id(&payload),
                    provider_sequence: None,
                    observed_at_unix_nanos,
                    received_at_unix_nanos: now_unix_nanos(),
                    payload,
                });
            }
        }
    }
}

fn account_event_time(event: &ExternalAccountEvent) -> kairos_primitives::UnixNanos {
    match event {
        ExternalAccountEvent::Snapshot(snapshot) => snapshot.observed_at_unix_nanos,
        ExternalAccountEvent::Order(order) => order.occurred_at_unix_nanos,
        ExternalAccountEvent::Fill(fill) => fill.occurred_at_unix_nanos,
        ExternalAccountEvent::Batch(events) => events
            .iter()
            .map(account_event_time)
            .max()
            .unwrap_or_else(|| kairos_primitives::UnixNanos::new(0)),
    }
}

fn provider_event_id(event: &ExternalAccountEvent) -> Option<String> {
    match event {
        ExternalAccountEvent::Fill(fill) => Some(format!("binance:fill:{}", fill.fill_id)),
        ExternalAccountEvent::Order(order) => Some(format!(
            "binance:order:{}:{}",
            order.order_id,
            order.occurred_at_unix_nanos.get()
        )),
        ExternalAccountEvent::Snapshot(_) | ExternalAccountEvent::Batch(_) => None,
    }
}

#[cfg(test)]
mod live_tests {
    use crate::application::AsyncAccountEventSource;

    use super::{BinanceSpotAccountClient, BinanceSpotAsyncAccountEventSource};

    #[tokio::test(flavor = "current_thread")]
    #[ignore = "requires explicit Binance credentials and live network access"]
    async fn live_hmac_user_data_subscription_connects() {
        let api_key = std::env::var("BINANCE_API_KEY").expect("BINANCE_API_KEY is required");
        let secret = std::env::var("BINANCE_API_SECRET").expect("BINANCE_API_SECRET is required");
        let client = BinanceSpotAccountClient::new(api_key, secret, "https://api.binance.com")
            .expect("live Binance client should be valid");
        let signed_timestamp = client
            .test_signed_timestamp_async()
            .await
            .expect("live Binance clock synchronization should succeed");
        let local_timestamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("local clock should be after Unix epoch")
            .as_millis() as u64;
        let signed_clock_delta = signed_timestamp as i128 - local_timestamp as i128;
        eprintln!("Binance signed clock delta from local time: {signed_clock_delta} ms");
        assert!(signed_clock_delta.abs() < 5_000);
        let mut source = BinanceSpotAsyncAccountEventSource::from_client_with_capacity(
            "account.binance.live-contract",
            "spot",
            client,
            "wss://ws-api.binance.com:443/ws-api/v3",
            16,
        )
        .expect("live Binance account event source should be valid");

        source
            .connect_channel()
            .await
            .expect("live Binance user-data subscription should connect");
        assert!(source.channel_health().authenticated);
        source
            .disconnect_channel()
            .await
            .expect("live Binance user-data subscription should disconnect");
    }
}

fn now_unix_nanos() -> kairos_primitives::UnixNanos {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u64::MAX as u128) as u64;
    kairos_primitives::UnixNanos::new(nanos)
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
        assert_eq!(event.binding_id, "account.binance.spot");
        assert_eq!(event.channel_id, "binance-spot-user-data");
        assert_eq!(event.channel_epoch, 1);
        assert!(event.received_at_unix_nanos >= event.observed_at_unix_nanos);
        let ExternalAccountEvent::Snapshot(snapshot) = event.payload else {
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
