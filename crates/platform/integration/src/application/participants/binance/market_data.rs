//! Binance public market-data projections.

use std::collections::{BTreeMap, VecDeque};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::application::{
    AsyncHistoricalMarketDataConnection, AsyncMarketEventSource, HistoricalMarketDataConnection,
    HistoricalMarketRequest, IntegrationError, MarketEvent, MarketStreamCapabilities,
    MarketSubscription, SubscriptionId,
};
use crate::domain::{ConnectionHealth, ConnectionState};
use crate::services::participants::binance as service;
use crate::services::transport::http::AsyncPublicHttpClient;
use crate::services::transport::websocket::{AsyncSocketEvent, AsyncTokioSocket};
use kairos_domain_types::Sequence;
use serde_json::{json, Value};
use tokio_tungstenite::tungstenite::Message;

/// Async Binance live market projection. Every variant is polled directly by
/// the caller's Tokio runtime; REST snapshots use the separate stateless
/// `BinanceAsyncMarketSnapshot` capability.
pub struct BinanceAsyncMarket {
    inner: BinanceMarketInner,
    health: ConnectionHealth,
}

pub struct BinanceSpotHistoricalMarket {
    inner: service::spot::market_stream::BinanceSpotAsyncHistoricalReader,
}

impl AsyncHistoricalMarketDataConnection for BinanceSpotHistoricalMarket {
    fn capabilities(&self) -> MarketStreamCapabilities {
        self.inner.capabilities()
    }

    async fn fetch(
        &mut self,
        request: &HistoricalMarketRequest,
    ) -> Result<Vec<MarketEvent>, IntegrationError> {
        self.inner.fetch(request).await
    }
}

enum BinanceMarketInner {
    SpotWebSocket(NativeBinanceSpotMarket),
    DerivativesWebSocket(NativeBinanceDerivativesMarket),
}

impl BinanceAsyncMarket {
    fn spot_websocket(endpoint: impl Into<String>) -> Result<Self, IntegrationError> {
        let native = NativeBinanceSpotMarket::new(endpoint)?;
        let health = native.state.health();
        Ok(Self {
            inner: BinanceMarketInner::SpotWebSocket(native),
            health,
        })
    }

    fn options_websocket(endpoint: impl Into<String>) -> Result<Self, IntegrationError> {
        Self::derivatives_websocket(super::ConnectionDomain::Options, endpoint)
    }

    fn derivatives_websocket(
        domain: super::ConnectionDomain,
        endpoint: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        let native = NativeBinanceDerivativesMarket::new(domain, endpoint)?;
        let health = native.state.health();
        Ok(Self {
            inner: BinanceMarketInner::DerivativesWebSocket(native),
            health,
        })
    }
}

impl AsyncMarketEventSource for BinanceAsyncMarket {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        let result = match &mut self.inner {
            BinanceMarketInner::SpotWebSocket(connection) => connection.connect().await,
            BinanceMarketInner::DerivativesWebSocket(connection) => connection.connect().await,
        };
        self.refresh_health();
        result
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        let result = match &mut self.inner {
            BinanceMarketInner::SpotWebSocket(connection) => connection.disconnect().await,
            BinanceMarketInner::DerivativesWebSocket(connection) => connection.disconnect().await,
        };
        self.refresh_health();
        result
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        let result = match &mut self.inner {
            BinanceMarketInner::SpotWebSocket(connection) => connection.reconnect().await,
            BinanceMarketInner::DerivativesWebSocket(connection) => connection.reconnect().await,
        };
        self.refresh_health();
        result
    }

    fn channel_health(&self) -> ConnectionHealth {
        self.health.clone()
    }

    async fn subscribe(
        &mut self,
        request: MarketSubscription,
    ) -> Result<SubscriptionId, IntegrationError> {
        let result = match &mut self.inner {
            BinanceMarketInner::SpotWebSocket(connection) => connection.subscribe(request).await,
            BinanceMarketInner::DerivativesWebSocket(connection) => {
                connection.subscribe(request).await
            }
        };
        self.refresh_health();
        result
    }

    async fn unsubscribe(&mut self, subscription: SubscriptionId) -> Result<(), IntegrationError> {
        let result = match &mut self.inner {
            BinanceMarketInner::SpotWebSocket(connection) => {
                connection.unsubscribe(subscription).await
            }
            BinanceMarketInner::DerivativesWebSocket(connection) => {
                connection.unsubscribe(subscription).await
            }
        };
        self.refresh_health();
        result
    }

    async fn next_market_event(&mut self) -> Result<MarketEvent, IntegrationError> {
        let result = match &mut self.inner {
            BinanceMarketInner::SpotWebSocket(connection) => connection.next_event().await,
            BinanceMarketInner::DerivativesWebSocket(connection) => connection.next_event().await,
        };
        self.refresh_health();
        result
    }
}

