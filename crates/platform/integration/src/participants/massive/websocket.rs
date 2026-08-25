use std::collections::{BTreeMap, VecDeque};
use std::task::{Context, Poll};
use std::time::Duration;

use secrecy::ExposeSecret;
use serde_json::{Value, json};

use super::MassiveWebSocketConfig;
use crate::services::participants::massive::market::data::{SocketService, normalize};
use crate::services::participants::massive::market_stream::{
    MarketStreamPolicy, PlannedStream, Product, event_is_demanded,
};
use crate::{
    ConnectionDescriptor, ConnectionHealth, ConnectionHealthQuery, ConnectionLifecycleCommand,
    IntegrationError, MarketDataStream, MarketDelivery, MarketEvent, MarketFeed,
    MarketSubscription, MarketSubscriptionCommand, MarketSubscriptionId, MarketSubscriptionOutcome,
    MarketSubscriptionRequest, ParticipantKind, ParticipantRef, ParticipantRejection,
};

macro_rules! websocket_connection {
    ($name:ident, $domain:literal, $product:expr) => {
        pub struct $name {
            service: SocketService,
            subscriptions: BTreeMap<
                MarketSubscriptionId,
                (Vec<MarketFeed>, Vec<PlannedStream>),
            >,
            physical: BTreeMap<PlannedStream, usize>,
            policy: MarketStreamPolicy,
            pending: VecDeque<MarketEvent>,
            event_capacity: usize,
            next_subscription_id: u64,
        }

        impl $name {
            pub fn new(
                connection_key: crate::ConnectionKey,
                config: MassiveWebSocketConfig,
            ) -> Result<Self, IntegrationError> {
                let descriptor = descriptor(connection_key, &config, $domain)?;
                let event_capacity = config.event_capacity;
                Ok(Self {
                    service: SocketService::new(
                        descriptor,
                        config.api_key.expose_secret(),
                        config.endpoint,
                        event_capacity,
                    )?,
                    subscriptions: BTreeMap::new(),
                    physical: BTreeMap::new(),
                    policy: MarketStreamPolicy::new($product),
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
                                if let Some(error) = status_error(status, message) {
                                    return Err(error);
                                }
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
                let params = self.physical.keys().cloned().collect::<Vec<_>>();
                if params.is_empty() {
                    return Ok(());
                }
                self.service
                    .send(control_payload("subscribe", &params))
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
                let result = async {
                    if let Some(rejection) = self.await_status("auth_success").await? {
                        return Err(IntegrationError::Authentication(rejection.message));
                    }
                    self.restore().await
                }
                .await;
                if result.is_err() {
                    let _ = self.service.disconnect().await;
                }
                result
            }

            async fn disconnect(&mut self) -> Result<(), IntegrationError> {
                self.pending.clear();
                self.service.disconnect().await
            }

            async fn reconnect(&mut self) -> Result<(), IntegrationError> {
                let retired = self.service.begin_replacement().await?;
                let result = async {
                    if let Some(rejection) = self.await_status("auth_success").await? {
                        return Err(IntegrationError::Authentication(rejection.message));
                    }
                    self.restore().await
                }
                .await;
                match result {
                    Ok(()) => {
                        self.pending.clear();
                        self.service.commit_replacement(retired).await;
                        Ok(())
                    },
                    Err(error) => {
                        self.service.rollback_replacement(retired).await;
                        Err(error)
                    },
                }
            }
        }

        impl MarketSubscriptionCommand for $name {
            async fn subscribe(
                &mut self,
                request: MarketSubscriptionRequest,
            ) -> Result<MarketSubscriptionOutcome<MarketSubscription>, IntegrationError> {
                let streams = request
                    .feeds
                    .iter()
                    .map(|feed| self.policy.plan(feed))
                    .collect::<Result<Vec<_>, _>>()?;
                let new_streams = streams
                    .iter()
                    .filter(|stream| !self.physical.contains_key(*stream))
                    .cloned()
                    .collect::<Vec<_>>();
                let mut desired = self.physical.clone();
                for stream in &streams {
                    *desired.entry(stream.clone()).or_default() += 1;
                }
                self.policy.admit(&desired)?;
                if !new_streams.is_empty() {
                    self.service.send(control_payload("subscribe", &new_streams)).await?;
                }
                let id = MarketSubscriptionId(self.next_subscription_id);
                self.next_subscription_id = self.next_subscription_id.saturating_add(1);
                let subscription = MarketSubscription {
                    id,
                    feeds: request.feeds.clone(),
                    delivery: MarketDelivery::Push,
                };
                let acknowledgement = if new_streams.is_empty() {
                    Ok(None)
                } else {
                    self.await_status("subscribed").await
                };
                match acknowledgement {
                    Ok(None) => {
                        self.physical = desired;
                        self.subscriptions.insert(id, (request.feeds, streams));
                        Ok(MarketSubscriptionOutcome::Confirmed(subscription))
                    }
                    Ok(Some(rejection)) => Ok(MarketSubscriptionOutcome::Rejected(rejection)),
                    Err(error) => {
                        self.physical = desired;
                        self.subscriptions.insert(id, (request.feeds, streams));
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
                let (_, streams) = self.subscriptions.get(&subscription).cloned().ok_or_else(|| {
                    IntegrationError::InvalidRequest("unknown Massive market subscription".into())
                })?;
                let mut desired = self.physical.clone();
                for stream in &streams {
                    let count = desired.get_mut(stream).expect("logical stream has physical ref");
                    *count -= 1;
                    if *count == 0 {
                        desired.remove(stream);
                    }
                }
                let removed = streams
                    .iter()
                    .filter(|stream| !desired.contains_key(*stream))
                    .cloned()
                    .collect::<Vec<_>>();
                if !removed.is_empty() {
                    self.service.send(control_payload("unsubscribe", &removed)).await?;
                }
                let acknowledgement = if removed.is_empty() {
                    Ok(None)
                } else {
                    self.await_status("unsubscribed").await
                };
                match acknowledgement {
                    Ok(None) => {
                        self.physical = desired;
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
            fn poll_next(
                &mut self,
                cx: &mut Context<'_>,
            ) -> Poll<Result<MarketEvent, IntegrationError>> {
                if let Some(event) = self.pending.pop_front() {
                    return Poll::Ready(Ok(event));
                }
                loop {
                    let rows = match self.service.poll_next_rows(cx) {
                        Poll::Ready(Ok(rows)) => rows,
                        Poll::Ready(Err(error)) => return Poll::Ready(Err(error)),
                        Poll::Pending => return Poll::Pending,
                    };
                    for row in rows {
                        match normalize(&row) {
                            Ok(Some(event)) if event_is_demanded(
                                self.subscriptions.values().flat_map(|(feeds, _)| feeds),
                                &event,
                            ) => {
                                if let Err(error) = self.buffer(event) {
                                    return Poll::Ready(Err(error));
                                }
                            }
                            Ok(Some(_)) => {}
                            Ok(None) => {}
                            Err(error) => return Poll::Ready(Err(error)),
                        }
                    }
                    if let Some(event) = self.pending.pop_front() {
                        return Poll::Ready(Ok(event));
                    }
                }
            }
        }

        impl crate::ConnectionMaintenance for $name {
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
    };
}

websocket_connection!(
    MassiveStocksWebSocketConnection,
    "stocks.websocket",
    Product::Stocks
);
websocket_connection!(
    MassiveOptionsWebSocketConnection,
    "options.websocket",
    Product::Options
);
websocket_connection!(
    MassiveFuturesWebSocketConnection,
    "futures.websocket",
    Product::Futures
);
websocket_connection!(
    MassiveIndicesWebSocketConnection,
    "indices.websocket",
    Product::Indices
);
websocket_connection!(
    MassiveForexWebSocketConnection,
    "forex.websocket",
    Product::Forex
);
websocket_connection!(
    MassiveCryptoWebSocketConnection,
    "crypto.websocket",
    Product::Crypto
);

fn descriptor(
    connection_key: crate::ConnectionKey,
    config: &MassiveWebSocketConfig,
    domain: &str,
) -> Result<ConnectionDescriptor, IntegrationError> {
    let mut descriptor = ConnectionDescriptor::new(
        connection_key,
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

#[cfg(test)]
fn feed_parameter(feed: &MarketFeed, product: Product) -> Result<String, IntegrationError> {
    Ok(MarketStreamPolicy::new(product).plan(feed)?.0)
}

fn control_payload(action: &str, streams: &[PlannedStream]) -> Value {
    json!({
        "action": action,
        "params": streams.iter().map(|stream| stream.0.as_str()).collect::<Vec<_>>().join(",")
    })
}

fn status_error(status: &str, message: &str) -> Option<IntegrationError> {
    let combined = format!("{status} {message}").to_ascii_lowercase();
    if combined.contains("auth_failed") || combined.contains("authentication") {
        Some(IntegrationError::Authentication(message.to_owned()))
    } else if combined.contains("entitle")
        || combined.contains("not authorized")
        || combined.contains("permission")
    {
        Some(IntegrationError::Entitlement(message.to_owned()))
    } else if combined.contains("maximum")
        || combined.contains("too many")
        || combined.contains("limit")
    {
        Some(IntegrationError::RateLimited(message.to_owned()))
    } else if matches!(status.to_ascii_lowercase().as_str(), "error" | "failed") {
        Some(IntegrationError::InvalidRequest(message.to_owned()))
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use futures_util::{SinkExt, StreamExt};
    use kairos_primitives::integration::ParticipantSymbol;
    use secrecy::SecretString;
    use serde_json::{Value, json};
    use tokio::net::TcpListener;
    use tokio_tungstenite::accept_async;
    use tokio_tungstenite::tungstenite::Message;

    use super::{MassiveStocksWebSocketConnection, Product, feed_parameter, status_error};
    use crate::participants::massive::MassiveWebSocketConfig;
    use crate::{
        ConnectionLifecycleCommand, MarketDataKind, MarketFeed, MarketSubscriptionCommand,
        MarketSubscriptionOutcome, MarketSubscriptionRequest,
    };

    fn feed(kind: MarketDataKind, interval: Option<&str>) -> MarketFeed {
        MarketFeed {
            kind,
            symbol: Some(ParticipantSymbol::new("I:SPX").unwrap()),
            interval: interval.map(str::to_owned),
            depth: None,
            update_speed_millis: None,
        }
    }

    #[test]
    fn indices_use_value_and_aggregate_channels() {
        assert_eq!(
            feed_parameter(&feed(MarketDataKind::IndexPrice, None), Product::Indices).unwrap(),
            "V.I:SPX"
        );
        assert_eq!(
            feed_parameter(&feed(MarketDataKind::Bar, Some("1s")), Product::Indices).unwrap(),
            "A.I:SPX"
        );
        assert_eq!(
            feed_parameter(&feed(MarketDataKind::Bar, Some("1m")), Product::Indices).unwrap(),
            "AM.I:SPX"
        );
    }

    #[test]
    fn indices_reject_equity_quote_channels() {
        assert!(feed_parameter(&feed(MarketDataKind::Quote, None), Product::Indices).is_err());
    }

    #[test]
    fn currency_products_use_product_specific_channels() {
        assert_eq!(
            feed_parameter(&feed(MarketDataKind::Quote, None), Product::Forex).unwrap(),
            "C.I:SPX"
        );
        assert_eq!(
            feed_parameter(&feed(MarketDataKind::Bar, Some("1s")), Product::Forex).unwrap(),
            "CAS.I:SPX"
        );
        assert_eq!(
            feed_parameter(&feed(MarketDataKind::Trade, None), Product::Crypto).unwrap(),
            "XT.I:SPX"
        );
        assert_eq!(
            feed_parameter(&feed(MarketDataKind::Bar, Some("1m")), Product::Crypto).unwrap(),
            "XA.I:SPX"
        );
    }

    #[test]
    fn provider_failures_are_classified_without_waiting_for_a_timeout() {
        assert!(matches!(
            status_error("auth_failed", "invalid API key"),
            Some(crate::IntegrationError::Authentication(_))
        ));
        assert!(matches!(
            status_error("error", "not authorized for indices"),
            Some(crate::IntegrationError::Entitlement(_))
        ));
        assert!(matches!(
            status_error("error", "maximum subscriptions exceeded"),
            Some(crate::IntegrationError::RateLimited(_))
        ));
    }

    async fn authenticate(socket: &mut tokio_tungstenite::WebSocketStream<tokio::net::TcpStream>) {
        let Message::Text(text) = socket.next().await.unwrap().unwrap() else {
            panic!("expected Massive authentication message")
        };
        let value: Value = serde_json::from_str(text.as_ref()).unwrap();
        assert_eq!(value.get("action").and_then(Value::as_str), Some("auth"));
        socket
            .send(Message::Text(
                json!([{"ev":"status","status":"auth_success","message":"authenticated"}])
                    .to_string()
                    .into(),
            ))
            .await
            .unwrap();
    }

    async fn acknowledge(
        socket: &mut tokio_tungstenite::WebSocketStream<tokio::net::TcpStream>,
        action: &str,
    ) {
        let Message::Text(text) = socket.next().await.unwrap().unwrap() else {
            panic!("expected Massive control message")
        };
        let value: Value = serde_json::from_str(text.as_ref()).unwrap();
        assert_eq!(value.get("action").and_then(Value::as_str), Some(action));
        socket
            .send(Message::Text(
                json!([{"ev":"status","status":"success","message":format!("{action}d") }])
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
            authenticate(&mut first).await;
            acknowledge(&mut first, "subscribe").await;

            let (second_transport, _) = listener.accept().await.unwrap();
            let mut second = accept_async(second_transport).await.unwrap();
            authenticate(&mut second).await;
            acknowledge(&mut second, "subscribe").await;
            assert!(matches!(
                first.next().await,
                Some(Ok(Message::Close(_))) | None
            ));
            acknowledge(&mut second, "unsubscribe").await;
        });

        let mut connection = MassiveStocksWebSocketConnection::new(
            crate::ConnectionKey::new("massive-market-test").unwrap(),
            MassiveWebSocketConfig {
                environment: "test".into(),
                endpoint: format!("ws://{address}"),
                api_key: SecretString::from("test-key"),
                event_capacity: 16,
            },
        )
        .unwrap();
        connection.connect().await.unwrap();
        let request =
            || MarketSubscriptionRequest::new(vec![feed(MarketDataKind::Quote, None)]).unwrap();
        let first = match connection.subscribe(request()).await.unwrap() {
            MarketSubscriptionOutcome::Confirmed(subscription) => subscription,
            other => panic!("unexpected first subscription outcome: {other:?}"),
        };
        let second = match connection.subscribe(request()).await.unwrap() {
            MarketSubscriptionOutcome::Confirmed(subscription) => subscription,
            other => panic!("unexpected second subscription outcome: {other:?}"),
        };
        assert_eq!(connection.physical.values().copied().sum::<usize>(), 2);
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
