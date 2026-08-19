use std::collections::{BTreeMap, VecDeque};
use std::task::{Context, Poll};

use kairos_primitives::{FillId, OrderId, SegmentKey, Symbol};
use serde_json::{Value, json};
use tokio_tungstenite::tungstenite::Message;

use crate::participants::hyperliquid::{HyperliquidUserStreamConfig, HyperliquidWebSocketConfig};
use crate::services::participants::hyperliquid::socket::SocketService;
use crate::services::participants::hyperliquid::stream;
use crate::{
    AccountStream, ConnectionDescriptor, ConnectionHealth, ConnectionHealthQuery,
    ConnectionLifecycleCommand, DecimalValue, ExecutionStream, ExternalAccountEvent,
    ExternalAccountEventEnvelope, ExternalEventDelivery, ExternalEventEnvelope,
    ExternalExecutionEvent, ExternalFillEvent, ExternalOrderEvent, ExternalOrderStatus,
    ExternalParticipantEvent, IntegrationError, MarketDataStream, MarketDelivery, MarketEvent,
    MarketFeed, MarketSubscription, MarketSubscriptionCommand, MarketSubscriptionId,
    MarketSubscriptionOutcome, MarketSubscriptionRequest, OrderSide, ParticipantEventStream,
    ParticipantInstrumentRef,
};

pub struct HyperliquidWebSocketConnection {
    service: SocketService,
    user: Option<HyperliquidUserStreamConfig>,
    subscriptions: BTreeMap<MarketSubscriptionId, (Vec<MarketFeed>, Vec<Value>)>,
    pending_market: VecDeque<MarketEvent>,
    pending_account: VecDeque<ExternalAccountEventEnvelope>,
    pending_execution: VecDeque<ExternalEventEnvelope<ExternalExecutionEvent>>,
    next_subscription_id: u64,
    channel_epoch: u64,
    event_capacity: usize,
}

