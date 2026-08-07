//! Massive Stocks and Options WebSocket market streams.

use std::collections::BTreeMap;
use std::net::TcpStream;

use serde_json::{json, Value};
use tungstenite::{connect, Message, WebSocket};

use crate::application::{
    Connection, IntegrationError, MarketEvent, MarketEventKind, MarketStreamConnection,
    MarketSubscription, SubscriptionId,
};
use crate::domain::{
    AccessScope, ConnectionHealth, ConnectionIdentity, ConnectionLifecycle, ConnectionState,
    IntegrationCapability, ProductFamily, TransportKind,
};

type Socket = WebSocket<tungstenite::stream::MaybeTlsStream<TcpStream>>;

pub struct MassiveMarketStream {
    identity: ConnectionIdentity,
    state: ConnectionState,
    api_key: String,
    endpoint: String,
    product: ProductFamily,
    socket: Option<Socket>,
    subscriptions: BTreeMap<SubscriptionId, Vec<String>>,
    next_subscription_id: u64,
}

impl MassiveMarketStream {
    pub fn new(
        api_key: impl Into<String>,
        endpoint: impl Into<String>,
        product: ProductFamily,
    ) -> Result<Self, IntegrationError> {
        if !matches!(product, ProductFamily::Equity | ProductFamily::Options) {
            return Err(IntegrationError::InvalidRequest(
                "Massive market stream requires equity or options product".into(),
            ));
        }
        let endpoint = endpoint.into().trim_end_matches('/').to_string();
        if endpoint.is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "Massive market endpoint is required".into(),
            ));
        }
        let name = if product == ProductFamily::Options {
            "options"
        } else {
            "equity"
        };
        let identity = ConnectionIdentity::new(
            format!("market.massive.{name}.websocket"),
            "massive",
            Some(product),
            AccessScope::Public,
            TransportKind::WebSocket,
            IntegrationCapability::MarketStream,
        )
        .map_err(IntegrationError::InvalidRequest)?;
        Ok(Self {
            state: ConnectionState::new(identity.clone()),
            identity,
            api_key: api_key.into(),
            endpoint,
            product,
            socket: None,
            subscriptions: BTreeMap::new(),
            next_subscription_id: 1,
        })
    }

    fn send(&mut self, value: Value) -> Result<(), String> {
        self.socket
            .as_mut()
            .ok_or_else(|| "Massive market socket is not connected".to_string())?
            .send(Message::Text(value.to_string().into()))
            .map_err(|error| error.to_string())
    }
}

impl Connection for MassiveMarketStream {
    fn identity(&self) -> &ConnectionIdentity {
        &self.identity
    }
    fn state(&self) -> &ConnectionState {
        &self.state
    }
    fn start(&mut self) -> Result<(), String> {
        if self.state.lifecycle == ConnectionLifecycle::Ready {
            return Ok(());
        }
        if self.api_key.trim().is_empty() {
            return Err("Massive market stream API key is required".into());
        }
        self.state.lifecycle = ConnectionLifecycle::Starting;
        let (socket, _) = connect(self.endpoint.as_str()).map_err(|error| error.to_string())?;
        self.socket = Some(socket);
        self.send(json!({"action":"auth","params":self.api_key}))?;
        self.state.lifecycle = ConnectionLifecycle::Ready;
        self.state.connected_at_unix_nanos = Some(now_unix_nanos());
        Ok(())
    }
    fn stop(&mut self) -> Result<(), String> {
        if let Some(mut socket) = self.socket.take() {
            let _ = socket.close(None);
        }
        self.subscriptions.clear();
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }
    fn reconnect(&mut self) -> Result<(), String> {
        self.stop()?;
        self.state.reconnect_count += 1;
        self.start()
    }
    fn health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: self.state.lifecycle == ConnectionLifecycle::Ready,
            authenticated: self.state.authenticated,
            last_error: self.state.last_error.clone(),
        }
    }
}

