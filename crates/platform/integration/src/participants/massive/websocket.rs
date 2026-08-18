use std::collections::{BTreeMap, VecDeque};
use std::time::Duration;

use secrecy::ExposeSecret;
use serde_json::{json, Value};

use crate::services::participants::massive::market::data::{normalize, SocketService};
use crate::{
    ConnectionDescriptor, ConnectionHealth, ConnectionHealthQuery, ConnectionLifecycleCommand,
    IntegrationError, MarketDataKind, MarketDataStream, MarketDelivery, MarketEvent, MarketFeed,
    MarketSubscription, MarketSubscriptionCommand, MarketSubscriptionId, MarketSubscriptionOutcome,
    MarketSubscriptionRequest, ParticipantKind, ParticipantRef, ParticipantRejection,
};

use super::MassiveWebSocketConfig;

macro_rules! websocket_connection {
    ($name:ident, $domain:literal) => {
        pub struct $name {
            service: SocketService,
            subscriptions: BTreeMap<
                MarketSubscriptionId,
                (Vec<MarketFeed>, Vec<String>),
            >,
            pending: VecDeque<MarketEvent>,
            event_capacity: usize,
            next_subscription_id: u64,
        }

        impl $name {
            pub fn new(config: MassiveWebSocketConfig) -> Result<Self, IntegrationError> {
                let descriptor = descriptor(&config, $domain)?;
                let event_capacity = config.event_capacity;
                Ok(Self {
                    service: SocketService::new(
                        descriptor,
                        config.api_key.expose_secret(),
                        config.endpoint,
                        event_capacity,
                    )?,
                    subscriptions: BTreeMap::new(),
                    pending: VecDeque::new(),
                    event_capacity,
                    next_subscription_id: 1,
                })
            }

            pub fn descriptor(&self) -> &ConnectionDescriptor {
                self.service.descriptor()
            }

            async fn await_status(
                &mut self,
                expected: &str,
            ) -> Result<Option<ParticipantRejection>, IntegrationError> {
                tokio::time::timeout(Duration::from_secs(5), async {
                    loop {
                        for row in self.service.next_rows().await? {
                            let event = row.get("ev").and_then(Value::as_str).unwrap_or_default();
                            if matches!(event, "status" | "status_update") {
                                let status = row
                                    .get("status")
                                    .and_then(Value::as_str)
                                    .unwrap_or_default();
                                let message = row
                                    .get("message")
                                    .and_then(Value::as_str)
                                    .unwrap_or_default();
                                let matches = status.eq_ignore_ascii_case(expected)
                                    || message.to_ascii_lowercase().contains(expected);
                                if !matches {
                                    continue;
                                }
                                return if status.eq_ignore_ascii_case("success")
                                    || status.eq_ignore_ascii_case("auth_success")
                                    || status.eq_ignore_ascii_case(expected)
                                {
                                    Ok(None)
                                } else {
                                    Ok(Some(ParticipantRejection {
                                        code: (!status.is_empty()).then(|| status.into()),
                                        message: message.into(),
                                        participant_request_id: None,
                                    }))
                                };
                            }
                            if let Some(event) = normalize(&row)? {
                                self.buffer(event)?;
                            }
                        }
                    }
                })
                .await
                .map_err(|_| {
                    IntegrationError::Transport(format!(
                        "timed out awaiting Massive {expected} acknowledgement"
                    ))
                })?
            }

            fn buffer(&mut self, event: MarketEvent) -> Result<(), IntegrationError> {
                if self.pending.len() >= self.event_capacity {
                    return Err(IntegrationError::Backpressure(
                        "Massive market event buffer overflowed while awaiting command acknowledgement"
                            .into(),
                    ));
                }
                self.pending.push_back(event);
                Ok(())
            }

            async fn restore(&mut self) -> Result<(), IntegrationError> {
                let params = self
                    .subscriptions
                    .values()
                    .flat_map(|(_, params)| params.clone())
                    .collect::<Vec<_>>();
                if params.is_empty() {
                    return Ok(());
                }
                self.service
                    .send(json!({"action":"subscribe","params":params.join(",")}))
                    .await?;
                match self.await_status("subscribed").await? {
                    None => Ok(()),
                    Some(rejection) => Err(IntegrationError::InvalidRequest(rejection.message)),
                }
            }
        }

        impl ConnectionHealthQuery for $name {
            fn connection_health(&mut self) -> ConnectionHealth {
                self.service.health()
            }
        }

        impl ConnectionLifecycleCommand for $name {
            async fn connect(&mut self) -> Result<(), IntegrationError> {
                self.service.connect().await?;
                if let Some(rejection) = self.await_status("auth_success").await? {
                    return Err(IntegrationError::Authentication(rejection.message));
                }
                self.restore().await
            }

            async fn disconnect(&mut self) -> Result<(), IntegrationError> {
                self.pending.clear();
                self.service.disconnect().await
            }

            async fn reconnect(&mut self) -> Result<(), IntegrationError> {
                self.disconnect().await?;
                self.connect().await
            }
        }

        impl MarketSubscriptionCommand for $name {
            async fn subscribe(
                &mut self,
                request: MarketSubscriptionRequest,
            ) -> Result<MarketSubscriptionOutcome<MarketSubscription>, IntegrationError> {
                let params = request
                    .feeds
                    .iter()
                    .map(feed_parameter)
                    .collect::<Result<Vec<_>, _>>()?;
                self.service
                    .send(json!({"action":"subscribe","params":params.join(",")}))
                    .await?;
                let id = MarketSubscriptionId(self.next_subscription_id);
                self.next_subscription_id = self.next_subscription_id.saturating_add(1);
                let subscription = MarketSubscription {
                    id,
                    feeds: request.feeds.clone(),
                    delivery: MarketDelivery::Push,
                };
                match self.await_status("subscribed").await {
                    Ok(None) => {
                        self.subscriptions.insert(id, (request.feeds, params));
                        Ok(MarketSubscriptionOutcome::Confirmed(subscription))
                    }
                    Ok(Some(rejection)) => Ok(MarketSubscriptionOutcome::Rejected(rejection)),
                    Err(error) => {
                        self.subscriptions.insert(id, (request.feeds, params));
                        Ok(MarketSubscriptionOutcome::Indeterminate {
                            provisional: Some(subscription),
                            reason: error.to_string(),
                        })
                    }
                }
            }

            async fn unsubscribe(
                &mut self,
                subscription: MarketSubscriptionId,
            ) -> Result<MarketSubscriptionOutcome<()>, IntegrationError> {
                let (_, params) = self.subscriptions.get(&subscription).cloned().ok_or_else(|| {
                    IntegrationError::InvalidRequest("unknown Massive market subscription".into())
                })?;
                self.service
                    .send(json!({"action":"unsubscribe","params":params.join(",")}))
                    .await?;
                match self.await_status("unsubscribed").await {
                    Ok(None) => {
                        self.subscriptions.remove(&subscription);
                        Ok(MarketSubscriptionOutcome::Confirmed(()))
                    }
                    Ok(Some(rejection)) => Ok(MarketSubscriptionOutcome::Rejected(rejection)),
                    Err(error) => Ok(MarketSubscriptionOutcome::Indeterminate {
                        provisional: Some(()),
                        reason: error.to_string(),
                    }),
                }
            }
        }

        impl MarketDataStream for $name {
            async fn next(&mut self) -> Result<MarketEvent, IntegrationError> {
                if let Some(event) = self.pending.pop_front() {
                    return Ok(event);
                }
                loop {
                    for row in self.service.next_rows().await? {
                        if let Some(event) = normalize(&row)? {
                            self.buffer(event)?;
                        }
                    }
                    if let Some(event) = self.pending.pop_front() {
                        return Ok(event);
                    }
                }
            }
        }
    };
}

