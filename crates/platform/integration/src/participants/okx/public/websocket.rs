use std::collections::BTreeMap;

use serde_json::{json, Value};
use tokio_tungstenite::tungstenite::Message;

use crate::participants::okx::OkxWebSocketConfig;
use crate::services::participants::okx::{market, socket::SocketService};
use crate::transport::websocket::InboundDispatcher;
use crate::{
    ConnectionDescriptor, ConnectionHealth, ConnectionHealthQuery, ConnectionLifecycleCommand,
    IntegrationError, MarketDataStream, MarketDelivery, MarketEvent, MarketFeed,
    MarketSubscription, MarketSubscriptionCommand, MarketSubscriptionId, MarketSubscriptionOutcome,
    MarketSubscriptionRequest,
};

pub struct OkxPublicWebSocketConnection {
    service: SocketService,
    subscriptions: BTreeMap<MarketSubscriptionId, (Vec<MarketFeed>, Vec<Value>)>,
    pending: InboundDispatcher<MarketEvent>,
    next_subscription_id: u64,
    next_request_id: u64,
}

impl OkxPublicWebSocketConnection {
    pub fn new(config: OkxWebSocketConfig) -> Result<Self, IntegrationError> {
        let event_capacity = config.event_capacity;
        Ok(Self {
            service: SocketService::new(config, "public.websocket", None)?,
            subscriptions: BTreeMap::new(),
            pending: InboundDispatcher::new(event_capacity)?,
            next_subscription_id: 1,
            next_request_id: 1,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        self.service.descriptor()
    }

    async fn send_and_confirm(
        &mut self,
        operation: &str,
        arguments: Vec<Value>,
    ) -> Result<Vec<crate::ParticipantRejection>, IntegrationError> {
        if arguments.is_empty() {
            return Ok(Vec::new());
        }
        let expected = arguments.len();
        let request_id = self.next_request_id.to_string();
        self.next_request_id = self.next_request_id.saturating_add(1);
        self.service
            .send(json!({"id": request_id, "op": operation, "args": arguments}).to_string())
            .await?;
        let mut received = 0;
        let mut rejections = Vec::new();
        while received < expected {
            let value = self.next_value().await?;
            if value.get("id").and_then(Value::as_str) == Some(request_id.as_str()) {
                received += 1;
                if value.get("event").and_then(Value::as_str) == Some("error")
                    || value
                        .get("code")
                        .and_then(Value::as_str)
                        .is_some_and(|code| code != "0")
                {
                    rejections.push(crate::ParticipantRejection {
                        code: value.get("code").and_then(Value::as_str).map(str::to_owned),
                        message: value
                            .get("msg")
                            .and_then(Value::as_str)
                            .unwrap_or("OKX subscription rejected")
                            .into(),
                        participant_request_id: Some(request_id.clone()),
                    });
                }
                continue;
            }
            self.pending.extend(market::stream_events(&value)?)?;
        }
        Ok(rejections)
    }

    async fn next_value(&mut self) -> Result<Value, IntegrationError> {
        loop {
            match self.service.next().await? {
                Message::Text(text) if text.as_str() == "pong" => continue,
                Message::Text(text) => {
                    return serde_json::from_str(&text)
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
                }
                Message::Close(_) => {
                    return Err(IntegrationError::Transport(
                        "OKX public WebSocket closed".into(),
                    ))
                }
                _ => continue,
            }
        }
    }

    async fn restore(&mut self) -> Result<(), IntegrationError> {
        let arguments = self
            .subscriptions
            .values()
            .flat_map(|(_, arguments)| arguments.clone())
            .collect::<Vec<_>>();
        if !arguments.is_empty() {
            let rejections = self.send_and_confirm("subscribe", arguments).await?;
            if !rejections.is_empty() {
                return Err(IntegrationError::InvalidRequest(format!(
                    "OKX rejected {} restored subscriptions",
                    rejections.len()
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
        self.restore().await
    }

    async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        self.pending.clear();
        self.service.disconnect().await
    }

    async fn reconnect(&mut self) -> Result<(), IntegrationError> {
        self.service.reconnect().await?;
        self.restore().await
    }
}

impl MarketSubscriptionCommand for OkxPublicWebSocketConnection {
    async fn subscribe(
        &mut self,
        request: MarketSubscriptionRequest,
    ) -> Result<MarketSubscriptionOutcome<MarketSubscription>, IntegrationError> {
        let arguments = request
            .feeds
            .iter()
            .map(market::feed_argument)
            .collect::<Result<Vec<_>, _>>()?;
        let id = MarketSubscriptionId(self.next_subscription_id);
        self.next_subscription_id = self.next_subscription_id.saturating_add(1);
        let subscription = MarketSubscription {
            id,
            feeds: request.feeds.clone(),
            delivery: MarketDelivery::Push,
        };
        match self.send_and_confirm("subscribe", arguments.clone()).await {
            Ok(rejections) if rejections.is_empty() => {
                self.subscriptions.insert(id, (request.feeds, arguments));
                Ok(MarketSubscriptionOutcome::Confirmed(subscription))
            }
            Ok(mut rejections) if rejections.len() == arguments.len() => {
                Ok(MarketSubscriptionOutcome::Rejected(rejections.remove(0)))
            }
            Ok(rejections) => {
                self.subscriptions.insert(id, (request.feeds, arguments));
                Ok(MarketSubscriptionOutcome::Indeterminate {
                    provisional: Some(subscription),
                    reason: format!(
                        "OKX accepted part of the subscription and rejected {} feeds",
                        rejections.len()
                    ),
                })
            }
            Err(IntegrationError::NotReady) => Err(IntegrationError::NotReady),
            Err(error) => Ok(MarketSubscriptionOutcome::Indeterminate {
                provisional: {
                    self.subscriptions.insert(id, (request.feeds, arguments));
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
        let (_, arguments) = self
            .subscriptions
            .get(&subscription)
            .cloned()
            .ok_or_else(|| {
                IntegrationError::InvalidRequest("unknown OKX market subscription".into())
            })?;
        match self
            .send_and_confirm("unsubscribe", arguments.clone())
            .await
        {
            Ok(rejections) if rejections.is_empty() => {
                self.subscriptions.remove(&subscription);
                Ok(MarketSubscriptionOutcome::Confirmed(()))
            }
            Ok(mut rejections) if rejections.len() == arguments.len() => {
                Ok(MarketSubscriptionOutcome::Rejected(rejections.remove(0)))
            }
            Ok(rejections) => Ok(MarketSubscriptionOutcome::Indeterminate {
                provisional: Some(()),
                reason: format!(
                    "OKX removed part of the subscription and rejected {} feeds",
                    rejections.len()
                ),
            }),
            Err(IntegrationError::NotReady) => Err(IntegrationError::NotReady),
            Err(error) => Ok(MarketSubscriptionOutcome::Indeterminate {
                provisional: Some(()),
                reason: error.to_string(),
            }),
        }
    }
}

impl MarketDataStream for OkxPublicWebSocketConnection {
    async fn next(&mut self) -> Result<MarketEvent, IntegrationError> {
        if let Some(event) = self.pending.pop() {
            return Ok(event);
        }
        loop {
            let value = self.next_value().await?;
            let mut events = market::stream_events(&value)?;
            let Some(first) = events.pop_front() else {
                continue;
            };
            self.pending.extend(events)?;
            return Ok(first);
        }
    }
}