impl BinanceAsyncMarket {
    fn refresh_health(&mut self) {
        self.health = match &self.inner {
            BinanceMarketInner::SpotWebSocket(connection) => connection.state.health(),
            BinanceMarketInner::DerivativesWebSocket(connection) => connection.state.health(),
        }
    }
}

struct NativeBinanceSpotMarket {
    state: ConnectionState,
    endpoint: String,
    rest_endpoint: String,
    http: AsyncPublicHttpClient,
    socket: Option<AsyncTokioSocket>,
    pending_events: VecDeque<MarketEvent>,
    snapshot_sequences: BTreeMap<String, u64>,
    subscriptions: BTreeMap<SubscriptionId, Vec<String>>,
    next_subscription_id: u64,
}

impl NativeBinanceSpotMarket {
    fn new(endpoint: impl Into<String>) -> Result<Self, IntegrationError> {
        let endpoint = endpoint.into();
        if !(endpoint.starts_with("ws://") || endpoint.starts_with("wss://")) {
            return Err(IntegrationError::InvalidRequest(
                "Binance Spot WebSocket endpoint must start with ws:// or wss://".into(),
            ));
        }
        let descriptor = service::descriptor("market.binance.spot.websocket.async", "spot")
            .map_err(IntegrationError::InvalidRequest)?;
        Ok(Self {
            state: ConnectionState::new(descriptor),
            endpoint,
            rest_endpoint: "https://api.binance.com".into(),
            http: AsyncPublicHttpClient::new("kairos-integration/binance-spot-async")
                .map_err(|error| IntegrationError::Transport(error.to_string()))?,
            socket: None,
            pending_events: VecDeque::new(),
            snapshot_sequences: BTreeMap::new(),
            subscriptions: BTreeMap::new(),
            next_subscription_id: 1,
        })
    }

    async fn connect(&mut self) -> Result<(), IntegrationError> {
        if self.socket.is_some() {
            return Ok(());
        }
        self.state.lifecycle = crate::domain::ConnectionLifecycle::Starting;
        let socket = AsyncTokioSocket::connect(&self.endpoint, 4_096)
            .await
            .map_err(|error| {
                self.state.mark_failed(error.clone());
                IntegrationError::Transport(error)
            })?;
        self.socket = Some(socket);
        let subscriptions = self.subscriptions.values().cloned().collect::<Vec<_>>();
        for symbols in subscriptions {
            self.send_subscription("SUBSCRIBE", &symbols).await?;
            self.queue_snapshots(&symbols).await?;
        }
        self.state.mark_ready(false);
        self.state.connected_at_unix_nanos = Some(now_unix_nanos().into());
        Ok(())
    }

