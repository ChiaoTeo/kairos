//! Execution capability handles projected from an OKX principal context.

use super::*;

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

pub struct OkxTradingOrderEvents {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) channel_id: String,
    pub(super) instrument_type: InstrumentType,
    pub(super) trading_mode: TradingMode,
    pub(super) websocket_url: String,
    pub(super) event_queue_capacity: usize,
    pub(super) api_key: SecretString,
    pub(super) secret: SecretString,
    pub(super) passphrase: SecretString,
    pub(super) socket: Option<AsyncTokioSocket>,
    pub(super) pending: VecDeque<ExternalEventEnvelope<ExternalExecutionEvent>>,
    pub(super) lifecycle: ConnectionLifecycle,
    pub(super) channel_epoch: u64,
    pub(super) last_error: Option<String>,
}

impl OkxTradingOrderEvents {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    fn fail(&mut self, error: &IntegrationError) {
        self.lifecycle = ConnectionLifecycle::Degraded;
        self.last_error = Some(error.to_string());
    }
}

impl AsyncOrderEventSource for OkxTradingOrderEvents {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        if self.socket.is_some() && self.lifecycle == ConnectionLifecycle::Ready {
            return Ok(());
        }
        self.lifecycle = ConnectionLifecycle::Starting;
        self.last_error = None;
        self.pending.clear();
        let mut socket =
            match AsyncTokioSocket::connect(&self.websocket_url, self.event_queue_capacity).await {
                Ok(socket) => socket,
                Err(message) => {
                    let error = IntegrationError::Transport(message);
                    self.fail(&error);
                    return Err(error);
                }
            };
        let timestamp = Utc::now().timestamp().to_string();
        let sign = okx_signature(
            self.secret.expose_secret(),
            &timestamp,
            "GET",
            "/users/self/verify",
            "",
        )
        .map_err(map_exchange_error)?;
        if let Err(message) = socket
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
            .await
        {
            let error = IntegrationError::Transport(message);
            self.fail(&error);
            return Err(error);
        }
        let login = match socket.next_event().await {
            AsyncSocketEvent::Message(message) => message,
            AsyncSocketEvent::Error(message) => {
                let error = IntegrationError::Transport(message);
                self.fail(&error);
                return Err(error);
            }
            AsyncSocketEvent::Backpressure => {
                let error = IntegrationError::Backpressure(
                    "OKX order stream queue overflowed during login".into(),
                );
                self.fail(&error);
                return Err(error);
            }
        };
        if !login_succeeded(&login).map_err(IntegrationError::InvalidPayload)? {
            let error = IntegrationError::Authentication(
                "OKX private order stream login was rejected".into(),
            );
            self.fail(&error);
            return Err(error);
        }
        if let Err(message) = socket
            .send_text(
                serde_json::json!({
                    "op": "subscribe",
                    "args": [{
                        "channel": "orders",
                        "instType": self.instrument_type.api_value()
                    }]
                })
                .to_string(),
            )
            .await
        {
            let error = IntegrationError::Transport(message);
            self.fail(&error);
            return Err(error);
        }
        self.socket = Some(socket);
        self.channel_epoch = self.channel_epoch.saturating_add(1);
        self.lifecycle = ConnectionLifecycle::Ready;
        Ok(())
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.lifecycle = ConnectionLifecycle::Stopping;
        if let Some(mut socket) = self.socket.take() {
            socket.close().await;
        }
        self.pending.clear();
        self.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    fn channel_health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.lifecycle,
            healthy: self.lifecycle == ConnectionLifecycle::Ready && self.socket.is_some(),
            authenticated: self.lifecycle == ConnectionLifecycle::Ready && self.socket.is_some(),
            last_error: self.last_error.clone(),
        }
    }

    async fn next_order_event(
        &mut self,
    ) -> Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError> {
        self.connect_channel().await?;
        loop {
            if let Some(event) = self.pending.pop_front() {
                return Ok(event);
            }
            let event = self
                .socket
                .as_mut()
                .ok_or(IntegrationError::NotReady)?
                .next_event()
                .await;
            match event {
                AsyncSocketEvent::Message(Message::Text(text)) => {
                    let events = parse_execution_events(
                        &self.descriptor.binding_id,
                        &self.channel_id,
                        self.channel_epoch,
                        self.trading_mode.as_str(),
                        now_unix_nanos().into(),
                        &text,
                    )
                    .map_err(IntegrationError::InvalidPayload)?;
                    self.pending.extend(events);
                }
                AsyncSocketEvent::Message(Message::Ping(payload)) => self
                    .socket
                    .as_ref()
                    .ok_or(IntegrationError::NotReady)?
                    .send_pong(payload.to_vec())
                    .await
                    .map_err(IntegrationError::Transport)?,
                AsyncSocketEvent::Message(Message::Close(frame)) => {
                    let error = IntegrationError::ResyncRequired(format!(
                        "OKX private order stream closed: {frame:?}"
                    ));
                    self.fail(&error);
                    return Err(error);
                }
                AsyncSocketEvent::Message(_) => {}
                AsyncSocketEvent::Error(message) => {
                    let error = IntegrationError::Transport(message);
                    self.fail(&error);
                    return Err(error);
                }
                AsyncSocketEvent::Backpressure => {
                    let error = IntegrationError::Backpressure(
                        "OKX private order stream queue overflowed; reconciliation is required"
                            .into(),
                    );
                    self.fail(&error);
                    return Err(error);
                }
            }
        }
    }
}

impl OkxTradingOrderEntry {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncOrderEntryConnection for OkxTradingOrderEntry {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        acquire_with_priority(self.quota.as_deref(), 1, SharedQuotaPriority::Ordinary)?;
        let body = order_request_body(request, self.trading_mode.as_str())?;
        let payload = match self.client.submit_order_async(body).await {
            Ok(payload) => payload,
            Err(error) => return command_error_outcome(error),
        };
        normalize_order_submission(request, &payload)
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        acquire_with_priority(self.quota.as_deref(), 1, SharedQuotaPriority::Reserved)?;
        let body = cancel_request_body(request, remote_order_id)?;
        let payload = match self.client.cancel_order_async(body).await {
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

impl AsyncOrderQueryConnection for OkxTradingOrderQuery {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        acquire(self.quota.as_deref(), 1)?;
        let payload = self
            .client
            .order_open_async(query)
            .await
            .map_err(map_exchange_error)?;
        normalize_okx_orders(&payload)
            .map(|orders| self.stamp(orders))
            .map_err(IntegrationError::InvalidPayload)
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        acquire(self.quota.as_deref(), 1)?;
        let payload = self
            .client
            .order_history_async(query)
            .await
            .map_err(map_exchange_error)?;
        normalize_okx_orders(&payload)
            .map(|orders| self.stamp(orders))
            .map_err(IntegrationError::InvalidPayload)
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        acquire(self.quota.as_deref(), 1)?;
        let payload = self
            .client
            .order_detail_async(query)
            .await
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
