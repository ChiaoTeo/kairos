use std::collections::{BTreeMap, BTreeSet};
use std::task::{Context, Poll};

use serde_json::{Value, json};
use tokio_tungstenite::tungstenite::Message;

use crate::participants::okx::OkxWebSocketConfig;
use crate::services::participants::okx::market;
use crate::services::participants::okx::market_stream::{
    ControlBudget, MarketStreamPolicy, PlannedStream,
};
use crate::services::participants::okx::socket::SocketService;
use crate::transport::websocket::InboundDispatcher;
use crate::{
    ConnectionDescriptor, ConnectionHealth, ConnectionHealthQuery, ConnectionLifecycleCommand,
    IntegrationError, MarketDataStream, MarketDelivery, MarketEvent, MarketFeed,
    MarketSubscription, MarketSubscriptionCommand, MarketSubscriptionId, MarketSubscriptionOutcome,
    MarketSubscriptionRequest,
};

pub struct OkxPublicWebSocketConnection {
    service: SocketService,
    subscriptions: BTreeMap<MarketSubscriptionId, LogicalSubscription>,
    physical_streams: BTreeMap<PlannedStream, usize>,
    policy: MarketStreamPolicy,
    control_budget: ControlBudget,
    pending: InboundDispatcher<MarketEvent>,
    order_book_sequences: crate::services::sequence::OrderBookSequenceTracker,
    next_subscription_id: u64,
    next_request_id: u64,
}

#[derive(Clone, Debug)]
struct LogicalSubscription {
    feeds: Vec<MarketFeed>,
    streams: Vec<PlannedStream>,
}

#[derive(Debug, Default)]
struct ControlConfirmation {
    accepted: Vec<PlannedStream>,
    rejections: Vec<crate::ParticipantRejection>,
}