    async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        if let Some(mut socket) = self.socket.take() {
            socket.close().await;
        }
        self.pending_events.clear();
        self.snapshot_sequences.clear();
        self.state.mark_stopped();
        Ok(())
    }

    async fn reconnect(&mut self) -> Result<(), IntegrationError> {
        self.disconnect().await?;
        self.connect().await?;
        self.state.mark_reconnected(false);
        Ok(())
    }

    async fn subscribe(
        &mut self,
        request: MarketSubscription,
    ) -> Result<SubscriptionId, IntegrationError> {
        if self.socket.is_none() {
            return Err(IntegrationError::NotReady);
        }
        let subscription = SubscriptionId(self.next_subscription_id);
        self.next_subscription_id = self.next_subscription_id.saturating_add(1);
        self.send_subscription("SUBSCRIBE", &request.symbols)
            .await?;
        self.queue_snapshots(&request.symbols).await?;
        self.subscriptions.insert(subscription, request.symbols);
        Ok(subscription)
    }

    async fn unsubscribe(&mut self, subscription: SubscriptionId) -> Result<(), IntegrationError> {
        let symbols = self.subscriptions.remove(&subscription).ok_or_else(|| {
            IntegrationError::InvalidRequest("unknown Binance subscription".into())
        })?;
        self.send_subscription("UNSUBSCRIBE", &symbols).await
    }

    async fn send_subscription(
        &self,
        method: &str,
        symbols: &[String],
    ) -> Result<(), IntegrationError> {
        let params = symbols
            .iter()
            .flat_map(|symbol| {
                let symbol = symbol.to_ascii_lowercase();
                [
                    format!("{symbol}@trade"),
                    format!("{symbol}@bookTicker"),
                    format!("{symbol}@depth@100ms"),
                    format!("{symbol}@kline_1m"),
                ]
            })
            .collect::<Vec<_>>();
        self.socket
            .as_ref()
            .ok_or(IntegrationError::NotReady)?
            .send_text(
                json!({
                    "method": method,
                    "params": params,
                    "id": self.next_subscription_id,
                })
                .to_string(),
            )
            .await
            .map_err(IntegrationError::Transport)
    }

    async fn queue_snapshots(&mut self, symbols: &[String]) -> Result<(), IntegrationError> {
        for symbol in symbols {
            let endpoint = format!("{}/api/v3/depth", self.rest_endpoint.trim_end_matches('/'));
            let response = self
                .http
                .get_json_response_with_headers_and_query(
                    &endpoint,
                    &[("symbol", symbol.clone()), ("limit", "1000".into())],
                    &[],
                )
                .await
                .map_err(|error| IntegrationError::Transport(error.to_string()))?;
            let sequence = response
                .body
                .get("lastUpdateId")
                .and_then(Value::as_u64)
                .ok_or_else(|| {
                    IntegrationError::InvalidPayload(
                        "Binance depth snapshot has no lastUpdateId".into(),
                    )
                })?;
            let symbol = symbol.to_ascii_uppercase();
            self.snapshot_sequences.insert(symbol.clone(), sequence);
            self.pending_events.push_back(
                service::spot::websocket::normalize_depth_snapshot(
                    &symbol,
                    &response.body,
                    sequence,
                )
                .map_err(IntegrationError::InvalidPayload)?,
            );
        }
        Ok(())
    }

    async fn next_event(&mut self) -> Result<MarketEvent, IntegrationError> {
        if let Some(event) = self.pending_events.pop_front() {
            return Ok(event);
        }
        loop {
            match self
                .socket
                .as_mut()
                .ok_or(IntegrationError::NotReady)?
                .next_event()
                .await
            {
                AsyncSocketEvent::Message(Message::Text(payload)) => {
                    let Some(event) = service::spot::websocket::normalize_market_message(&payload)
                        .map_err(IntegrationError::InvalidPayload)?
                    else {
                        continue;
                    };
                    if let Some(event) = self.align_depth_event(event)? {
                        return Ok(event);
                    }
                }
                AsyncSocketEvent::Message(Message::Ping(payload)) => {
                    self.socket
                        .as_ref()
                        .ok_or(IntegrationError::NotReady)?
                        .send_pong(payload.to_vec())
                        .await
                        .map_err(IntegrationError::Transport)?;
                }
                AsyncSocketEvent::Message(Message::Close(_)) => {
                    self.state.mark_failed("Binance WebSocket closed");
                    return Err(IntegrationError::Transport(
                        "Binance WebSocket closed".into(),
                    ));
                }
                AsyncSocketEvent::Message(_) => {}
                AsyncSocketEvent::Error(error) => {
                    self.state.mark_failed(error.clone());
                    return Err(IntegrationError::Transport(error));
                }
                AsyncSocketEvent::Backpressure => {
                    self.state
                        .mark_failed("Binance WebSocket event queue overflowed");
                    return Err(IntegrationError::Backpressure(
                        "Binance WebSocket event queue overflowed".into(),
                    ));
                }
            }
        }
    }

    fn align_depth_event(
        &mut self,
        mut event: MarketEvent,
    ) -> Result<Option<MarketEvent>, IntegrationError> {
        if event.kind != crate::application::MarketEventKind::BookDelta {
            return Ok(Some(event));
        }
        let symbol = event.symbol.to_string();
        let Some(snapshot_sequence) = self.snapshot_sequences.get(&symbol).copied() else {
            return Ok(Some(event));
        };
        let last = event.last_sequence.ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance depth event has no final sequence".into())
        })?;
        let first = event.first_sequence.ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance depth event has no first sequence".into())
        })?;
        if last.get() <= snapshot_sequence {
            return Ok(None);
        }
        let expected = snapshot_sequence.saturating_add(1);
        if first.get() > expected {
            self.snapshot_sequences.remove(&symbol);
            return Err(IntegrationError::ResyncRequired(format!(
                "Binance depth gap for {symbol} after snapshot: expected {expected}, got {first}"
            )));
        }
        event.first_sequence = Some(Sequence::new(expected));
        self.snapshot_sequences.remove(&symbol);
        Ok(Some(event))
    }
}

