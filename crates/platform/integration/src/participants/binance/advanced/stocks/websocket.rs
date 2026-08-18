//! Binance Stocks one-way URL-bound WebSocket streams.
//!
//! Unlike Spot streams, Stocks has no subscribe/unsubscribe RPC. A changed
//! desired set is confirmed by a successful replacement socket handshake.

use std::collections::BTreeMap;

use crate::participants::binance::BinanceWebSocketConfig;
use crate::services::participants::binance::{socket::SocketService, stream};
use crate::transport::websocket::InboundDispatcher;
use crate::{
    ConnectionDescriptor, ConnectionHealth, ConnectionHealthQuery, ConnectionLifecycleCommand,
    IntegrationError, MarketDataStream, MarketDelivery, MarketEvent, MarketSubscription,
    MarketSubscriptionCommand, MarketSubscriptionId, MarketSubscriptionOutcome,
    MarketSubscriptionRequest,
};

pub struct BinanceStocksWebSocketConnection {
    descriptor: ConnectionDescriptor,
    endpoint: String,
    event_capacity: usize,
    socket: Option<SocketService>,
    subscriptions: BTreeMap<MarketSubscriptionId, MarketSubscriptionRequest>,
    pending: InboundDispatcher<MarketEvent>,
    next_subscription_id: u64,
}

impl BinanceStocksWebSocketConnection {
    pub fn new(config: BinanceWebSocketConfig) -> Result<Self, IntegrationError> {
        if !(config.endpoint.starts_with("ws://") || config.endpoint.starts_with("wss://")) {
            return Err(IntegrationError::InvalidRequest(
                "Binance Stocks WebSocket endpoint must start with ws:// or wss://".into(),
            ));
        }
        if config.event_capacity == 0 {
            return Err(IntegrationError::InvalidRequest(
                "Binance Stocks WebSocket event capacity must be positive".into(),
            ));
        }
        Ok(Self {
            descriptor: config.descriptor("advanced.stocks.websocket")?,
            endpoint: config.endpoint.trim_end_matches('/').into(),
            event_capacity: config.event_capacity,
            socket: None,
            subscriptions: BTreeMap::new(),
            pending: InboundDispatcher::new(config.event_capacity)?,
            next_subscription_id: 1,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    async fn replace_socket(&mut self) -> Result<(), IntegrationError> {
        let streams = self
            .subscriptions
            .values()
            .flat_map(|request| request.feeds.iter())
            .map(|feed| stream::stream_name(feed, "stocks"))
            .collect::<Result<Vec<_>, _>>()?;
        if streams.is_empty() {
            if let Some(mut socket) = self.socket.take() {
                // The desired URL state is already empty. A close-frame failure must not
                // resurrect a logically removed subscription.
                let _ = socket.disconnect().await;
            }
            return Ok(());
        }
        let endpoint = if streams.len() == 1 {
            format!("{}/ws/{}", self.endpoint, streams[0])
        } else {
            format!("{}/stream?streams={}", self.endpoint, streams.join("/"))
        };
        let mut replacement =
            SocketService::new(self.descriptor.clone(), endpoint, self.event_capacity)?;
        replacement.connect().await?;
        if let Some(mut previous) = self.socket.replace(replacement) {
            // The replacement handshake is the provider confirmation point. Closing the
            // superseded socket is best-effort cleanup, not the subscription outcome.
            let _ = previous.disconnect().await;
        }
        Ok(())
    }
}

impl ConnectionHealthQuery for BinanceStocksWebSocketConnection {
    fn connection_health(&mut self) -> ConnectionHealth {
        self.socket.as_mut().map_or(
            ConnectionHealth {
                lifecycle: crate::ConnectionLifecycle::Created,
                healthy: false,
                authenticated: false,
                last_error: None,
            },
            SocketService::health,
        )
    }
}

impl ConnectionLifecycleCommand for BinanceStocksWebSocketConnection {
    async fn connect(&mut self) -> Result<(), IntegrationError> {
        if self.subscriptions.is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "Binance Stocks requires a subscription before opening its URL-bound stream".into(),
            ));
        }
        self.replace_socket().await
    }
    async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        self.pending.clear();
        if let Some(mut socket) = self.socket.take() {
            socket.disconnect().await?;
        }
        Ok(())
    }
    async fn reconnect(&mut self) -> Result<(), IntegrationError> {
        self.replace_socket().await
    }
}

impl MarketSubscriptionCommand for BinanceStocksWebSocketConnection {
    async fn subscribe(
        &mut self,
        request: MarketSubscriptionRequest,
    ) -> Result<MarketSubscriptionOutcome<MarketSubscription>, IntegrationError> {
        if request.feeds.is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "empty Binance Stocks subscription".into(),
            ));
        }
        for feed in &request.feeds {
            stream::stream_name(feed, "stocks")?;
        }
        let id = MarketSubscriptionId(self.next_subscription_id);
        self.next_subscription_id = self.next_subscription_id.saturating_add(1);
        self.subscriptions.insert(id, request.clone());
        if let Err(error) = self.replace_socket().await {
            self.subscriptions.remove(&id);
            return Err(error);
        }
        Ok(MarketSubscriptionOutcome::Confirmed(MarketSubscription {
            id,
            feeds: request.feeds,
            delivery: MarketDelivery::Push,
        }))
    }

    async fn unsubscribe(
        &mut self,
        subscription: MarketSubscriptionId,
    ) -> Result<MarketSubscriptionOutcome<()>, IntegrationError> {
        let removed = self.subscriptions.remove(&subscription).ok_or_else(|| {
            IntegrationError::InvalidRequest("unknown Binance Stocks subscription".into())
        })?;
        if let Err(error) = self.replace_socket().await {
            self.subscriptions.insert(subscription, removed);
            return Err(error);
        }
        Ok(MarketSubscriptionOutcome::Confirmed(()))
    }
}

impl MarketDataStream for BinanceStocksWebSocketConnection {
    async fn next(&mut self) -> Result<MarketEvent, IntegrationError> {
        if let Some(event) = self.pending.pop() {
            return Ok(event);
        }
        loop {
            let message = self
                .socket
                .as_mut()
                .ok_or(IntegrationError::NotReady)?
                .next()
                .await?;
            let tokio_tungstenite::tungstenite::Message::Text(text) = message else {
                continue;
            };
            let value = serde_json::from_str(&text)
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?;
            let mut events = stream::normalize(&value)?;
            let Some(first) = events.pop_front() else {
                continue;
            };
            self.pending.extend(events)?;
            return Ok(first);
        }
    }
}

// The configured endpoint must be the Stocks base URL with `{listenKey}` in
// its stream path, for example `/ws/{listenKey}@orderReport`.
user_websocket_connection!(
    BinanceStocksUserWebSocketConnection,
    "advanced.stocks.user.websocket",
    "/sapi/v1/equity/listenKey"
);
