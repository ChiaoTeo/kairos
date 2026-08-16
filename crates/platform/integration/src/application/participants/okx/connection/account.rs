//! Account capability handles projected from an OKX principal context.

use super::*;

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
    pub(super) socket: Option<AsyncTokioSocket>,
    pub(super) lifecycle: ConnectionLifecycle,
    pub(super) last_error: Option<String>,
    pub(super) reconnect_count: u64,
    pub(super) channel_epoch: u64,
    pub(super) pending_messages: std::collections::VecDeque<Message>,
}

impl OkxTradingAccountEvents {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    fn fail(&mut self, message: impl Into<String>) {
        self.lifecycle = ConnectionLifecycle::Degraded;
        self.last_error = Some(message.into());
    }
}

impl AsyncAccountEventSource for OkxTradingAccountEvents {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        if self.socket.is_some() && self.lifecycle == ConnectionLifecycle::Ready {
            return Ok(());
        }
        self.lifecycle = ConnectionLifecycle::Starting;
        self.last_error = None;
        let mut socket = AsyncTokioSocket::connect(&self.websocket_url, self.event_queue_capacity)
            .await
            .map_err(|error| {
                self.fail(error.clone());
                IntegrationError::Transport(error)
            })?;
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
            .await
            .map_err(IntegrationError::Transport)?;
        let login =
            match tokio::time::timeout(std::time::Duration::from_secs(10), socket.next_event())
                .await
            {
                Err(_) => {
                    self.fail("OKX private stream login timed out");
                    return Err(IntegrationError::Unavailable(
                        "OKX private stream login timed out".into(),
                    ));
                }
                Ok(event) => match event {
                    AsyncSocketEvent::Message(message) => message,
                    AsyncSocketEvent::Error(error) => {
                        self.fail(error.clone());
                        return Err(IntegrationError::Transport(error));
                    }
                    AsyncSocketEvent::Backpressure => {
                        self.fail("OKX private stream queue overflowed during login");
                        return Err(IntegrationError::Backpressure(
                            "OKX private stream queue overflowed during login".into(),
                        ));
                    }
                },
            };
        if !login_succeeded(&login).map_err(IntegrationError::InvalidPayload)? {
            self.fail("OKX private stream login was rejected");
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
            .await
            .map_err(IntegrationError::Transport)?;
        let mut account_ready = false;
        let mut orders_ready = false;
        while !(account_ready && orders_ready) {
            let event =
                match tokio::time::timeout(std::time::Duration::from_secs(10), socket.next_event())
                    .await
                {
                    Ok(event) => event,
                    Err(_) => {
                        self.fail("OKX private stream subscription acknowledgement timed out");
                        return Err(IntegrationError::Unavailable(
                            "OKX private stream subscription acknowledgement timed out".into(),
                        ));
                    }
                };
            match event {
                AsyncSocketEvent::Message(Message::Text(text)) => {
                    let value: serde_json::Value = serde_json::from_str(&text)
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?;
                    let event = value.get("event").and_then(serde_json::Value::as_str);
                    let rejected = event == Some("error")
                        || (event == Some("subscribe")
                            && value
                                .get("code")
                                .and_then(serde_json::Value::as_str)
                                .is_some_and(|code| code != "0"));
                    if rejected {
                        self.fail("OKX private stream subscription was rejected");
                        return Err(IntegrationError::Unavailable(
                            "OKX private stream subscription was rejected".into(),
                        ));
                    }
                    if event == Some("subscribe") {
                        match value
                            .pointer("/arg/channel")
                            .and_then(serde_json::Value::as_str)
                        {
                            Some("account") => account_ready = true,
                            Some("orders") => orders_ready = true,
                            _ => {}
                        }
                    } else if value.get("data").is_some() {
                        self.pending_messages.push_back(Message::Text(text));
                    }
                }
                AsyncSocketEvent::Message(Message::Ping(payload)) => {
                    socket
                        .send_pong(payload.to_vec())
                        .await
                        .map_err(IntegrationError::Transport)?;
                }
                AsyncSocketEvent::Message(_) => {}
                AsyncSocketEvent::Error(error) => {
                    self.fail(error.clone());
                    return Err(IntegrationError::Transport(error));
                }
                AsyncSocketEvent::Backpressure => {
                    self.fail("OKX private stream queue overflowed during subscribe");
                    return Err(IntegrationError::Backpressure(
                        "OKX private stream queue overflowed during subscribe".into(),
                    ));
                }
            }
        }
        self.socket = Some(socket);
        self.channel_epoch = self.channel_epoch.saturating_add(1);
        self.lifecycle = ConnectionLifecycle::Ready;
        Ok(())
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.lifecycle = ConnectionLifecycle::Stopping;
        self.pending_messages.clear();
        if let Some(mut socket) = self.socket.take() {
            socket.close().await;
        }
        self.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.disconnect_channel().await?;
        self.reconnect_count = self.reconnect_count.saturating_add(1);
        self.connect_channel().await
    }