websocket_connection!(MassiveStocksWebSocketConnection, "stocks.websocket");
websocket_connection!(MassiveOptionsWebSocketConnection, "options.websocket");

fn descriptor(
    config: &MassiveWebSocketConfig,
    domain: &str,
) -> Result<ConnectionDescriptor, IntegrationError> {
    let mut descriptor = ConnectionDescriptor::new(
        config.binding_id.clone(),
        ParticipantRef::new(ParticipantKind::DataProvider, "massive")
            .map_err(IntegrationError::InvalidRequest)?,
        domain,
    )
    .map_err(IntegrationError::InvalidRequest)?;
    descriptor.environment = config.environment.clone();
    descriptor
        .validate()
        .map_err(IntegrationError::InvalidRequest)?;
    Ok(descriptor)
}

fn feed_parameter(feed: &MarketFeed) -> Result<String, IntegrationError> {
    let symbol = feed.symbol.as_ref().ok_or_else(|| {
        IntegrationError::InvalidRequest(format!("Massive {:?} feed requires symbol", feed.kind))
    })?;
    let channel = match feed.kind {
        MarketDataKind::Quote => "Q",
        MarketDataKind::Trade => "T",
        MarketDataKind::Bar | MarketDataKind::TradeBar | MarketDataKind::QuoteBar => {
            match feed.interval.as_deref() {
                Some("1s") => "A",
                Some("1m") | None => "AM",
                Some(interval) => {
                    return Err(IntegrationError::InvalidRequest(format!(
                        "Massive live aggregate interval is unsupported: {interval}"
                    )))
                }
            }
        }
        unsupported => {
            return Err(IntegrationError::InvalidRequest(format!(
                "Massive WebSocket does not support {unsupported:?}"
            )))
        }
    };
    Ok(format!(
        "{channel}.{}",
        symbol.as_str().to_ascii_uppercase()
    ))
}