struct NativeBinanceDerivativesMarket {
    state: ConnectionState,
    domain: super::ConnectionDomain,
    endpoint: String,
    socket: Option<AsyncTokioSocket>,
    subscriptions: BTreeMap<SubscriptionId, Vec<String>>,
    next_subscription_id: u64,
    next_request_id: u64,
}

impl NativeBinanceDerivativesMarket {
    fn new(
        domain: super::ConnectionDomain,
        endpoint: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            domain,
            super::ConnectionDomain::UsdMFutures
                | super::ConnectionDomain::CoinMFutures
                | super::ConnectionDomain::Options
        ) {
            return Err(IntegrationError::InvalidRequest(
                "Binance derivatives WebSocket requires a futures or options product".into(),
            ));
        }
        let endpoint = endpoint.into().trim_end_matches('/').to_owned();
        if !(endpoint.starts_with("ws://") || endpoint.starts_with("wss://")) {
            return Err(IntegrationError::InvalidRequest(
                "Binance derivatives WebSocket endpoint must start with ws:// or wss://".into(),
            ));
        }
        let descriptor = service::descriptor(
            format!("market.binance.{}.websocket.async", domain.as_str()),
            domain.as_str(),
        )
        .map_err(IntegrationError::InvalidRequest)?;
        Ok(Self {
            state: ConnectionState::new(descriptor),
            domain,
            endpoint,
            socket: None,
            subscriptions: BTreeMap::new(),
            next_subscription_id: 1,
            next_request_id: 1,
        })
    }

    async fn connect(&mut self) -> Result<(), IntegrationError> {
        if self.socket.is_some() {
            return Ok(());
        }
        self.state.lifecycle = crate::domain::ConnectionLifecycle::Starting;
        let socket = AsyncTokioSocket::connect(&self.endpoint, 4_096)
            .await
            .map_err(|error| {
                self.state.mark_failed(error.clone());
                IntegrationError::Transport(error)
            })?;
        self.socket = Some(socket);
        let subscriptions = self.subscriptions.values().cloned().collect::<Vec<_>>();
        for symbols in subscriptions {
            self.send_subscription("SUBSCRIBE", &symbols).await?;
        }
        self.state.mark_ready(false);
        self.state.connected_at_unix_nanos = Some(now_unix_nanos().into());
        Ok(())
    }

    async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        if let Some(mut socket) = self.socket.take() {
            socket.close().await;
        }
        self.state.mark_stopped();
        Ok(())
    }

    async fn reconnect(&mut self) -> Result<(), IntegrationError> {
        self.disconnect().await?;
        self.connect().await?;
        self.state.mark_reconnected(false);
        Ok(())
    }

    async fn subscribe(
        &mut self,
        request: MarketSubscription,
    ) -> Result<SubscriptionId, IntegrationError> {
        if self.socket.is_none() {
            return Err(IntegrationError::NotReady);
        }
        let id = SubscriptionId(self.next_subscription_id);
        self.next_subscription_id = self.next_subscription_id.saturating_add(1);
        self.send_subscription("SUBSCRIBE", &request.symbols)
            .await?;
        self.subscriptions.insert(id, request.symbols);
        Ok(id)
    }

    async fn unsubscribe(&mut self, id: SubscriptionId) -> Result<(), IntegrationError> {
        let symbols = self.subscriptions.remove(&id).ok_or_else(|| {
            IntegrationError::InvalidRequest("unknown Binance derivatives subscription".into())
        })?;
        self.send_subscription("UNSUBSCRIBE", &symbols).await
    }

    async fn send_subscription(
        &mut self,
        method: &str,
        symbols: &[String],
    ) -> Result<(), IntegrationError> {
        let params = symbols
            .iter()
            .map(|symbol| format!("{}@bookTicker", symbol.to_ascii_lowercase()))
            .collect::<Vec<_>>();
        let request_id = self.next_request_id;
        self.next_request_id = self.next_request_id.saturating_add(1);
        self.socket
            .as_ref()
            .ok_or(IntegrationError::NotReady)?
            .send_text(json!({"method": method, "params": params, "id": request_id}).to_string())
            .await
            .map_err(IntegrationError::Transport)
    }

    async fn next_event(&mut self) -> Result<MarketEvent, IntegrationError> {
        loop {
            let socket = self.socket.as_mut().ok_or(IntegrationError::NotReady)?;
            match socket.next_event().await {
                AsyncSocketEvent::Message(Message::Text(payload)) => {
                    if let Some(event) =
                        service::market_data::derivatives::normalize_derivatives_market_message(
                            payload.as_ref(),
                        )
                        .map_err(IntegrationError::InvalidPayload)?
                    {
                        return Ok(event);
                    }
                }
                AsyncSocketEvent::Message(Message::Ping(payload)) => socket
                    .send_pong(payload.to_vec())
                    .await
                    .map_err(IntegrationError::Transport)?,
                AsyncSocketEvent::Message(Message::Close(frame)) => {
                    let error = IntegrationError::Transport(format!(
                        "Binance {} WebSocket closed: {frame:?}",
                        self.domain.as_str()
                    ));
                    self.state.mark_failed(error.to_string());
                    return Err(error);
                }
                AsyncSocketEvent::Message(_) => {}
                AsyncSocketEvent::Error(message) => {
                    self.state.mark_failed(message.clone());
                    return Err(IntegrationError::Transport(message));
                }
                AsyncSocketEvent::Backpressure => {
                    self.state
                        .mark_failed("Binance derivatives WebSocket event queue overflowed");
                    return Err(IntegrationError::Backpressure(
                        "Binance derivatives WebSocket event queue overflowed".into(),
                    ));
                }
            }
        }
    }
}

