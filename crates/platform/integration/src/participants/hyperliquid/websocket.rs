use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::task::{Context, Poll};

use kairos_primitives::account::SegmentKey;
use kairos_primitives::execution::{FillId, OrderId};
use kairos_primitives::reference::Symbol;
use serde_json::{Value, json};
use tokio_tungstenite::tungstenite::Message;

use crate::participants::hyperliquid::{HyperliquidUserStreamConfig, HyperliquidWebSocketConfig};
use crate::services::participants::hyperliquid::market_stream::{
    ControlBudget, MarketStreamPolicy, PlannedStream,
};
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
    subscriptions: BTreeMap<MarketSubscriptionId, LogicalSubscription>,
    physical_streams: BTreeMap<PlannedStream, usize>,
    policy: MarketStreamPolicy,
    control_budget: ControlBudget,
    pending_market: VecDeque<MarketEvent>,
    pending_account: VecDeque<ExternalAccountEventEnvelope>,
    pending_execution: VecDeque<ExternalEventEnvelope<ExternalExecutionEvent>>,
    next_subscription_id: u64,
    channel_epoch: u64,
    event_capacity: usize,
    reserved_streams: usize,
}

#[derive(Clone, Debug)]
struct LogicalSubscription {
    feeds: Vec<MarketFeed>,
    streams: Vec<PlannedStream>,
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
        let service = SocketService::new(connection_key, config)?;
        let policy = MarketStreamPolicy::default();
        let reserved_streams = if user.is_some() {
            policy.reserve_process_subscriptions(3, false)?;
            3
        } else {
            0
        };
        Ok(Self {
            service,
            user,
            subscriptions: BTreeMap::new(),
            physical_streams: BTreeMap::new(),
            policy,
            control_budget: ControlBudget::default(),
            pending_market: VecDeque::new(),
            pending_account: VecDeque::new(),
            pending_execution: VecDeque::new(),
            next_subscription_id: 1,
            channel_epoch: 0,
            event_capacity,
            reserved_streams,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        self.service.descriptor()
    }

    async fn command(
        &mut self,
        method: &str,
        subscription: Value,
        recovery: bool,
    ) -> Result<(), IntegrationError> {
        self.control_budget
            .admit(&self.policy, tokio::time::Instant::now(), recovery)?;
        self.service
            .send(json!({"method": method, "subscription": subscription.clone()}).to_string())
            .await?;
        tokio::time::timeout(std::time::Duration::from_secs(10), async {
            loop {
                let value = self.next_value().await?;
                match value.get("channel").and_then(Value::as_str) {
                    Some("subscriptionResponse")
                        if value.pointer("/data/method").and_then(Value::as_str)
                            == Some(method)
                            && value.pointer("/data/subscription").is_some_and(|actual| {
                                subscription_ack_matches(&subscription, actual)
                            }) =>
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
        })
        .await
        .map_err(|_| {
            IntegrationError::Transport(format!("Hyperliquid {method} acknowledgement timed out"))
        })?
    }

    async fn apply_market_commands(
        &mut self,
        method: &str,
        subscriptions: &[PlannedStream],
        recovery: bool,
    ) -> Result<usize, (usize, IntegrationError)> {
        let mut confirmed = 0;
        for subscription in subscriptions {
            if let Err(error) = self
                .command(method, subscription.subscription(), recovery)
                .await
            {
                return Err((confirmed, error));
            }
            confirmed += 1;
        }
        Ok(confirmed)
    }

    fn rebuild_physical_streams(&mut self) {
        self.physical_streams =
            crate::services::participants::hyperliquid::market_stream::reference_counts(
                self.subscriptions
                    .values()
                    .map(|subscription| subscription.streams.clone()),
            );
    }

    fn queue_market_events(
        &mut self,
        events: impl IntoIterator<Item = MarketEvent>,
    ) -> Result<(), IntegrationError> {
        let demanded = events
            .into_iter()
            .filter(|event| {
                crate::services::participants::hyperliquid::market_stream::event_is_demanded(
                    self.subscriptions
                        .values()
                        .flat_map(|subscription| subscription.feeds.iter()),
                    event,
                )
            })
            .collect::<Vec<_>>();
        self.ensure_capacity(demanded.len())?;
        self.pending_market.extend(demanded);
        Ok(())
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
                self.queue_market_events([stream::book(value.get("data").ok_or_else(|| {
                    IntegrationError::InvalidPayload("Hyperliquid l2Book data is missing".into())
                })?)?])?
            },
            "trades" => {
                let rows = value.get("data").and_then(Value::as_array).ok_or_else(|| {
                    IntegrationError::InvalidPayload("Hyperliquid trades data is missing".into())
                })?;
                self.queue_market_events(
                    rows.iter()
                        .map(stream::trade)
                        .collect::<Result<Vec<_>, _>>()?,
                )?;
            },
            "candle" => self.queue_market_events([stream::candle(
                value.get("data").ok_or_else(|| {
                    IntegrationError::InvalidPayload("Hyperliquid candle data is missing".into())
                })?,
            )?])?,
            "bbo" => {
                self.queue_market_events([stream::best_bid_offer(value.get("data").ok_or_else(
                    || IntegrationError::InvalidPayload("Hyperliquid bbo data is missing".into()),
                )?)?])?
            },
            "activeAssetCtx" => self.queue_market_events(stream::active_asset_context(
                value.get("data").ok_or_else(|| {
                    IntegrationError::InvalidPayload(
                        "Hyperliquid active asset context data is missing".into(),
                    )
                })?,
            )?)?,
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
                let mut events = Vec::new();
                for (coin, price) in mids {
                    let mut event =
                        stream::empty(coin, crate::MarketEventKind::Quote, stream::now())?;
                    event.price = stream::optional(Some(price))?;
                    events.push(event);
                }
                self.queue_market_events(events)?;
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
                        kairos_primitives::integration::RemoteOrderId::new(&oid)
                            .map_err(payload)?,
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
        let market = self.physical_streams.keys().cloned().collect::<Vec<_>>();
        self.policy.admit_subscription_count(market.len(), true)?;
        for subscription in market {
            self.command("subscribe", subscription.subscription(), true)
                .await?;
        }
        if let Some(user) = self.user.clone() {
            for kind in ["orderUpdates", "userEvents", "userFills"] {
                self.command(
                    "subscribe",
                    json!({"type": kind, "user": user.address}),
                    true,
                )
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
        self.control_budget = ControlBudget::default();
        if let Err(error) = self.restore().await {
            self.service.disconnect().await?;
            return Err(error);
        }
        Ok(())
    }
    async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        self.pending_market.clear();
        self.pending_account.clear();
        self.pending_execution.clear();
        self.service.disconnect().await
    }
    async fn reconnect(&mut self) -> Result<(), IntegrationError> {
        let overlap = self.physical_streams.len() + usize::from(self.user.is_some()) * 3;
        if self
            .policy
            .reserve_process_subscriptions(overlap, true)
            .is_err()
        {
            self.service.disconnect().await?;
            self.control_budget = ControlBudget::default();
            self.service.connect().await?;
            let next_epoch = self.channel_epoch.saturating_add(1);
            if let Err(error) = self.restore().await {
                self.service.disconnect().await?;
                return Err(error);
            }
            self.channel_epoch = next_epoch;
            self.pending_market.clear();
            self.pending_account.clear();
            self.pending_execution.clear();
            return Ok(());
        }
        let retired = match self.service.begin_replacement().await {
            Ok(retired) => retired,
            Err(error) => {
                self.policy.release_process_subscriptions(overlap);
                return Err(error);
            },
        };
        let previous_budget = std::mem::take(&mut self.control_budget);
        let next_epoch = self.channel_epoch.saturating_add(1);
        match self.restore().await {
            Ok(()) => {
                self.service.commit_replacement(retired).await;
                self.policy.release_process_subscriptions(overlap);
                self.channel_epoch = next_epoch;
                self.pending_market.clear();
                self.pending_account.clear();
                self.pending_execution.clear();
                Ok(())
            },
            Err(error) => {
                self.service.rollback_replacement(retired).await;
                self.policy.release_process_subscriptions(overlap);
                self.control_budget = previous_budget;
                Err(error)
            },
        }
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
        let streams = request
            .feeds
            .iter()
            .map(|feed| self.policy.plan(feed))
            .collect::<Result<BTreeSet<_>, _>>()?
            .into_iter()
            .collect::<Vec<_>>();
        let new_streams = streams
            .iter()
            .filter(|stream| !self.physical_streams.contains_key(*stream))
            .cloned()
            .collect::<Vec<_>>();
        self.policy.admit_subscription_count(
            self.physical_streams
                .len()
                .saturating_add(new_streams.len()),
            false,
        )?;
        self.policy
            .reserve_process_subscriptions(new_streams.len(), false)?;
        self.reserved_streams = self.reserved_streams.saturating_add(new_streams.len());
        let id = MarketSubscriptionId(self.next_subscription_id);
        self.next_subscription_id = self.next_subscription_id.saturating_add(1);
        let subscription = MarketSubscription {
            id,
            feeds: request.feeds.clone(),
            delivery: MarketDelivery::Push,
        };
        let logical = LogicalSubscription {
            feeds: request.feeds,
            streams,
        };
        match self
            .apply_market_commands("subscribe", &new_streams, false)
            .await
        {
            Ok(_) => {
                self.subscriptions.insert(id, logical);
                self.rebuild_physical_streams();
                Ok(MarketSubscriptionOutcome::Confirmed(subscription))
            },
            Err((0, IntegrationError::InvalidRequest(message))) => {
                self.policy.release_process_subscriptions(new_streams.len());
                self.reserved_streams = self.reserved_streams.saturating_sub(new_streams.len());
                Ok(MarketSubscriptionOutcome::Rejected(
                    crate::ParticipantRejection {
                        code: None,
                        message,
                        participant_request_id: Some(id.0.to_string()),
                    },
                ))
            },
            Err((0, error @ (IntegrationError::NotReady | IntegrationError::RateLimited(_)))) => {
                self.policy.release_process_subscriptions(new_streams.len());
                self.reserved_streams = self.reserved_streams.saturating_sub(new_streams.len());
                Err(error)
            },
            Err((confirmed, error)) => {
                self.subscriptions.insert(id, logical);
                self.rebuild_physical_streams();
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
        let logical = self
            .subscriptions
            .get(&subscription)
            .cloned()
            .ok_or_else(|| {
                IntegrationError::InvalidRequest("unknown Hyperliquid subscription".into())
            })?;
        let removed_streams = logical
            .streams
            .iter()
            .filter(|stream| self.physical_streams.get(*stream) == Some(&1))
            .cloned()
            .collect::<Vec<_>>();
        match self
            .apply_market_commands("unsubscribe", &removed_streams, false)
            .await
        {
            Ok(_) => {
                self.subscriptions.remove(&subscription);
                self.rebuild_physical_streams();
                self.policy
                    .release_process_subscriptions(removed_streams.len());
                self.reserved_streams = self.reserved_streams.saturating_sub(removed_streams.len());
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
            Err((confirmed, error)) => {
                self.subscriptions.remove(&subscription);
                self.rebuild_physical_streams();
                self.policy
                    .release_process_subscriptions(removed_streams.len());
                self.reserved_streams = self.reserved_streams.saturating_sub(removed_streams.len());
                Ok(MarketSubscriptionOutcome::Indeterminate {
                    provisional: Some(()),
                    reason: format!(
                        "Hyperliquid confirmed removal of {confirmed} feeds before unsubscription became uncertain: {error}; desired state will be restored on reconnect"
                    ),
                })
            },
        }
    }
}

impl Drop for HyperliquidWebSocketConnection {
    fn drop(&mut self) {
        self.policy
            .release_process_subscriptions(self.reserved_streams);
        self.reserved_streams = 0;
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

fn subscription_ack_matches(requested: &Value, actual: &Value) -> bool {
    match (requested.as_object(), actual.as_object()) {
        (Some(requested), Some(actual)) => requested
            .iter()
            .all(|(key, value)| actual.get(key) == Some(value)),
        _ => requested == actual,
    }
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
    use futures_util::{SinkExt, StreamExt};
    use kairos_primitives::integration::ParticipantSymbol;
    use tokio::net::TcpListener;
    use tokio_tungstenite::accept_async;

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

    #[test]
    fn subscription_ack_accepts_provider_supplied_default_fields() {
        assert!(subscription_ack_matches(
            &json!({"type":"l2Book","coin":"@109"}),
            &json!({
                "type":"l2Book","coin":"@109",
                "nSigFigs":null,"mantissa":null,"fast":false
            })
        ));
    }

    fn quote_feed() -> MarketFeed {
        MarketFeed {
            kind: crate::MarketDataKind::Quote,
            symbol: Some(ParticipantSymbol::new("BTC").unwrap()),
            interval: None,
            depth: None,
            update_speed_millis: None,
        }
    }

    async fn acknowledge(
        socket: &mut tokio_tungstenite::WebSocketStream<tokio::net::TcpStream>,
        expected_method: &str,
    ) {
        let message = socket.next().await.unwrap().unwrap();
        let Message::Text(text) = message else {
            panic!("expected Hyperliquid text control message")
        };
        let request: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(
            request.get("method").and_then(Value::as_str),
            Some(expected_method)
        );
        let subscription = request.get("subscription").unwrap();
        socket
            .send(Message::Text(
                json!({
                    "channel":"subscriptionResponse",
                    "data":{"method":expected_method,"subscription":subscription}
                })
                .to_string()
                .into(),
            ))
            .await
            .unwrap();
    }

    #[tokio::test(flavor = "current_thread")]
    async fn duplicate_logical_demand_uses_one_physical_stream_across_reconnect() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (first_transport, _) = listener.accept().await.unwrap();
            let mut first = accept_async(first_transport).await.unwrap();
            acknowledge(&mut first, "subscribe").await;

            let (second_transport, _) = listener.accept().await.unwrap();
            let mut second = accept_async(second_transport).await.unwrap();
            acknowledge(&mut second, "subscribe").await;
            assert!(matches!(
                first.next().await,
                Some(Ok(Message::Close(_))) | None
            ));
            acknowledge(&mut second, "unsubscribe").await;
        });

        let mut connection = HyperliquidWebSocketConnection::new(
            crate::ConnectionKey::new("hyperliquid-market-test").unwrap(),
            HyperliquidWebSocketConfig {
                environment: "test".into(),
                endpoint: format!("ws://{address}"),
                event_capacity: 16,
                user: None,
            },
        )
        .unwrap();
        connection.connect().await.unwrap();
        let first = match connection
            .subscribe(MarketSubscriptionRequest::new(vec![quote_feed()]).unwrap())
            .await
            .unwrap()
        {
            MarketSubscriptionOutcome::Confirmed(subscription) => subscription,
            other => panic!("unexpected first subscription outcome: {other:?}"),
        };
        let second = match connection
            .subscribe(MarketSubscriptionRequest::new(vec![quote_feed()]).unwrap())
            .await
            .unwrap()
        {
            MarketSubscriptionOutcome::Confirmed(subscription) => subscription,
            other => panic!("unexpected second subscription outcome: {other:?}"),
        };
        assert_eq!(
            connection.physical_streams.values().copied().sum::<usize>(),
            2
        );
        assert!(matches!(
            connection.unsubscribe(first.id).await.unwrap(),
            MarketSubscriptionOutcome::Confirmed(())
        ));
        connection.reconnect().await.unwrap();
        assert!(matches!(
            connection.unsubscribe(second.id).await.unwrap(),
            MarketSubscriptionOutcome::Confirmed(())
        ));
        connection.disconnect().await.unwrap();
        server.await.unwrap();
    }
}