impl HyperliquidWebSocketConnection {
    pub fn new(
        connection_key: crate::ConnectionKey,
        config: HyperliquidWebSocketConfig,
    ) -> Result<Self, IntegrationError> {
        if let Some(user) = config.user.as_ref() {
            if user.address.trim().is_empty() || user.segment_key.trim().is_empty() {
                return Err(IntegrationError::InvalidRequest(
                    "Hyperliquid user address and segment key are required".into(),
                ));
            }
        }
        let user = config.user.clone();
        let event_capacity = config.event_capacity;
        Ok(Self {
            service: SocketService::new(connection_key, config)?,
            user,
            subscriptions: BTreeMap::new(),
            pending_market: VecDeque::new(),
            pending_account: VecDeque::new(),
            pending_execution: VecDeque::new(),
            next_subscription_id: 1,
            channel_epoch: 0,
            event_capacity,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        self.service.descriptor()
    }

    async fn command(&mut self, method: &str, subscription: Value) -> Result<(), IntegrationError> {
        self.service
            .send(json!({"method": method, "subscription": subscription.clone()}).to_string())
            .await?;
        loop {
            let value = self.next_value().await?;
            match value.get("channel").and_then(Value::as_str) {
                Some("subscriptionResponse")
                    if value.pointer("/data/method").and_then(Value::as_str) == Some(method)
                        && value.pointer("/data/subscription") == Some(&subscription) =>
                {
                    return Ok(());
                },
                Some("error") => {
                    return Err(IntegrationError::InvalidRequest(
                        value
                            .pointer("/data/error")
                            .or_else(|| value.get("data"))
                            .and_then(Value::as_str)
                            .unwrap_or("Hyperliquid subscription rejected")
                            .into(),
                    ));
                },
                _ => {},
            }
            self.demultiplex(&value)?;
        }
    }

    async fn apply_market_commands(
        &mut self,
        method: &str,
        subscriptions: &[Value],
    ) -> Result<usize, (usize, IntegrationError)> {
        let mut confirmed = 0;
        for subscription in subscriptions {
            if let Err(error) = self.command(method, subscription.clone()).await {
                return Err((confirmed, error));
            }
            confirmed += 1;
        }
        Ok(confirmed)
    }

    async fn next_value(&mut self) -> Result<Value, IntegrationError> {
        loop {
            match self.service.next().await? {
                Message::Text(text) => return serde_json::from_str(&text).map_err(payload),
                Message::Close(_) => {
                    return Err(IntegrationError::Transport(
                        "Hyperliquid WebSocket closed".into(),
                    ));
                },
                _ => continue,
            }
        }
    }

    fn poll_next_value(&mut self, cx: &mut Context<'_>) -> Poll<Result<Value, IntegrationError>> {
        loop {
            let message = match self.service.poll_next(cx) {
                Poll::Ready(Ok(message)) => message,
                Poll::Ready(Err(error)) => return Poll::Ready(Err(error)),
                Poll::Pending => return Poll::Pending,
            };
            match message {
                Message::Text(text) => {
                    return Poll::Ready(serde_json::from_str(&text).map_err(payload));
                },
                Message::Close(_) => {
                    return Poll::Ready(Err(IntegrationError::Transport(
                        "Hyperliquid WebSocket closed".into(),
                    )));
                },
                _ => continue,
            }
        }
    }

    fn demultiplex(&mut self, value: &Value) -> Result<(), IntegrationError> {
        match value
            .get("channel")
            .and_then(Value::as_str)
            .unwrap_or_default()
        {
            "l2Book" => {
                self.ensure_capacity(1)?;
                self.pending_market
                    .push_back(stream::book(value.get("data").ok_or_else(|| {
                        IntegrationError::InvalidPayload(
                            "Hyperliquid l2Book data is missing".into(),
                        )
                    })?)?)
            },
            "trades" => {
                let rows = value.get("data").and_then(Value::as_array).ok_or_else(|| {
                    IntegrationError::InvalidPayload("Hyperliquid trades data is missing".into())
                })?;
                self.ensure_capacity(rows.len())?;
                self.pending_market.extend(
                    rows.iter()
                        .map(stream::trade)
                        .collect::<Result<Vec<_>, _>>()?,
                );
            },
            "candle" => {
                self.ensure_capacity(1)?;
                self.pending_market
                    .push_back(stream::candle(value.get("data").ok_or_else(|| {
                        IntegrationError::InvalidPayload(
                            "Hyperliquid candle data is missing".into(),
                        )
                    })?)?)
            },
            "allMids" => {
                let mids = value
                    .pointer("/data/mids")
                    .or_else(|| value.get("data"))
                    .and_then(Value::as_object)
                    .ok_or_else(|| {
                        IntegrationError::InvalidPayload(
                            "Hyperliquid allMids data is missing".into(),
                        )
                    })?;
                self.ensure_capacity(mids.len())?;
                for (coin, price) in mids {
                    let mut event =
                        stream::empty(coin, crate::MarketEventKind::Quote, stream::now())?;
                    event.price = stream::optional(Some(price))?;
                    self.pending_market.push_back(event);
                }
            },
            "orderUpdates" => self.normalize_orders(value.get("data").unwrap_or(&Value::Null))?,
            "userEvents" | "userFills" => {
                self.normalize_fills(value.get("data").unwrap_or(&Value::Null))?
            },
            _ => {},
        }
        Ok(())
    }

    fn ensure_capacity(&self, additions: usize) -> Result<(), IntegrationError> {
        if self.pending_market.len()
            + self.pending_account.len()
            + self.pending_execution.len()
            + additions
            > self.event_capacity
        {
            return Err(IntegrationError::Backpressure(
                "Hyperliquid event buffer overflowed".into(),
            ));
        }
        Ok(())
    }

    fn normalize_orders(&mut self, data: &Value) -> Result<(), IntegrationError> {
        let rows = data
            .as_array()
            .or_else(|| data.get("orders").and_then(Value::as_array))
            .cloned()
            .unwrap_or_default();
        self.ensure_capacity(rows.len().saturating_mul(2))?;
        let descriptor = self.descriptor().clone();
        for row in rows {
            let order = row.get("order").unwrap_or(&row);
            let oid = order
                .get("oid")
                .and_then(Value::as_u64)
                .map(|v| v.to_string())
                .unwrap_or_else(|| "unknown".into());
            let coin = order
                .get("coin")
                .and_then(Value::as_str)
                .unwrap_or("UNKNOWN");
            let status = row
                .get("status")
                .and_then(Value::as_str)
                .unwrap_or("unknown");
            let observed = stream::millis(row.get("statusTimestamp").and_then(Value::as_u64));
            let order_id = OrderId::new(order.get("cloid").and_then(Value::as_str).unwrap_or(&oid))
                .map_err(payload)?;
            let participant_event_id = format!("hyperliquid:{oid}:{status}:{}", observed.get());
            self.pending_account.push_back(ExternalEventEnvelope {
                participant: descriptor.participant.clone(),
                connection_key: descriptor.connection_key.clone(),
                channel_id: format!("{}.user", descriptor.connection_key),
                channel_epoch: self.channel_epoch,
                participant_event_id: Some(participant_event_id.clone()),
                participant_sequence: None,
                delivery: ExternalEventDelivery::Incremental,
                observed_at_unix_nanos: observed,
                received_at_unix_nanos: stream::now(),
                payload: ExternalAccountEvent::Order(ExternalOrderEvent {
                    order_id: order_id.clone(),
                    status: account_order_status(status),
                    remote_order_id: Some(
                        kairos_primitives::RemoteOrderId::new(&oid).map_err(payload)?,
                    ),
                    filled_quantity: None,
                    occurred_at_unix_nanos: observed,
                    reason: status.into(),
                }),
            });
            self.pending_execution.push_back(ExternalEventEnvelope {
                participant: descriptor.participant.clone(),
                connection_key: descriptor.connection_key.clone(),
                channel_id: format!("{}.user", descriptor.connection_key),
                channel_epoch: self.channel_epoch,
                participant_event_id: Some(participant_event_id),
                participant_sequence: None,
                delivery: ExternalEventDelivery::Incremental,
                observed_at_unix_nanos: observed,
                received_at_unix_nanos: stream::now(),
                payload: ExternalExecutionEvent {
                    order_id,
                    symbol: Symbol::new(coin).map_err(payload)?,
                    status: crate::domain::execution::normalize_order_status(status),
                    side: order.get("side").and_then(Value::as_str).map(|v| {
                        if v == "A" {
                            OrderSide::Sell
                        } else {
                            OrderSide::Buy
                        }
                    }),
                    order_type: None,
                    quantity: decimal(order.get("sz"))?,
                    limit_price: decimal(order.get("limitPx"))?,
                    filled_quantity: None,
                    remaining_quantity: None,
                    fill_quantity: None,
                    fill_price: None,
                    execution_id: None,
                    fee_currency: None,
                    fee_amount: None,
                    occurred_at_unix_nanos: observed,
                    reason: String::new(),
                },
            });
        }
        Ok(())
    }

    fn normalize_fills(&mut self, data: &Value) -> Result<(), IntegrationError> {
        let delivery = if data.get("isSnapshot").and_then(Value::as_bool) == Some(true) {
            ExternalEventDelivery::Snapshot
        } else {
            ExternalEventDelivery::Incremental
        };
        let rows = data
            .get("fills")
            .and_then(Value::as_array)
            .or_else(|| data.as_array())
            .cloned()
            .unwrap_or_default();
        let Some(user) = self.user.as_ref() else {
            return Ok(());
        };
        self.ensure_capacity(rows.len().saturating_mul(2))?;
        let segment_key = SegmentKey::new(&user.segment_key).map_err(payload)?;
        let descriptor = self.descriptor().clone();
        for row in rows {
            let coin = row.get("coin").and_then(Value::as_str).unwrap_or("UNKNOWN");
            let oid = row
                .get("oid")
                .and_then(Value::as_u64)
                .map(|v| v.to_string())
                .unwrap_or_else(|| "unknown".into());
            let tid = row
                .get("tid")
                .and_then(Value::as_u64)
                .map(|v| v.to_string())
                .unwrap_or_else(|| format!("{oid}:fill"));
            let observed = stream::millis(row.get("time").and_then(Value::as_u64));
            let fill_quantity = required_decimal(row.get("sz"))?;
            let fill_price = required_decimal(row.get("px"))?;
            let instrument =
                ParticipantInstrumentRef::new(descriptor.participant.clone(), None, coin)
                    .map_err(IntegrationError::InvalidPayload)?;
            let account_payload = ExternalAccountEvent::Fill(ExternalFillEvent {
                fill_id: FillId::new(format!("hyperliquid:{tid}")).map_err(payload)?,
                order_id: OrderId::new(&oid).map_err(payload)?,
                segment_key: segment_key.clone(),
                participant_instrument: instrument,
                side: row
                    .get("side")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .into(),
                quantity: fill_quantity,
                price: fill_price,
                fee_asset: None,
                fee_amount: decimal(row.get("fee"))?,
                occurred_at_unix_nanos: observed,
            });
            self.pending_account.push_back(ExternalEventEnvelope {
                participant: descriptor.participant.clone(),
                connection_key: descriptor.connection_key.clone(),
                channel_id: format!("{}.user", descriptor.connection_key),
                channel_epoch: self.channel_epoch,
                participant_event_id: Some(format!("hyperliquid:{tid}")),
                participant_sequence: None,
                delivery,
                observed_at_unix_nanos: observed,
                received_at_unix_nanos: stream::now(),
                payload: account_payload,
            });
            self.pending_execution.push_back(ExternalEventEnvelope {
                participant: descriptor.participant.clone(),
                connection_key: descriptor.connection_key.clone(),
                channel_id: format!("{}.user", descriptor.connection_key),
                channel_epoch: self.channel_epoch,
                participant_event_id: Some(format!("hyperliquid:{tid}")),
                participant_sequence: None,
                delivery,
                observed_at_unix_nanos: observed,
                received_at_unix_nanos: stream::now(),
                payload: ExternalExecutionEvent {
                    order_id: OrderId::new(&oid).map_err(payload)?,
                    symbol: Symbol::new(coin).map_err(payload)?,
                    status: crate::OrderStatus::PartiallyFilled,
                    side: None,
                    order_type: None,
                    quantity: None,
                    limit_price: None,
                    filled_quantity: None,
                    remaining_quantity: None,
                    fill_quantity: Some(fill_quantity),
                    fill_price: Some(fill_price),
                    execution_id: Some(FillId::new(format!("hyperliquid:{tid}")).map_err(payload)?),
                    fee_currency: None,
                    fee_amount: decimal(row.get("fee"))?,
                    occurred_at_unix_nanos: observed,
                    reason: String::new(),
                },
            });
        }
        Ok(())
    }

    async fn restore(&mut self) -> Result<(), IntegrationError> {
        let market = self
            .subscriptions
            .values()
            .flat_map(|(_, values)| values.clone())
            .collect::<Vec<_>>();
        for subscription in market {
            self.command("subscribe", subscription).await?;
        }
        if let Some(user) = self.user.clone() {
            for kind in ["orderUpdates", "userEvents", "userFills"] {
                self.command("subscribe", json!({"type": kind, "user": user.address}))
                    .await?;
            }
        }
        Ok(())
    }

    fn poll_receive(&mut self, cx: &mut Context<'_>) -> Poll<Result<(), IntegrationError>> {
        match self.poll_next_value(cx) {
            Poll::Ready(Ok(value)) => Poll::Ready(self.demultiplex(&value)),
            Poll::Ready(Err(error)) => Poll::Ready(Err(error)),
            Poll::Pending => Poll::Pending,
        }
    }
}

impl ConnectionHealthQuery for HyperliquidWebSocketConnection {
    fn connection_health(&mut self) -> ConnectionHealth {
        self.service.health()
    }
}
impl ConnectionLifecycleCommand for HyperliquidWebSocketConnection {
    async fn connect(&mut self) -> Result<(), IntegrationError> {
        self.service.connect().await?;
        self.channel_epoch = self.channel_epoch.saturating_add(1);
        self.restore().await
    }
    async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        self.pending_market.clear();
        self.pending_account.clear();
        self.pending_execution.clear();
        self.service.disconnect().await
    }
    async fn reconnect(&mut self) -> Result<(), IntegrationError> {
        self.disconnect().await?;
        self.connect().await
    }
}
impl crate::ConnectionMaintenance for HyperliquidWebSocketConnection {
    fn next_maintenance_at(&self) -> Option<tokio::time::Instant> {
        self.service.next_maintenance_at()
    }