fn now_unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos().min(u128::from(u64::MAX)) as u64)
        .unwrap_or_default()
}

pub fn spot_websocket_market(
    endpoint: impl Into<String>,
) -> Result<BinanceAsyncMarket, IntegrationError> {
    BinanceAsyncMarket::spot_websocket(endpoint)
}

pub fn options_websocket_market(
    endpoint: impl Into<String>,
) -> Result<BinanceAsyncMarket, IntegrationError> {
    BinanceAsyncMarket::options_websocket(endpoint)
}

pub fn futures_websocket_market(
    product: super::ConnectionDomain,
    endpoint: impl Into<String>,
) -> Result<BinanceAsyncMarket, IntegrationError> {
    if !matches!(
        product,
        super::ConnectionDomain::UsdMFutures | super::ConnectionDomain::CoinMFutures
    ) {
        return Err(IntegrationError::InvalidRequest(
            "Binance Futures WebSocket requires USD-M or COIN-M product".into(),
        ));
    }
    BinanceAsyncMarket::derivatives_websocket(product, endpoint)
}

pub fn spot_historical_market(
    endpoint: impl Into<String>,
) -> Result<BinanceSpotHistoricalMarket, IntegrationError> {
    service::spot::market_stream::BinanceSpotAsyncHistoricalReader::new(endpoint)
        .map(|inner| BinanceSpotHistoricalMarket { inner })
}

pub fn blocking_spot_historical_market(
    endpoint: impl Into<String>,
) -> Result<Box<dyn HistoricalMarketDataConnection>, IntegrationError> {
    service::spot::market_stream::BinanceSpotSnapshotReader::new(endpoint)
        .map(|value| Box::new(value) as Box<dyn HistoricalMarketDataConnection>)
}

#[cfg(test)]
mod native_tests {
    use futures_util::{SinkExt, StreamExt};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;
    use tokio_tungstenite::accept_async;

    use super::{
        futures_websocket_market, options_websocket_market, spot_websocket_market,
        BinanceMarketInner,
    };
    use crate::application::{AsyncMarketEventSource, MarketEventKind, MarketSubscription};

