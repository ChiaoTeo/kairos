//! Explicit synchronous compatibility projections for OKX capabilities.

use super::*;
use crate::services::transport::websocket::{SocketEvent, TokioSocket};

pub struct OkxTradingAccountRead {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) client: OkxAccountClient,
    pub(super) quota: Option<Arc<SharedFixedWindowQuota>>,
}

pub struct OkxTradingCredentialInspection {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) instrument_type: InstrumentType,
    pub(super) client: OkxAccountClient,
    pub(super) quota: Option<Arc<SharedFixedWindowQuota>>,
}

pub struct OkxTradingAccountMarketProfile {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) client: OkxAccountClient,
    pub(super) quota: Option<Arc<SharedFixedWindowQuota>>,
}

pub struct OkxTradingAccountEvents {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) instrument_type: InstrumentType,
    pub(super) segment_key: String,
    pub(super) websocket_url: String,
    pub(super) event_queue_capacity: usize,
    pub(super) api_key: SecretString,
    pub(super) secret: SecretString,
    pub(super) passphrase: SecretString,
    pub(super) socket: Option<TokioSocket>,
    pub(super) lifecycle: ConnectionLifecycle,
    pub(super) last_error: Option<String>,
    pub(super) reconnect_count: u64,
}

pub struct OkxTradingOrderEntry {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) trading_mode: TradingMode,
    pub(super) client: OkxAccountClient,
    pub(super) quota: Option<Arc<SharedFixedWindowQuota>>,
}

pub struct OkxTradingOrderQuery {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) client: OkxAccountClient,
    pub(super) quota: Option<Arc<SharedFixedWindowQuota>>,
}

pub struct OkxInstrumentCatalog {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) instrument_type: InstrumentType,
    pub(super) base_url: String,
    pub(super) http: PublicHttpClient,
}

fn reject_async_runtime() -> Result<(), IntegrationError> {
    if tokio::runtime::Handle::try_current().is_ok() {
        return Err(IntegrationError::InvalidRequest(
            "kairos_integration::blocking cannot run on a Tokio runtime worker; use the default async capability or move the blocking call to a dedicated thread".into(),
        ));
    }
    Ok(())
}

impl OkxInstrumentCatalog {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl InstrumentCatalogConnection for OkxInstrumentCatalog {
    fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        reject_async_runtime()?;
        let payload = self
            .http
            .get_json_with_query(
                &format!("{}/api/v5/public/instruments", self.base_url),
                &[("instType", self.instrument_type.api_value().into())],
            )
            .map_err(map_exchange_error)?;
        normalize_instrument_catalog(self.instrument_type, &payload)
    }
}

impl OkxTradingAccountRead {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub fn fetch_account(
        &mut self,
        segment: &crate::application::capabilities::account_facts::ExternalAccountSegment,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountSnapshot,
        IntegrationError,
    > {
        reject_async_runtime()?;
        acquire(self.quota.as_deref(), 3)?;
        let balance = self.client.balance().map_err(map_exchange_error)?;
        let positions = self.client.positions().map_err(map_exchange_error)?;
        let orders = self.client.pending_orders().map_err(map_exchange_error)?;
        normalize_account(segment, &balance, &positions, &orders)
            .map_err(IntegrationError::InvalidPayload)
    }
}

impl OkxTradingCredentialInspection {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub fn inspect_credential(
        &mut self,
    ) -> Result<ExternalAccountCredentialProfile, IntegrationError> {
        reject_async_runtime()?;
        acquire(self.quota.as_deref(), 1)?;
        let payload = self.client.config().map_err(map_exchange_error)?;
        normalize_credential_profile(&payload, self.instrument_type.as_str())
            .map_err(IntegrationError::InvalidPayload)
    }
}

impl OkxTradingAccountMarketProfile {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub fn fetch_market_profile(
        &mut self,
        request: &ExternalMarketProfileRequest,
    ) -> Result<ExternalMarketProfile, IntegrationError> {
        reject_async_runtime()?;
        if request.source_symbol.as_str().trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "OKX market profile instrument is required".into(),
            ));
        }
        acquire(self.quota.as_deref(), 2)?;
        let fee = self
            .client
            .trade_fee(request.source_symbol.as_str())
            .map_err(map_exchange_error)?;
        let config = self.client.config().map_err(map_exchange_error)?;
        normalize_market_profile(request, &fee, &config).map_err(IntegrationError::InvalidPayload)
    }
}

impl OkxTradingOrderEntry {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl OrderEntryConnection for OkxTradingOrderEntry {
    fn submit_order(&mut self, request: &OrderEntryRequest) -> CommandResult<OrderEntryEvent> {
        reject_async_runtime()?;
        acquire_with_priority(self.quota.as_deref(), 1, SharedQuotaPriority::Ordinary)?;
        let body = order_request_body(request, self.trading_mode.as_str())?;
        let payload = match self.client.submit_order(body) {
            Ok(payload) => payload,
            Err(error) => return command_error_outcome(error),
        };
        normalize_order_submission(request, &payload)
    }

    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        reject_async_runtime()?;
        acquire_with_priority(self.quota.as_deref(), 1, SharedQuotaPriority::Reserved)?;
        let body = cancel_request_body(request, remote_order_id)?;
        let payload = match self.client.cancel_order(body) {
            Ok(payload) => payload,
            Err(error) => return command_error_outcome(error),
        };
        normalize_order_cancellation(request, remote_order_id, at_unix_nanos, &payload)
    }
}

impl OkxTradingOrderQuery {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    fn stamp(&self, mut orders: Vec<ExternalOrder>) -> Vec<ExternalOrder> {
        for order in &mut orders {
            order.binding_id.clone_from(&self.descriptor.binding_id);
        }
        orders
    }
}