    fn poll_maintenance(
        &mut self,
        _cx: &mut Context<'_>,
        now: tokio::time::Instant,
    ) -> Poll<Result<crate::MaintenanceOutcome, IntegrationError>> {
        self.service.poll_maintenance(now)
    }
}
impl MarketSubscriptionCommand for HyperliquidWebSocketConnection {
    async fn subscribe(
        &mut self,
        request: MarketSubscriptionRequest,
    ) -> Result<MarketSubscriptionOutcome<MarketSubscription>, IntegrationError> {
        let mut values = Vec::new();
        for feed in &request.feeds {
            let value = stream::subscription(feed)?;
            if !values.contains(&value) {
                values.push(value);
            }
        }
        let id = MarketSubscriptionId(self.next_subscription_id);
        self.next_subscription_id = self.next_subscription_id.saturating_add(1);
        let subscription = MarketSubscription {
            id,
            feeds: request.feeds.clone(),
            delivery: MarketDelivery::Push,
        };
        match self.apply_market_commands("subscribe", &values).await {
            Ok(_) => {
                self.subscriptions.insert(id, (request.feeds, values));
                Ok(MarketSubscriptionOutcome::Confirmed(subscription))
            },
            Err((0, IntegrationError::InvalidRequest(message))) => Ok(
                MarketSubscriptionOutcome::Rejected(crate::ParticipantRejection {
                    code: None,
                    message,
                    participant_request_id: Some(id.0.to_string()),
                }),
            ),
            Err((0, IntegrationError::NotReady)) => Err(IntegrationError::NotReady),
            Err((confirmed, error)) => {
                self.subscriptions.insert(id, (request.feeds, values));
                Ok(MarketSubscriptionOutcome::Indeterminate {
                    provisional: Some(subscription),
                    reason: format!(
                        "Hyperliquid confirmed {confirmed} feeds before subscription became uncertain: {error}"
                    ),
                })
            },
        }
    }
    async fn unsubscribe(
        &mut self,
        subscription: MarketSubscriptionId,
    ) -> Result<MarketSubscriptionOutcome<()>, IntegrationError> {
        let (_, values) = self
            .subscriptions
            .get(&subscription)
            .cloned()
            .ok_or_else(|| {
                IntegrationError::InvalidRequest("unknown Hyperliquid subscription".into())
            })?;
        match self.apply_market_commands("unsubscribe", &values).await {
            Ok(_) => {
                self.subscriptions.remove(&subscription);
                Ok(MarketSubscriptionOutcome::Confirmed(()))
            },
            Err((0, IntegrationError::InvalidRequest(message))) => Ok(
                MarketSubscriptionOutcome::Rejected(crate::ParticipantRejection {
                    code: None,
                    message,
                    participant_request_id: Some(subscription.0.to_string()),
                }),
            ),
            Err((0, IntegrationError::NotReady)) => Err(IntegrationError::NotReady),
            Err((confirmed, error)) => Ok(MarketSubscriptionOutcome::Indeterminate {
                provisional: Some(()),
                reason: format!(
                    "Hyperliquid confirmed removal of {confirmed} feeds before unsubscription became uncertain: {error}"
                ),
            }),
        }
    }
}
impl MarketDataStream for HyperliquidWebSocketConnection {
    fn poll_next(&mut self, cx: &mut Context<'_>) -> Poll<Result<MarketEvent, IntegrationError>> {
        loop {
            if let Some(v) = self.pending_market.pop_front() {
                return Poll::Ready(Ok(v));
            }
            match self.poll_receive(cx) {
                Poll::Ready(Ok(())) => {},
                Poll::Ready(Err(error)) => return Poll::Ready(Err(error)),
                Poll::Pending => return Poll::Pending,
            }
        }
    }
}
impl AccountStream for HyperliquidWebSocketConnection {
    fn poll_next(
        &mut self,
        cx: &mut Context<'_>,
    ) -> Poll<Result<ExternalAccountEventEnvelope, IntegrationError>> {
        if self.user.is_none() {
            return Poll::Ready(Err(IntegrationError::InvalidRequest(
                "Hyperliquid user stream is not configured".into(),
            )));
        }
        loop {
            if let Some(v) = self.pending_account.pop_front() {
                return Poll::Ready(Ok(v));
            }
            match self.poll_receive(cx) {
                Poll::Ready(Ok(())) => {},
                Poll::Ready(Err(error)) => return Poll::Ready(Err(error)),
                Poll::Pending => return Poll::Pending,
            }
        }
    }
}
impl ExecutionStream for HyperliquidWebSocketConnection {
    fn poll_next(
        &mut self,
        cx: &mut Context<'_>,
    ) -> Poll<Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError>> {
        if self.user.is_none() {
            return Poll::Ready(Err(IntegrationError::InvalidRequest(
                "Hyperliquid user stream is not configured".into(),
            )));
        }
        loop {
            if let Some(v) = self.pending_execution.pop_front() {
                return Poll::Ready(Ok(v));
            }
            match self.poll_receive(cx) {
                Poll::Ready(Ok(())) => {},
                Poll::Ready(Err(error)) => return Poll::Ready(Err(error)),
                Poll::Pending => return Poll::Pending,
            }
        }
    }
}
impl ParticipantEventStream for HyperliquidWebSocketConnection {
    fn poll_next(
        &mut self,
        cx: &mut Context<'_>,
    ) -> Poll<Result<ExternalParticipantEvent, IntegrationError>> {
        loop {
            if let Some(value) = self.pending_account.pop_front() {
                return Poll::Ready(Ok(ExternalParticipantEvent::Account(value)));
            }
            if let Some(value) = self.pending_execution.pop_front() {
                return Poll::Ready(Ok(ExternalParticipantEvent::Execution(value)));
            }
            if let Some(value) = self.pending_market.pop_front() {
                return Poll::Ready(Ok(ExternalParticipantEvent::Market(value)));
            }
            match self.poll_receive(cx) {
                Poll::Ready(Ok(())) => {},
                Poll::Ready(Err(error)) => return Poll::Ready(Err(error)),
                Poll::Pending => return Poll::Pending,
            }
        }
    }
}

fn decimal(value: Option<&Value>) -> Result<Option<DecimalValue>, IntegrationError> {
    value
        .and_then(Value::as_str)
        .filter(|v| !v.is_empty())
        .map(|v| parse_decimal(v))
        .transpose()
}
fn account_order_status(value: &str) -> ExternalOrderStatus {
    match value.to_ascii_lowercase().as_str() {
        "open" | "triggered" => ExternalOrderStatus::Acknowledged,
        "filled" => ExternalOrderStatus::Filled,
        "canceled" | "cancelled" | "margin_canceled" => ExternalOrderStatus::Canceled,
        "rejected" => ExternalOrderStatus::Rejected,
        "expired" => ExternalOrderStatus::Expired,
        _ => ExternalOrderStatus::Unknown,
    }
}
fn required_decimal(value: Option<&Value>) -> Result<DecimalValue, IntegrationError> {
    let value = value
        .and_then(Value::as_str)
        .ok_or_else(|| IntegrationError::InvalidPayload("Hyperliquid decimal missing".into()))?;
    parse_decimal(value)
}
fn parse_decimal(value: &str) -> Result<DecimalValue, IntegrationError> {
    DecimalValue::parse(value).map_err(payload)
}
fn payload(error: impl std::fmt::Display) -> IntegrationError {
    IntegrationError::InvalidPayload(error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn connection() -> HyperliquidWebSocketConnection {
        HyperliquidWebSocketConnection::new(
            crate::ConnectionKey::new("account.hyperliquid.test").unwrap(),
            HyperliquidWebSocketConfig {
                environment: "test".into(),
                endpoint: "ws://127.0.0.1:1".into(),
                event_capacity: 8,
                user: Some(HyperliquidUserStreamConfig {
                    address: "0x0000000000000000000000000000000000000001".into(),
                    segment_key: "trading".into(),
                }),
            },
        )
        .unwrap()
    }

    #[test]
    fn order_update_emits_account_and_execution_facts() {
        let mut connection = connection();
        connection
            .normalize_orders(&serde_json::json!([{
                "status": "filled",
                "statusTimestamp": 1_700_000_000_000_u64,
                "order": {
                    "coin": "BTC",
                    "side": "B",
                    "limitPx": "50000",
                    "sz": "0.1",
                    "oid": 42,
                    "cloid": "client-42"
                }
            }]))
            .unwrap();

        let account = connection.pending_account.pop_front().unwrap();
        let execution = connection.pending_execution.pop_front().unwrap();
        assert_eq!(account.participant_event_id, execution.participant_event_id);
        match account.payload {
            ExternalAccountEvent::Order(order) => {
                assert_eq!(order.order_id.as_str(), "client-42");
                assert_eq!(order.status, ExternalOrderStatus::Filled);
                assert_eq!(
                    order.remote_order_id.as_ref().map(|value| value.as_str()),
                    Some("42")
                );
            },
            other => panic!("expected account order event, got {other:?}"),
        }
        assert_eq!(execution.payload.order_id.as_str(), "client-42");
    }

    #[test]
    fn fill_snapshot_flag_is_preserved_for_recovery_deduplication() {
        let mut connection = connection();
        connection
            .normalize_fills(&serde_json::json!({
                "isSnapshot": true,
                "fills": [{
                    "coin":"BTC","oid":42,"tid":84,"time":1_700_000_000_000_u64,
                    "sz":"0.1","px":"50000","side":"B","fee":"0.01"
                }]
            }))
            .unwrap();

        let account = connection.pending_account.pop_front().unwrap();
        let execution = connection.pending_execution.pop_front().unwrap();
        assert_eq!(account.delivery, ExternalEventDelivery::Snapshot);
        assert_eq!(execution.delivery, ExternalEventDelivery::Snapshot);
        assert_eq!(
            account.participant_event_id.as_deref(),
            Some("hyperliquid:84")
        );
    }
}
