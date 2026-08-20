use std::collections::{BTreeMap, VecDeque};
use std::task::{Context, Poll};
use std::time::Duration;

use secrecy::ExposeSecret;
use serde_json::{Value, json};

use super::MassiveWebSocketConfig;
use crate::services::participants::massive::market::data::{SocketService, normalize};
use crate::{
    ConnectionDescriptor, ConnectionHealth, ConnectionHealthQuery, ConnectionLifecycleCommand,
    IntegrationError, MarketDataKind, MarketDataStream, MarketDelivery, MarketEvent, MarketFeed,
    MarketSubscription, MarketSubscriptionCommand, MarketSubscriptionId, MarketSubscriptionOutcome,
    MarketSubscriptionRequest, ParticipantKind, ParticipantRef, ParticipantRejection,
};

#[derive(Clone, Copy)]
enum SocketProduct {
    Standard,
    Indices,
    Forex,
    Crypto,
}

macro_rules! websocket_connection {
    ($name:ident, $domain:literal, $product:expr) => {
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
                    .map(|feed| feed_parameter(feed, $product))
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
                            Ok(Some(event)) => {
                                if let Err(error) = self.buffer(event) {
                                    return Poll::Ready(Err(error));
                                }
                            }
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
    SocketProduct::Standard
);
websocket_connection!(
    MassiveOptionsWebSocketConnection,
    "options.websocket",
    SocketProduct::Standard
);
websocket_connection!(
    MassiveFuturesWebSocketConnection,
    "futures.websocket",
    SocketProduct::Standard
);
websocket_connection!(
    MassiveIndicesWebSocketConnection,
    "indices.websocket",
    SocketProduct::Indices
);
websocket_connection!(
    MassiveForexWebSocketConnection,
    "forex.websocket",
    SocketProduct::Forex
);
websocket_connection!(
    MassiveCryptoWebSocketConnection,
    "crypto.websocket",
    SocketProduct::Crypto
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

fn feed_parameter(feed: &MarketFeed, product: SocketProduct) -> Result<String, IntegrationError> {
    let symbol = feed.symbol.as_ref().ok_or_else(|| {
        IntegrationError::InvalidRequest(format!("Massive {:?} feed requires symbol", feed.kind))
    })?;
    let channel = match (product, feed.kind) {
        (SocketProduct::Indices, MarketDataKind::IndexPrice) => "V",
        (SocketProduct::Indices, MarketDataKind::Bar) => match feed.interval.as_deref() {
            Some("1s") => "A",
            Some("1m") | None => "AM",
            Some(interval) => {
                return Err(IntegrationError::InvalidRequest(format!(
                    "Massive live index aggregate interval is unsupported: {interval}"
                )));
            },
        },
        (SocketProduct::Forex, MarketDataKind::Quote) => "C",
        (SocketProduct::Forex, MarketDataKind::Bar) => match feed.interval.as_deref() {
            Some("1s") => "CAS",
            Some("1m") | None => "CA",
            Some(interval) => {
                return Err(IntegrationError::InvalidRequest(format!(
                    "Massive live forex aggregate interval is unsupported: {interval}"
                )));
            },
        },
        (SocketProduct::Crypto, MarketDataKind::Quote) => "XQ",
        (SocketProduct::Crypto, MarketDataKind::Trade) => "XT",
        (SocketProduct::Crypto, MarketDataKind::Bar) => match feed.interval.as_deref() {
            Some("1s") => "XAS",
            Some("1m") | None => "XA",
            Some(interval) => {
                return Err(IntegrationError::InvalidRequest(format!(
                    "Massive live crypto aggregate interval is unsupported: {interval}"
                )));
            },
        },
        (SocketProduct::Standard, MarketDataKind::Quote) => "Q",
        (SocketProduct::Standard, MarketDataKind::Trade) => "T",
        (
            SocketProduct::Standard,
            MarketDataKind::Bar | MarketDataKind::TradeBar | MarketDataKind::QuoteBar,
        ) => match feed.interval.as_deref() {
            Some("1s") => "A",
            Some("1m") | None => "AM",
            Some(interval) => {
                return Err(IntegrationError::InvalidRequest(format!(
                    "Massive live aggregate interval is unsupported: {interval}"
                )));
            },
        },
        (_, unsupported) => {
            return Err(IntegrationError::InvalidRequest(format!(
                "Massive WebSocket does not support {unsupported:?}"
            )));
        },
    };
    Ok(format!(
        "{channel}.{}",
        symbol.as_str().to_ascii_uppercase()
    ))
}

#[cfg(test)]
mod tests {
    use kairos_primitives::integration::ParticipantSymbol;

    use super::{SocketProduct, feed_parameter};
    use crate::{MarketDataKind, MarketFeed};

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
            feed_parameter(
                &feed(MarketDataKind::IndexPrice, None),
                SocketProduct::Indices
            )
            .unwrap(),
            "V.I:SPX"
        );
        assert_eq!(
            feed_parameter(
                &feed(MarketDataKind::Bar, Some("1s")),
                SocketProduct::Indices
            )
            .unwrap(),
            "A.I:SPX"
        );
        assert_eq!(
            feed_parameter(
                &feed(MarketDataKind::Bar, Some("1m")),
                SocketProduct::Indices
            )
            .unwrap(),
            "AM.I:SPX"
        );
    }

    #[test]
    fn indices_reject_equity_quote_channels() {
        assert!(
            feed_parameter(&feed(MarketDataKind::Quote, None), SocketProduct::Indices).is_err()
        );
    }

    #[test]
    fn currency_products_use_product_specific_channels() {
        assert_eq!(
            feed_parameter(&feed(MarketDataKind::Quote, None), SocketProduct::Forex).unwrap(),
            "C.I:SPX"
        );
        assert_eq!(
            feed_parameter(&feed(MarketDataKind::Bar, Some("1s")), SocketProduct::Forex).unwrap(),
            "CAS.I:SPX"
        );
        assert_eq!(
            feed_parameter(&feed(MarketDataKind::Trade, None), SocketProduct::Crypto).unwrap(),
            "XT.I:SPX"
        );
        assert_eq!(
            feed_parameter(
                &feed(MarketDataKind::Bar, Some("1m")),
                SocketProduct::Crypto
            )
            .unwrap(),
            "XA.I:SPX"
        );
    }
}