impl OrderQueryConnection for OkxTradingOrderQuery {
    fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        reject_async_runtime()?;
        acquire(self.quota.as_deref(), 1)?;
        let payload = self.client.order_open(query).map_err(map_exchange_error)?;
        normalize_okx_orders(&payload)
            .map(|orders| self.stamp(orders))
            .map_err(IntegrationError::InvalidPayload)
    }

    fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        reject_async_runtime()?;
        acquire(self.quota.as_deref(), 1)?;
        let payload = self
            .client
            .order_history(query)
            .map_err(map_exchange_error)?;
        normalize_okx_orders(&payload)
            .map(|orders| self.stamp(orders))
            .map_err(IntegrationError::InvalidPayload)
    }

    fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        reject_async_runtime()?;
        acquire(self.quota.as_deref(), 1)?;
        let payload = self
            .client
            .order_detail(query)
            .map_err(map_exchange_error)?;
        let rows = payload
            .get("data")
            .and_then(serde_json::Value::as_array)
            .ok_or_else(|| {
                IntegrationError::InvalidPayload("OKX order detail data is missing".into())
            })?;
        rows.first()
            .map(normalize_okx_order)
            .transpose()
            .map(|order| {
                order.map(|mut order| {
                    order.binding_id.clone_from(&self.descriptor.binding_id);
                    order
                })
            })
            .map_err(IntegrationError::InvalidPayload)
    }
}

impl OkxTradingAccountEvents {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        reject_async_runtime()?;
        if self.socket.is_some() && self.lifecycle == ConnectionLifecycle::Ready {
            return Ok(());
        }
        self.lifecycle = ConnectionLifecycle::Starting;
        let socket = TokioSocket::connect_with_event_capacity(
            self.websocket_url.clone(),
            self.event_queue_capacity,
        )
        .map_err(IntegrationError::Transport)?;
        let timestamp = Utc::now().timestamp().to_string();
        let sign = okx_signature(
            self.secret.expose_secret(),
            &timestamp,
            "GET",
            "/users/self/verify",
            "",
        )
        .map_err(map_exchange_error)?;
        socket
            .send_text(
                serde_json::json!({
                    "op": "login",
                    "args": [{
                        "apiKey": self.api_key.expose_secret(),
                        "passphrase": self.passphrase.expose_secret(),
                        "timestamp": timestamp,
                        "sign": sign
                    }]
                })
                .to_string(),
            )
            .map_err(IntegrationError::Transport)?;
        let login = socket.recv().map_err(IntegrationError::Transport)?;
        let login = match login {
            SocketEvent::Message(message) => message,
            SocketEvent::Error(error) => return Err(IntegrationError::Transport(error)),
            SocketEvent::Backpressure => {
                return Err(IntegrationError::Backpressure(
                    "OKX private stream queue overflowed during login".into(),
                ))
            }
        };
        if !login_succeeded(&login).map_err(IntegrationError::InvalidPayload)? {
            return Err(IntegrationError::Authentication(
                "OKX private stream login was rejected".into(),
            ));
        }
        socket
            .send_text(
                serde_json::json!({
                    "op": "subscribe",
                    "args": [
                        {"channel": "account"},
                        {"channel": "orders", "instType": self.instrument_type.api_value()}
                    ]
                })
                .to_string(),
            )
            .map_err(IntegrationError::Transport)?;
        self.socket = Some(socket);
        self.lifecycle = ConnectionLifecycle::Ready;
        self.last_error = None;
        Ok(())
    }

    pub fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        reject_async_runtime()?;
        self.socket.take();
        self.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    pub fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        reject_async_runtime()?;
        self.socket.take();
        self.reconnect_count = self.reconnect_count.saturating_add(1);
        self.connect_channel()
    }

    pub fn channel_health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.lifecycle,
            healthy: self.lifecycle == ConnectionLifecycle::Ready,
            authenticated: self.lifecycle == ConnectionLifecycle::Ready,
            last_error: self.last_error.clone(),
        }
    }

    pub fn next_account_event(
        &mut self,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountEvent,
        IntegrationError,
    > {
        reject_async_runtime()?;
        loop {
            let event = self
                .socket
                .as_ref()
                .ok_or(IntegrationError::NotReady)?
                .recv()
                .map_err(IntegrationError::Transport)?;
            match event {
                SocketEvent::Message(Message::Text(text)) => {
                    if let Some(event) = parse_event(&self.segment_key, &text)
                        .map_err(IntegrationError::InvalidPayload)?
                    {
                        return Ok(event);
                    }
                }
                SocketEvent::Message(Message::Ping(payload)) => self
                    .socket
                    .as_ref()
                    .ok_or(IntegrationError::NotReady)?
                    .send_pong(payload.to_vec())
                    .map_err(IntegrationError::Transport)?,
                SocketEvent::Message(_) => {}
                SocketEvent::Error(error) => {
                    self.lifecycle = ConnectionLifecycle::Degraded;
                    self.last_error = Some(error.clone());
                    return Err(IntegrationError::Transport(error));
                }
                SocketEvent::Backpressure => {
                    self.lifecycle = ConnectionLifecycle::Degraded;
                    self.last_error = Some("OKX private stream event queue overflowed".into());
                    return Err(IntegrationError::Backpressure(
                        "OKX private stream event queue overflowed".into(),
                    ));
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    #[tokio::test]
    async fn blocking_facade_rejects_tokio_runtime_workers() {
        assert!(super::reject_async_runtime().is_err());
    }
}