impl OkxPublicWebSocketConnection {
    pub fn new(
        connection_key: crate::ConnectionKey,
        config: OkxWebSocketConfig,
    ) -> Result<Self, IntegrationError> {
        let event_capacity = config.event_capacity;
        Ok(Self {
            service: SocketService::new(connection_key, config, "public.websocket", None)?,
            subscriptions: BTreeMap::new(),
            physical_streams: BTreeMap::new(),
            policy: MarketStreamPolicy::default(),
            control_budget: ControlBudget::default(),
            pending: InboundDispatcher::new(event_capacity)?,
            order_book_sequences: Default::default(),
            next_subscription_id: 1,
            next_request_id: 1,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        self.service.descriptor()
    }

    fn queue_market_events(
        &mut self,
        events: impl IntoIterator<Item = MarketEvent>,
    ) -> Result<(), IntegrationError> {
        for event in events {
            if !crate::services::participants::okx::market_stream::event_is_demanded(
                self.subscriptions
                    .values()
                    .flat_map(|subscription| subscription.feeds.iter()),
                &event,
            ) {
                continue;
            }
            match self.order_book_sequences.validate_okx(&event)? {
                crate::services::sequence::SequenceDisposition::Accept => {
                    self.pending.buffer(event)?;
                },
                crate::services::sequence::SequenceDisposition::Duplicate => {},
            }
        }
        Ok(())
    }

    fn rebuild_physical_streams(&mut self) {
        self.physical_streams = crate::services::participants::okx::market_stream::reference_counts(
            self.subscriptions
                .values()
                .map(|subscription| subscription.streams.clone()),
        );
    }

    async fn send_and_confirm(
        &mut self,
        operation: &str,
        streams: Vec<PlannedStream>,
        recovery: bool,
    ) -> Result<ControlConfirmation, IntegrationError> {
        if streams.is_empty() {
            return Ok(ControlConfirmation::default());
        }
        self.control_budget
            .admit(&self.policy, tokio::time::Instant::now(), recovery)?;
        let arguments = streams
            .iter()
            .map(PlannedStream::argument)
            .collect::<Vec<_>>();
        let request_id = self.next_request_id.to_string();
        self.next_request_id = self.next_request_id.saturating_add(1);
        self.service
            .send(json!({"id": request_id, "op": operation, "args": arguments}).to_string())
            .await?;
        let mut remaining = streams;
        let mut confirmation = ControlConfirmation::default();
        while !remaining.is_empty() {
            let value = tokio::time::timeout(std::time::Duration::from_secs(10), self.next_value())
                .await
                .map_err(|_| {
                    IntegrationError::Transport(format!(
                        "OKX {operation} acknowledgement timed out"
                    ))
                })??;
            if value.get("id").and_then(Value::as_str) == Some(request_id.as_str()) {
                let matched = value.get("arg").and_then(|argument| {
                    remaining
                        .iter()
                        .position(|stream| stream.argument() == *argument)
                });
                if value.get("event").and_then(Value::as_str) == Some("error")
                    || value
                        .get("code")
                        .and_then(Value::as_str)
                        .is_some_and(|code| code != "0")
                {
                    let rejection = crate::ParticipantRejection {
                        code: value.get("code").and_then(Value::as_str).map(str::to_owned),
                        message: value
                            .get("msg")
                            .and_then(Value::as_str)
                            .unwrap_or("OKX subscription rejected")
                            .into(),
                        participant_request_id: Some(request_id.clone()),
                    };
                    if let Some(index) = matched {
                        remaining.remove(index);
                        confirmation.rejections.push(rejection);
                    } else {
                        confirmation
                            .rejections
                            .extend(remaining.drain(..).map(|_| rejection.clone()));
                    }
                } else if let Some(index) = matched {
                    confirmation.accepted.push(remaining.remove(index));
                } else {
                    return Err(IntegrationError::InvalidPayload(format!(
                        "OKX {operation} acknowledgement did not identify a requested argument"
                    )));
                }
                continue;
            }
            self.queue_market_events(market::stream_events(&value)?)?;
        }
        Ok(confirmation)
    }

    async fn apply_control(
        &mut self,
        operation: &str,
        streams: Vec<PlannedStream>,
        recovery: bool,
    ) -> Result<ControlConfirmation, IntegrationError> {
        let batches = self.policy.batches(operation, streams)?;
        let mut aggregate = ControlConfirmation::default();
        for batch in batches {
            let mut confirmation = self
                .send_and_confirm(operation, batch.streams, recovery)
                .await?;
            aggregate.accepted.append(&mut confirmation.accepted);
            aggregate.rejections.append(&mut confirmation.rejections);
        }
        Ok(aggregate)
    }

    async fn next_value(&mut self) -> Result<Value, IntegrationError> {
        loop {
            match self.service.next().await? {
                Message::Text(text) if text.as_str() == "pong" => continue,
                Message::Text(text) => {
                    return serde_json::from_str(&text)
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()));
                },
                Message::Close(_) => {
                    return Err(IntegrationError::Transport(
                        "OKX public WebSocket closed".into(),
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
                Message::Text(text) if text.as_str() == "pong" => continue,
                Message::Text(text) => {
                    return Poll::Ready(
                        serde_json::from_str(&text)
                            .map_err(|error| IntegrationError::InvalidPayload(error.to_string())),
                    );
                },
                Message::Close(_) => {
                    return Poll::Ready(Err(IntegrationError::Transport(
                        "OKX public WebSocket closed".into(),
                    )));
                },
                _ => continue,
            }
        }
    }

    async fn restore(&mut self) -> Result<(), IntegrationError> {
        let streams = self.physical_streams.keys().cloned().collect::<Vec<_>>();
        if !streams.is_empty() {
            let confirmation = self.apply_control("subscribe", streams, true).await?;
            if !confirmation.rejections.is_empty() {
                return Err(IntegrationError::InvalidRequest(format!(
                    "OKX rejected {} restored subscriptions",
                    confirmation.rejections.len()
                )));
            }
        }
        Ok(())
    }
}

impl ConnectionHealthQuery for OkxPublicWebSocketConnection {
    fn connection_health(&mut self) -> ConnectionHealth {
        self.service.health()
    }
}

impl ConnectionLifecycleCommand for OkxPublicWebSocketConnection {
    async fn connect(&mut self) -> Result<(), IntegrationError> {
        self.service.connect().await?;
        self.control_budget.reset();
        if let Err(error) = self.restore().await {
            self.service.disconnect().await?;
            return Err(error);
        }
        Ok(())
    }

    async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        self.pending.clear();
        self.order_book_sequences.clear();
        self.service.disconnect().await
    }

    async fn reconnect(&mut self) -> Result<(), IntegrationError> {
        let retired = self.service.begin_replacement().await?;
        let previous_budget = std::mem::take(&mut self.control_budget);
        match self.restore().await {
            Ok(()) => {
                self.service.commit_replacement(retired).await;
                Ok(())
            },
            Err(error) => {
                self.service.rollback_replacement(retired).await;
                self.control_budget = previous_budget;
                Err(error)
            },
        }
    }
}

impl MarketSubscriptionCommand for OkxPublicWebSocketConnection {
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
            .apply_control("subscribe", new_streams.clone(), false)
            .await
        {
            Ok(confirmation) if confirmation.rejections.is_empty() => {
                self.subscriptions.insert(id, logical);
                self.rebuild_physical_streams();
                Ok(MarketSubscriptionOutcome::Confirmed(subscription))
            },
            Ok(mut confirmation) if confirmation.rejections.len() == new_streams.len() => Ok(
                MarketSubscriptionOutcome::Rejected(confirmation.rejections.remove(0)),
            ),
            Ok(confirmation) => {
                self.subscriptions.insert(id, logical);
                self.rebuild_physical_streams();
                Ok(MarketSubscriptionOutcome::Indeterminate {
                    provisional: Some(subscription),
                    reason: format!(
                        "OKX accepted part of the subscription and rejected {} feeds",
                        confirmation.rejections.len()
                    ),
                })
            },
            Err(IntegrationError::NotReady) => Err(IntegrationError::NotReady),
            Err(error) => Ok(MarketSubscriptionOutcome::Indeterminate {
                provisional: {
                    self.subscriptions.insert(id, logical);
                    self.rebuild_physical_streams();
                    Some(subscription)
                },
                reason: error.to_string(),
            }),
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
                IntegrationError::InvalidRequest("unknown OKX market subscription".into())
            })?;
        let removed_streams = logical
            .streams
            .iter()
            .filter(|stream| self.physical_streams.get(*stream) == Some(&1))
            .cloned()
            .collect::<Vec<_>>();
        match self
            .apply_control("unsubscribe", removed_streams.clone(), false)
            .await
        {
            Ok(confirmation) if confirmation.rejections.is_empty() => {
                self.subscriptions.remove(&subscription);
                self.rebuild_physical_streams();
                Ok(MarketSubscriptionOutcome::Confirmed(()))
            },
            Ok(mut confirmation) if confirmation.rejections.len() == removed_streams.len() => Ok(
                MarketSubscriptionOutcome::Rejected(confirmation.rejections.remove(0)),
            ),
            Ok(confirmation) => {
                self.subscriptions.remove(&subscription);
                self.rebuild_physical_streams();
                Ok(MarketSubscriptionOutcome::Indeterminate {
                    provisional: Some(()),
                    reason: format!(
                        "OKX removed part of the subscription and rejected {} feeds; desired state will be restored on reconnect",
                        confirmation.rejections.len()
                    ),
                })
            },
            Err(IntegrationError::NotReady) => Err(IntegrationError::NotReady),
            Err(error) => {
                self.subscriptions.remove(&subscription);
                self.rebuild_physical_streams();
                Ok(MarketSubscriptionOutcome::Indeterminate {
                    provisional: Some(()),
                    reason: format!("{error}; desired state will be restored on reconnect"),
                })
            },
        }
    }
}