    fn channel_health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.lifecycle,
            healthy: self.lifecycle == ConnectionLifecycle::Ready,
            authenticated: self.lifecycle == ConnectionLifecycle::Ready,
            last_error: self.last_error.clone(),
        }
    }

    async fn next_account_event(
        &mut self,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountEventEnvelope,
        IntegrationError,
    > {
        loop {
            let event = match self.pending_messages.pop_front() {
                Some(message) => AsyncSocketEvent::Message(message),
                None => {
                    self.socket
                        .as_mut()
                        .ok_or(IntegrationError::NotReady)?
                        .next_event()
                        .await
                }
            };
            match event {
                AsyncSocketEvent::Message(Message::Text(text)) => {
                    if let Some(payload) = parse_event(&self.segment_key, &text)
                        .map_err(IntegrationError::InvalidPayload)?
                    {
                        let observed_at_unix_nanos = account_event_time(&payload);
                        return Ok(crate::application::ExternalEventEnvelope {
                            participant: self.descriptor.participant.clone(),
                            binding_id: self.descriptor.binding_id.clone(),
                            channel_id: format!(
                                "okx-private:{}:{}",
                                self.segment_key,
                                parse_provider_channel(&text).unwrap_or("unknown")
                            ),
                            channel_epoch: self.channel_epoch,
                            provider_event_id: provider_event_id(&payload),
                            provider_sequence: parse_provider_sequence(&text),
                            observed_at_unix_nanos,
                            received_at_unix_nanos: now_unix_nanos(),
                            payload,
                        });
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
                AsyncSocketEvent::Message(_) => {}
                AsyncSocketEvent::Error(error) => {
                    self.fail(error.clone());
                    return Err(IntegrationError::Transport(error));
                }
                AsyncSocketEvent::Backpressure => {
                    self.fail("OKX private stream event queue overflowed");
                    return Err(IntegrationError::Backpressure(
                        "OKX private stream event queue overflowed".into(),
                    ));
                }
            }
        }
    }
}

fn account_event_time(
    event: &crate::application::capabilities::account_facts::ExternalAccountEvent,
) -> kairos_domain_types::UnixNanos {
    use crate::application::capabilities::account_facts::ExternalAccountEvent;
    match event {
        ExternalAccountEvent::Snapshot(snapshot) => snapshot.observed_at_unix_nanos,
        ExternalAccountEvent::Order(order) => order.occurred_at_unix_nanos,
        ExternalAccountEvent::Fill(fill) => fill.occurred_at_unix_nanos,
        ExternalAccountEvent::Batch(events) => events
            .iter()
            .map(account_event_time)
            .max()
            .unwrap_or_else(|| kairos_domain_types::UnixNanos::new(0)),
    }
}

fn provider_event_id(
    event: &crate::application::capabilities::account_facts::ExternalAccountEvent,
) -> Option<String> {
    use crate::application::capabilities::account_facts::ExternalAccountEvent;
    match event {
        ExternalAccountEvent::Fill(fill) => Some(format!("okx:fill:{}", fill.fill_id)),
        ExternalAccountEvent::Order(order) => Some(format!(
            "okx:order:{}:{}",
            order.order_id,
            order.occurred_at_unix_nanos.get()
        )),
        ExternalAccountEvent::Snapshot(_) | ExternalAccountEvent::Batch(_) => None,
    }
}

fn parse_provider_sequence(text: &str) -> Option<u64> {
    let value = serde_json::from_str::<serde_json::Value>(text).ok()?;
    let sequence = value
        .get("seqId")
        .or_else(|| value.pointer("/data/0/seqId"))?;
    sequence
        .as_u64()
        .or_else(|| sequence.as_str().and_then(|value| value.parse().ok()))
}

fn parse_provider_channel(text: &str) -> Option<&'static str> {
    let value = serde_json::from_str::<serde_json::Value>(text).ok()?;
    // The returned string is borrowed from a temporary JSON value, so expose
    // only the two channel identities this capability subscribes to.
    match value
        .pointer("/arg/channel")
        .and_then(serde_json::Value::as_str)
    {
        Some("account") => Some("account"),
        Some("orders") => Some("orders"),
        _ => None,
    }
}

fn now_unix_nanos() -> kairos_domain_types::UnixNanos {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u64::MAX as u128) as u64;
    kairos_domain_types::UnixNanos::new(nanos)
}

impl OkxTradingAccountRead {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncAccountReadConnection for OkxTradingAccountRead {
    async fn fetch_account(
        &mut self,
        segment: &crate::application::capabilities::account_facts::ExternalAccountSegment,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountSnapshot,
        IntegrationError,
    > {
        acquire(self.quota.as_deref(), 3)?;
        let (balance, positions, orders) = tokio::try_join!(
            self.client.balance_async(),
            self.client.positions_async(),
            self.client.pending_orders_async(),
        )
        .map_err(map_exchange_error)?;
        normalize_account(segment, &balance, &positions, &orders)
            .map_err(IntegrationError::InvalidPayload)
    }
}

impl OkxTradingCredentialInspection {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncAccountCredentialInspectionConnection for OkxTradingCredentialInspection {
    async fn inspect_credential(
        &mut self,
    ) -> Result<ExternalAccountCredentialProfile, IntegrationError> {
        acquire(self.quota.as_deref(), 1)?;
        let payload = self
            .client
            .config_async()
            .await
            .map_err(map_exchange_error)?;
        normalize_credential_profile(&payload, self.instrument_type.as_str())
            .map_err(IntegrationError::InvalidPayload)
    }
}

impl OkxTradingAccountMarketProfile {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncAccountMarketProfileConnection for OkxTradingAccountMarketProfile {
    async fn fetch_market_profile(
        &mut self,
        request: &ExternalMarketProfileRequest,
    ) -> Result<ExternalMarketProfile, IntegrationError> {
        if request.source_symbol.as_str().trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "OKX market profile instrument is required".into(),
            ));
        }
        acquire(self.quota.as_deref(), 2)?;
        let (fee, config) = tokio::try_join!(
            self.client.trade_fee_async(request.source_symbol.as_str()),
            self.client.config_async(),
        )
        .map_err(map_exchange_error)?;
        normalize_market_profile(request, &fee, &config).map_err(IntegrationError::InvalidPayload)
    }
}