impl MarketStreamConnection for MassiveMarketStream {
    fn subscribe(
        &mut self,
        request: MarketSubscription,
    ) -> Result<SubscriptionId, IntegrationError> {
        if self.state.lifecycle != ConnectionLifecycle::Ready {
            return Err(IntegrationError::NotReady);
        }
        let channel = if request.symbols.iter().any(|symbol| symbol.contains(':')) {
            "Q"
        } else {
            "Q"
        };
        let params = request
            .symbols
            .iter()
            .map(|symbol| format!("{channel}.{}", symbol.to_ascii_uppercase()))
            .collect::<Vec<_>>()
            .join(",");
        self.send(json!({"action":"subscribe","params":params}))
            .map_err(IntegrationError::Transport)?;
        let id = SubscriptionId(self.next_subscription_id);
        self.next_subscription_id += 1;
        self.subscriptions.insert(id, request.symbols);
        Ok(id)
    }
    fn unsubscribe(&mut self, subscription: SubscriptionId) -> Result<(), IntegrationError> {
        let symbols = self.subscriptions.remove(&subscription).ok_or_else(|| {
            IntegrationError::InvalidRequest("unknown Massive market subscription".into())
        })?;
        let params = symbols
            .iter()
            .map(|symbol| format!("Q.{}", symbol.to_ascii_uppercase()))
            .collect::<Vec<_>>()
            .join(",");
        self.send(json!({"action":"unsubscribe","params":params}))
            .map_err(IntegrationError::Transport)
    }
    fn next_event(&mut self) -> Result<Option<MarketEvent>, IntegrationError> {
        if self.state.lifecycle != ConnectionLifecycle::Ready {
            return Err(IntegrationError::NotReady);
        }
        loop {
            let message = self
                .socket
                .as_mut()
                .ok_or(IntegrationError::NotReady)?
                .read()
                .map_err(|error| IntegrationError::Transport(error.to_string()))?;
            let Message::Text(text) = message else {
                continue;
            };
            let values: Value = serde_json::from_str(text.as_ref())
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?;
            let rows = values.as_array().cloned().unwrap_or_else(|| vec![values]);
            for row in rows {
                if let Some(event) = normalize(&row, self.product)? {
                    return Ok(Some(event));
                }
            }
        }
    }
}

fn normalize(
    value: &Value,
    product: ProductFamily,
) -> Result<Option<MarketEvent>, IntegrationError> {
    let event = value.get("ev").and_then(Value::as_str).unwrap_or_default();
    if event == "status" || event == "status_update" {
        return Ok(None);
    }
    let symbol = value
        .get("sym")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_ascii_uppercase();
    if symbol.is_empty() {
        return Ok(None);
    }
    let timestamp = value
        .get("t")
        .and_then(Value::as_u64)
        .unwrap_or_else(now_unix_nanos);
    let timestamp = if timestamp < 10_000_000_000_000 {
        timestamp.saturating_mul(1_000_000)
    } else {
        timestamp
    };
    match event {
        "Q" => Ok(Some(MarketEvent {
            symbol,
            kind: MarketEventKind::Quote,
            price: text(value, "bp").or_else(|| text(value, "ap")),
            quantity: text(value, "bs"),
            ask_price: text(value, "ap"),
            ask_quantity: text(value, "as"),
            bids: Vec::new(),
            asks: Vec::new(),
            first_sequence: None,
            last_sequence: None,
            sequence: value.get("q").and_then(Value::as_u64),
            observed_at_unix_nanos: timestamp,
        })),
        "T" => Ok(Some(MarketEvent {
            symbol,
            kind: MarketEventKind::Trade,
            price: text(value, "p"),
            quantity: text(value, "s"),
            ask_price: None,
            ask_quantity: None,
            bids: Vec::new(),
            asks: Vec::new(),
            first_sequence: None,
            last_sequence: None,
            sequence: value.get("q").and_then(Value::as_u64),
            observed_at_unix_nanos: timestamp,
        })),
        _ if product == ProductFamily::Options => Ok(None),
        _ => Ok(None),
    }
}

fn text(value: &Value, key: &str) -> Option<String> {
    value.get(key).and_then(Value::as_str).map(str::to_owned)
}
fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos() as u64)
        .unwrap_or_default()
}