    #[tokio::test(flavor = "current_thread")]
    async fn spot_websocket_uses_native_async_socket_and_snapshot_barrier() {
        let websocket_listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let websocket_address = websocket_listener.local_addr().unwrap();
        let websocket_server = tokio::spawn(async move {
            let (stream, _) = websocket_listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            let message = socket.next().await.unwrap().unwrap();
            assert!(message.into_text().unwrap().contains("btcusdt@depth@100ms"));
            let _ = socket.next().await;
        });

        let http_listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let http_address = http_listener.local_addr().unwrap();
        let http_server = tokio::spawn(async move {
            let (mut stream, _) = http_listener.accept().await.unwrap();
            let mut request = vec![0_u8; 2_048];
            let read = stream.read(&mut request).await.unwrap();
            let request = String::from_utf8_lossy(&request[..read]);
            assert!(request.contains("symbol=BTCUSDT"), "{request}");
            let body = r#"{"lastUpdateId":42,"bids":[["100","2"]],"asks":[["101","3"]]}"#;
            stream
                .write_all(
                    format!(
                        "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                        body.len()
                    )
                    .as_bytes(),
                )
                .await
                .unwrap();
        });

        let mut market = spot_websocket_market(format!("ws://{websocket_address}")).unwrap();
        let BinanceMarketInner::SpotWebSocket(native) = &mut market.inner else {
            panic!("spot WebSocket must use the native async transport");
        };
        native.rest_endpoint = format!("http://{http_address}");
        market.connect_channel().await.unwrap();
        market
            .subscribe(MarketSubscription::new(["BTCUSDT"]).unwrap())
            .await
            .unwrap();
        let snapshot = tokio::time::timeout(
            std::time::Duration::from_secs(1),
            market.next_market_event(),
        )
        .await
        .unwrap()
        .unwrap();
        assert_eq!(snapshot.kind, MarketEventKind::BookSnapshot);
        assert_eq!(snapshot.last_sequence.map(|value| value.get()), Some(42));
        market.disconnect_channel().await.unwrap();
        http_server.await.unwrap();
        websocket_server.abort();
        let _ = websocket_server.await;
    }

    #[tokio::test(flavor = "current_thread")]
    async fn options_websocket_uses_native_async_socket() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            let request = socket.next().await.unwrap().unwrap().into_text().unwrap();
            assert!(request.contains("btc-260925-145000-c@bookTicker"));
            socket
                .send(tokio_tungstenite::tungstenite::Message::Text(
                    r#"{"e":"bookTicker","E":1,"s":"BTC-260925-145000-C","b":"10","B":"2","a":"11","A":"3","u":9}"#
                        .into(),
                ))
                .await
                .unwrap();
        });

        let mut market = options_websocket_market(format!("ws://{address}")).unwrap();
        assert!(matches!(
            &market.inner,
            BinanceMarketInner::DerivativesWebSocket(_)
        ));
        market.connect_channel().await.unwrap();
        market
            .subscribe(MarketSubscription::new(["BTC-260925-145000-C"]).unwrap())
            .await
            .unwrap();
        let event = tokio::time::timeout(
            std::time::Duration::from_secs(1),
            market.next_market_event(),
        )
        .await
        .unwrap()
        .unwrap();
        assert_eq!(event.kind, MarketEventKind::Quote);
        assert_eq!(event.price.unwrap().to_string(), "10");
        assert_eq!(event.ask_price.unwrap().to_string(), "11");
        market.disconnect_channel().await.unwrap();
        server.await.unwrap();
    }

    #[tokio::test(flavor = "current_thread")]
    async fn futures_websocket_uses_product_native_async_socket() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            let request = socket.next().await.unwrap().unwrap().into_text().unwrap();
            assert!(request.contains("btcusdt@bookTicker"), "{request}");
            socket
                .send(tokio_tungstenite::tungstenite::Message::Text(
                    r#"{"e":"bookTicker","E":2,"s":"BTCUSDT","b":"100","B":"2","a":"101","A":"3","u":10}"#
                        .into(),
                ))
                .await
                .unwrap();
        });

        let mut market = futures_websocket_market(
            super::super::ConnectionDomain::UsdMFutures,
            format!("ws://{address}"),
        )
        .unwrap();
        market.connect_channel().await.unwrap();
        market
            .subscribe(MarketSubscription::new(["BTCUSDT"]).unwrap())
            .await
            .unwrap();
        let event = tokio::time::timeout(
            std::time::Duration::from_secs(1),
            market.next_market_event(),
        )
        .await
        .unwrap()
        .unwrap();
        assert_eq!(event.kind, MarketEventKind::Quote);
        assert_eq!(event.symbol.as_str(), "BTCUSDT");
        assert_eq!(event.sequence.map(|value| value.get()), Some(10));
        market.disconnect_channel().await.unwrap();
        server.await.unwrap();
    }
}