impl MarketDataStream for OkxPublicWebSocketConnection {
    fn poll_next(&mut self, cx: &mut Context<'_>) -> Poll<Result<MarketEvent, IntegrationError>> {
        if let Some(event) = self.pending.pop() {
            return Poll::Ready(Ok(event));
        }
        loop {
            let value = match self.poll_next_value(cx) {
                Poll::Ready(Ok(value)) => value,
                Poll::Ready(Err(error)) => return Poll::Ready(Err(error)),
                Poll::Pending => return Poll::Pending,
            };
            let mut events = match market::stream_events(&value) {
                Ok(events) => events,
                Err(error) => return Poll::Ready(Err(error)),
            };
            if let Err(error) = self.queue_market_events(events.drain(..)) {
                return Poll::Ready(Err(error));
            }
            if let Some(event) = self.pending.pop() {
                return Poll::Ready(Ok(event));
            }
        }
    }
}

impl crate::ConnectionMaintenance for OkxPublicWebSocketConnection {
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

#[cfg(test)]
mod tests {
    use futures_util::{SinkExt, StreamExt};
    use kairos_primitives::integration::ParticipantSymbol;
    use serde_json::json;
    use tokio::net::TcpListener;
    use tokio_tungstenite::accept_async;

    use super::*;
    use crate::{ConnectionKey, MarketDataKind};

    fn trade_feed() -> MarketFeed {
        MarketFeed {
            kind: MarketDataKind::Trade,
            symbol: Some(ParticipantSymbol::new("BTC-USDT").unwrap()),
            interval: None,
            depth: None,
            update_speed_millis: None,
        }
    }

    async fn acknowledge(
        socket: &mut tokio_tungstenite::WebSocketStream<tokio::net::TcpStream>,
        expected_operation: &str,
    ) {
        let message = socket.next().await.unwrap().unwrap();
        let Message::Text(text) = message else {
            panic!("expected OKX text control message")
        };
        let request: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(
            request.get("op").and_then(Value::as_str),
            Some(expected_operation)
        );
        let id = request.get("id").and_then(Value::as_str).unwrap();
        for argument in request.get("args").and_then(Value::as_array).unwrap() {
            socket
                .send(Message::Text(
                    json!({"id":id,"event":expected_operation,"arg":argument})
                        .to_string()
                        .into(),
                ))
                .await
                .unwrap();
        }
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

        let mut connection = OkxPublicWebSocketConnection::new(
            ConnectionKey::new("okx-public-test").unwrap(),
            OkxWebSocketConfig {
                environment: "test".into(),
                endpoint: format!("ws://{address}"),
                event_capacity: 16,
            },
        )
        .unwrap();
        connection.connect().await.unwrap();
        let first = match connection
            .subscribe(MarketSubscriptionRequest::new(vec![trade_feed()]).unwrap())
            .await
            .unwrap()
        {
            MarketSubscriptionOutcome::Confirmed(subscription) => subscription,
            other => panic!("unexpected first subscription outcome: {other:?}"),
        };
        let second = match connection
            .subscribe(MarketSubscriptionRequest::new(vec![trade_feed()]).unwrap())
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
