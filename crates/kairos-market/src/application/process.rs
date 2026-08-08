//! Market process runtime and strategy command boundary.

use crate::application::MarketApplication;
use crate::domain::observations::MarketObservation;
use crate::services::publication::MmapMarketSnapshotPublisher;
use crate::services::reference::resolve_market;
use crate::{MarketDescriptor, SubscriptionId};
use flatbuffers::FlatBufferBuilder;
use kairos_protocol::generated::kairos::common::v_1::{
    Decimal64, MessageHeader, MessageHeaderArgs,
};
use kairos_protocol::generated::kairos::market::v_1::{
    finish_quote_message_buffer, finish_trade_message_buffer, Quote as FbQuote,
    QuoteArgs as FbQuoteArgs, QuoteMessage, QuoteMessageArgs, Trade as FbTrade,
    TradeArgs as FbTradeArgs, TradeMessage, TradeMessageArgs,
};
use kairos_protocol::InstanceIdentity;
use kairos_workspace::runtime::{HEALTH_PATH, SNAPSHOT_PATH, STOP_PATH};
use serde::Deserialize;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{UnixListener, UnixStream};
use tokio::sync::mpsc::{self, UnboundedReceiver, UnboundedSender};
use tokio::time::{self, MissedTickBehavior};

#[derive(Debug, Deserialize)]
struct SubscribePayload {
    subject: String,
    selectors: Vec<String>,
    exchange: Option<String>,
    market_type: Option<String>,
    #[serde(default)]
    asset_type: Option<String>,
    identity: Option<String>,
    dynamic: bool,
}

#[derive(Debug, Deserialize)]
struct CommandEnvelope<T> {
    schema_version: u16,
    command_id: String,
    idempotency_key: String,
    operation: String,
    strategy_id: String,
    instance_id: String,
    payload: T,
}

#[derive(Debug, Deserialize)]
struct SubscribeRequest {
    request_id: String,
    strategy_id: String,
    instance_id: String,
    subject: String,
    selectors: Vec<String>,
    exchange: Option<String>,
    market_type: Option<String>,
    #[serde(default)]
    asset_type: Option<String>,
    identity: Option<String>,
    dynamic: bool,
}

#[derive(Debug, Deserialize)]
struct UnsubscribePayload {
    subscription_id: String,
}

pub struct MarketProcess {
    application: MarketApplication,
    publisher: MmapMarketSnapshotPublisher,
    socket_path: PathBuf,
    event_socket_path: PathBuf,
    event_sender: UnboundedSender<Vec<u8>>,
    event_receiver: UnboundedReceiver<Vec<u8>>,
    event_actor_id: String,
    event_identity: InstanceIdentity,
    interval: Duration,
    feed_enabled: bool,
    stop_requested: bool,
    reference_socket_path: Option<PathBuf>,
}

impl MarketProcess {
    pub fn new(
        application: MarketApplication,
        publisher: MmapMarketSnapshotPublisher,
        socket_path: impl Into<PathBuf>,
        event_socket_path: impl Into<PathBuf>,
        interval: Duration,
        feed_enabled: bool,
    ) -> Result<Self, String> {
        Self::new_with_identity(
            application,
            publisher,
            socket_path,
            event_socket_path,
            interval,
            feed_enabled,
            InstanceIdentity::default(),
        )
    }

    pub fn new_with_identity(
        application: MarketApplication,
        publisher: MmapMarketSnapshotPublisher,
        socket_path: impl Into<PathBuf>,
        event_socket_path: impl Into<PathBuf>,
        interval: Duration,
        feed_enabled: bool,
        identity: InstanceIdentity,
    ) -> Result<Self, String> {
        if interval.is_zero() {
            return Err("market process interval must be positive".into());
        }
        let (event_sender, event_receiver) = mpsc::unbounded_channel();
        let event_actor_id = application.snapshot().actor_id;
        Ok(Self {
            application,
            publisher,
            socket_path: socket_path.into(),
            event_socket_path: event_socket_path.into(),
            event_sender,
            event_receiver,
            event_actor_id,
            event_identity: identity,
            interval,
            feed_enabled,
            stop_requested: false,
            reference_socket_path: None,
        })
    }

    pub fn with_reference_socket(mut self, path: impl Into<PathBuf>) -> Self {
        self.reference_socket_path = Some(path.into());
        self
    }

    pub async fn run(mut self) -> Result<(), Box<dyn std::error::Error>> {
        remove_socket(&self.socket_path)?;
        remove_socket(&self.event_socket_path)?;
        if let Some(parent) = self.socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        if let Some(parent) = self.event_socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let listener = UnixListener::bind(&self.socket_path)?;
        let event_listener = UnixListener::bind(&self.event_socket_path)?;
        let mut event_clients = Vec::new();
        self.publish_snapshot()?;
        let mut ticks = time::interval(self.interval);
        ticks.set_missed_tick_behavior(MissedTickBehavior::Delay);
        while !self.stop_requested {
            tokio::select! {
                accepted = listener.accept() => {
                    let (stream, _) = accepted?;
                    self.handle(stream).await?;
                }
                accepted = event_listener.accept() => {
                    let (stream, _) = accepted?;
                    event_clients.push(stream);
                }
                Some(payload) = self.event_receiver.recv() => {
                    let mut failed = Vec::new();
                    for (index, client) in event_clients.iter_mut().enumerate() {
                        let frame = (payload.len() as u32).to_be_bytes();
                        if client.write_all(&frame).await.is_err()
                            || client.write_all(&payload).await.is_err()
                        {
                            failed.push(index);
                        }
                    }
                    for index in failed.into_iter().rev() {
                        event_clients.remove(index);
                    }
                }
                _ = ticks.tick() => {
                    if self.feed_enabled && !self.application.snapshot().subscriptions.is_empty() {
                        let error = std::thread::scope(|scope| {
                            match scope.spawn(|| self.application.poll_feed()).join() {
                                Ok(Ok(_)) => None,
                                Ok(Err(error)) => Some(error.to_string()),
                                Err(_) => Some("market feed poll thread panicked".into()),
                            }
                        });
                        if let Some(error) = error {
                            eprintln!(
                                "{{\"level\":\"error\",\"component\":\"market\",\"message\":\"market feed poll failed\",\"error\":{}}}",
                                serde_json::to_string(&error).unwrap_or_else(|_| "\"unknown\"".into())
                            );
                            let _ = std::thread::scope(|scope| {
                                scope.spawn(|| self.application.recover_feed()).join()
                            });
                        }
                    }
                    self.publish_pending_events()?;
                    self.publish_snapshot()?;
                }
            }
        }
        remove_socket(&self.socket_path)?;
        remove_socket(&self.event_socket_path)?;
        Ok(())
    }

    fn publish_snapshot(&mut self) -> Result<(), String> {
        self.publisher.publish(&self.application.snapshot())
    }

    fn publish_pending_events(&mut self) -> Result<(), String> {
        for (sequence, observation) in self.application.drain_events() {
            self.event_sender
                .send(encode_event(
                    &self.event_actor_id,
                    &self.event_identity,
                    sequence,
                    &observation,
                )?)
                .map_err(|_| "market event endpoint is closed".to_string())?;
        }
        Ok(())
    }

    async fn handle(&mut self, mut stream: UnixStream) -> Result<(), Box<dyn std::error::Error>> {
        let mut buffer = vec![0_u8; 64 * 1024];
        let size = stream.read(&mut buffer).await?;
        let request = String::from_utf8_lossy(&buffer[..size]);
        let (head, body) = request
            .split_once("\r\n\r\n")
            .unwrap_or((request.as_ref(), ""));
        let path = head
            .lines()
            .next()
            .and_then(|line| line.split_whitespace().nth(1))
            .unwrap_or("/");
        let (status, payload) = match path {
            HEALTH_PATH => (200, self.health()),
            SNAPSHOT_PATH => (200, serde_json::to_value(self.application.snapshot())?),
            "/v1/subscribe" => self.subscribe(body),
            "/v1/unsubscribe" => self.unsubscribe(body),
            "/v1/refresh" | "/v1/recover" => match std::thread::scope(|scope| {
                scope.spawn(|| self.application.recover_feed()).join()
            }) {
                Ok(Ok(())) => (202, json!({"status":"recovering"})),
                Ok(Err(error)) => (422, json!({"error": error.to_string()})),
                Err(_) => (500, json!({"error":"market recovery thread panicked"})),
            },
            STOP_PATH => {
                self.stop_requested = true;
                (202, json!({"status":"stopping"}))
            }
            _ => (404, json!({"error":"unknown market control path"})),
        };
        if let Err(error) = write_json(&mut stream, status, &payload).await {
            if error.kind() != std::io::ErrorKind::BrokenPipe {
                return Err(error.into());
            }
        }
        Ok(())
    }

    fn health(&self) -> Value {
        let snapshot = self.application.snapshot();
        json!({
            "status": match snapshot.feed_status {
                // A server without a configured feed is still available for
                // control-plane validation and subscription commands.
                crate::FeedStatus::Disconnected => "ready",
                crate::FeedStatus::Ready => "ready",
                crate::FeedStatus::Reconnecting => "reconnecting",
                crate::FeedStatus::WarmingUp => "warming_up",
                crate::FeedStatus::Degraded => "degraded",
            },
            "actor_id": snapshot.actor_id,
            "generation": snapshot.generation,
            "event_sequence": snapshot.event_sequence,
            "subscription_count": snapshot.subscriptions.len(),
            "feed_status": snapshot.feed_status,
        })
    }

    fn subscribe(&mut self, body: &str) -> (u16, Value) {
        let value: CommandEnvelope<SubscribePayload> = match serde_json::from_str(body) {
            Ok(value) => value,
            Err(error) => {
                return (
                    400,
                    json!({"error":{"code":"command.invalid_json","message":format!("invalid subscribe command: {error}"),"retryable":false}}),
                )
            }
        };
        if value.schema_version != 1
            || value.operation != "market.subscribe"
            || value.command_id.trim().is_empty()
            || value.idempotency_key.trim().is_empty()
        {
            return (
                422,
                json!({"error":{"code":"command.invalid_envelope","message":"unsupported market command schema or operation","retryable":false}}),
            );
        }
        let request = SubscribeRequest {
            request_id: value.command_id,
            strategy_id: value.strategy_id,
            instance_id: value.instance_id,
            subject: value.payload.subject,
            selectors: value.payload.selectors,
            exchange: value.payload.exchange,
            market_type: value.payload.market_type,
            asset_type: value.payload.asset_type,
            identity: value.payload.identity,
            dynamic: value.payload.dynamic,
        };
        if request.request_id.trim().is_empty()
            || request.strategy_id.trim().is_empty()
            || request.instance_id.trim().is_empty()
            || request.subject.trim().is_empty()
        {
            return (
                422,
                json!({"error":"request_id, strategy_id, instance_id and subject are required"}),
            );
        }
        if request.dynamic {
            return (
                422,
                json!({"error":"dynamic subscriptions are not supported by this process"}),
            );
        }
        let source_symbol = request
            .subject
            .strip_prefix("market.")
            .unwrap_or(&request.subject)
            .to_owned();
        let market_id = request
            .identity
            .clone()
            .unwrap_or_else(|| source_symbol.clone());
        let venue = request.exchange.clone().unwrap_or_else(|| "binance".into());
        let market_type = request.market_type.clone().unwrap_or_else(|| "spot".into());
        let descriptor_result = if let Some(reference_socket) = &self.reference_socket_path {
            resolve_market(
                reference_socket,
                &venue,
                &market_type,
                request.asset_type.as_deref(),
                &source_symbol,
            )
        } else {
            match request.asset_type {
                Some(asset_type) => MarketDescriptor::new_with_asset_type(
                    market_id,
                    source_symbol.clone(),
                    venue,
                    market_type,
                    asset_type,
                    source_symbol.clone(),
                ),
                None => MarketDescriptor::new(
                    market_id.clone(),
                    source_symbol.clone(),
                    venue.clone(),
                    market_type.clone(),
                    source_symbol.clone(),
                )
                .and_then(|descriptor| {
                    if venue == "binance" && market_type == "spot" {
                        MarketDescriptor::new_with_asset_type(
                            descriptor.market_id,
                            descriptor.instrument_id,
                            descriptor.venue_id,
                            descriptor.market_type,
                            "crypto",
                            descriptor.source_symbol,
                        )
                    } else {
                        Ok(descriptor)
                    }
                }),
            }
        };
        let descriptor = match descriptor_result {
            Ok(value) => value,
            Err(error) => return (422, json!({"error": error})),
        };
        let subscription_id = match SubscriptionId::new(request.request_id.clone()) {
            Ok(value) => value,
            Err(error) => return (422, json!({"error": error})),
        };
        let result = std::thread::scope(|scope| {
            scope
                .spawn(|| {
                    self.application.subscribe_static(
                        subscription_id,
                        request.strategy_id,
                        descriptor,
                    )
                })
                .join()
        });
        let command_id = request.request_id.clone();
        let selectors = request.selectors.clone();
        match result {
            Err(_) => (
                500,
                json!({
                    "schema_version": 1,
                    "status": "failed",
                    "error": {"code": "market.internal", "message": "market subscription thread panicked", "retryable": true}
                }),
            ),
            Ok(Err(error)) => (
                422,
                json!({
                    "schema_version": 1,
                    "command_id": command_id,
                    "request_id": request.request_id,
                    "status": "rejected",
                    "error": {"code": "market.subscription_rejected", "message": error.to_string(), "retryable": false}
                }),
            ),
            Ok(Ok(())) => (
                202,
                json!({
                    "schema_version": 1,
                    "command_id": command_id,
                    "request_id": request.request_id,
                    "instance_id": request.instance_id,
                    "status": "accepted",
                    "result": {
                        "subscription_id": request.request_id.clone(),
                        "selectors": selectors.clone(),
                    },
                    "subscription_id": request.request_id,
                    "selectors": selectors,
                }),
            ),
        }
    }

    fn unsubscribe(&mut self, body: &str) -> (u16, Value) {
        let value: CommandEnvelope<UnsubscribePayload> = match serde_json::from_str(body) {
            Ok(value) => value,
            Err(error) => {
                return (
                    400,
                    json!({"error":{"code":"command.invalid_json","message":format!("invalid unsubscribe command: {error}"),"retryable":false}}),
                )
            }
        };
        if value.schema_version != 1
            || value.operation != "market.unsubscribe"
            || value.command_id.trim().is_empty()
            || value.idempotency_key.trim().is_empty()
        {
            return (
                422,
                json!({"error":{"code":"command.invalid_envelope","message":"unsupported market command schema or operation","retryable":false}}),
            );
        }
        let request_id = value.command_id;
        let subscription_id = value.payload.subscription_id;
        let id = match SubscriptionId::new(subscription_id) {
            Ok(value) => value,
            Err(error) => return (422, json!({"error": error})),
        };
        if self.application.unsubscribe(&id) {
            (
                202,
                json!({"schema_version":1, "command_id":request_id, "request_id": request_id, "status":"accepted"}),
            )
        } else {
            (
                404,
                json!({"schema_version":1, "command_id":request_id, "request_id": request_id, "status":"rejected", "error":{"code":"market.subscription_not_found","message":"subscription not found","retryable":false}}),
            )
        }
    }
}

fn encode_event(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    observation: &MarketObservation,
) -> Result<Vec<u8>, String> {
    let stream_id = "market.events";
    match observation {
        MarketObservation::Quote(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos,
            );
            let instrument_id = builder.create_string(&value.instrument_id);
            let market_id = builder.create_string(&value.market_id);
            let source_id = builder.create_string(&value.source_id);
            let bid_price = decimal64(value.bid_price.as_deref());
            let bid_quantity = decimal64(value.bid_quantity.as_deref());
            let ask_price = decimal64(value.ask_price.as_deref());
            let ask_quantity = decimal64(value.ask_quantity.as_deref());
            let quote = FbQuote::create(
                &mut builder,
                &FbQuoteArgs {
                    instrument_id: Some(instrument_id),
                    market_id: Some(market_id),
                    bid_price: bid_price.as_ref(),
                    bid_quantity: bid_quantity.as_ref(),
                    ask_price: ask_price.as_ref(),
                    ask_quantity: ask_quantity.as_ref(),
                    event_time_unix_nanos: value.observed_at_unix_nanos,
                    source_id: Some(source_id),
                    ..Default::default()
                },
            );
            let root = QuoteMessage::create(
                &mut builder,
                &QuoteMessageArgs {
                    header: Some(header),
                    payload: Some(quote),
                },
            );
            finish_quote_message_buffer(&mut builder, root);
            Ok(builder.finished_data().to_vec())
        }
        MarketObservation::Trade(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos,
            );
            let instrument_id = builder.create_string(&value.instrument_id);
            let market_id = builder.create_string(&value.market_id);
            let source_id = builder.create_string(&value.source_id);
            let price = decimal64(Some(&value.price))
                .ok_or_else(|| "trade price is not decimal".to_string())?;
            let quantity = decimal64(Some(&value.quantity))
                .ok_or_else(|| "trade quantity is not decimal".to_string())?;
            let trade_id = value.trade_id.as_ref().map(|id| builder.create_string(id));
            let trade = FbTrade::create(
                &mut builder,
                &FbTradeArgs {
                    trade_id,
                    instrument_id: Some(instrument_id),
                    market_id: Some(market_id),
                    price: Some(&price),
                    quantity: Some(&quantity),
                    event_time_unix_nanos: value.observed_at_unix_nanos,
                    source_id: Some(source_id),
                    ..Default::default()
                },
            );
            let root = TradeMessage::create(
                &mut builder,
                &TradeMessageArgs {
                    header: Some(header),
                    payload: Some(trade),
                },
            );
            finish_trade_message_buffer(&mut builder, root);
            Ok(builder.finished_data().to_vec())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::MarketProcess;
    use crate::application::protocol::{MarketFeed, MarketOrderBookUpdate};
    use crate::composition::{CompositeMarketFeed, MarketFeedFactory, MarketRoute};
    use crate::domain::freshness::FeedStatus;
    use crate::domain::market::MarketDescriptor;
    use crate::domain::observations::MarketObservation;
    use crate::services::actor::MarketActor;
    use crate::services::publication::MmapMarketSnapshotPublisher;
    use crate::{MarketApplication, SubscriptionId};
    use serde_json::json;
    use std::collections::BTreeMap;
    use std::time::Duration;

    struct FakeFeed {
        next: u64,
    }

    impl MarketFeed for FakeFeed {
        fn subscribe(&mut self, _market: &MarketDescriptor) -> Result<SubscriptionId, String> {
            let id = SubscriptionId::new(format!("fake:{}", self.next))?;
            self.next += 1;
            Ok(id)
        }

        fn unsubscribe(&mut self, _subscription: &SubscriptionId) -> Result<(), String> {
            Ok(())
        }

        fn poll(&mut self) -> Result<Vec<MarketObservation>, String> {
            Ok(Vec::new())
        }

        fn poll_orderbooks(&mut self) -> Result<Vec<MarketOrderBookUpdate>, String> {
            Ok(Vec::new())
        }

        fn status(&self) -> FeedStatus {
            FeedStatus::Ready
        }
    }

    #[test]
    fn command_endpoint_accepts_all_required_product_routes_in_one_process() {
        let routes = [
            ("massive", "equity", "equity"),
            ("massive", "options", "equity"),
            ("binance", "spot", "crypto"),
            ("binance", "usd-m-futures", "crypto"),
            ("binance", "coin-m-futures", "crypto"),
            ("binance", "options", "crypto"),
            ("binance", "equity", "equity"),
            ("okx", "spot", "crypto"),
            ("okx", "spot", "equity"),
            ("okx", "swap", "crypto"),
            ("okx", "futures", "crypto"),
            ("okx", "options", "crypto"),
        ];
        let mut factories: BTreeMap<MarketRoute, MarketFeedFactory> = BTreeMap::new();
        for (venue, market_type, asset_type) in routes {
            factories.insert(
                MarketRoute::with_asset_type(venue, market_type, asset_type),
                Box::new(|| Ok(Box::new(FakeFeed { next: 1 }) as Box<dyn MarketFeed>)),
            );
        }
        let mut application = MarketApplication::new(MarketActor::new("test-market", 100).unwrap());
        application.attach_feed(Box::new(CompositeMarketFeed::new(factories).unwrap()));
        let directory = tempfile::tempdir().unwrap();
        let publisher = MmapMarketSnapshotPublisher::create(
            directory.path().join("market.snapshot"),
            4096,
            "test-market",
            "market.events",
        )
        .unwrap();
        let mut process = MarketProcess::new(
            application,
            publisher,
            directory.path().join("market.sock"),
            directory.path().join("market-events.sock"),
            Duration::from_secs(1),
            true,
        )
        .unwrap();

        for (index, (venue, market_type, asset_type)) in routes.into_iter().enumerate() {
            let body = json!({
                "schema_version": 1,
                "command_id": format!("command-{index}"),
                "idempotency_key": format!("command-{index}"),
                "operation": "market.subscribe",
                "strategy_id": "all-products",
                "instance_id": "instance-1",
                "payload": {
                    "subject": format!("market.SYMBOL{index}"),
                    "selectors": ["quote"],
                    "exchange": venue,
                    "market_type": market_type,
                    "asset_type": asset_type,
                    "identity": null,
                    "dynamic": false,
                }
            })
            .to_string();
            let (status, _) = process.subscribe(&body);
            assert_eq!(status, 202, "route {venue}/{market_type}/{asset_type}");
        }
        assert_eq!(
            process.application.snapshot().subscriptions.len(),
            routes.len()
        );
    }
}

fn event_header<'a, A: flatbuffers::Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    actor_id: &str,
    identity: &InstanceIdentity,
    stream_id: &str,
    sequence: u64,
    event_time: u64,
) -> flatbuffers::WIPOffset<MessageHeader<'a>> {
    let message_id = builder.create_string(&format!("market:{sequence}"));
    let stream = builder.create_string(stream_id);
    let actor = builder.create_string(actor_id);
    let workspace_id = non_empty_string(builder, &identity.workspace_id);
    let launch_id = non_empty_string(builder, &identity.launch_id);
    let instance_id = non_empty_string(builder, &identity.instance_id);
    MessageHeader::create(
        builder,
        &MessageHeaderArgs {
            message_id: Some(message_id),
            stream_id: Some(stream),
            producer_id: Some(actor),
            workspace_id,
            launch_id,
            instance_id,
            sequence,
            event_time_unix_nanos: event_time,
            publish_time_unix_nanos: event_time,
        },
    )
}

fn decimal64(value: Option<&str>) -> Option<Decimal64> {
    let value = value?;
    let (whole, fraction) = value.split_once('.').unwrap_or((value, ""));
    Some(Decimal64::new(
        format!("{whole}{fraction}").parse().ok()?,
        fraction.len() as u8,
    ))
}

fn non_empty_string<'a, 'b, A: flatbuffers::Allocator + 'a>(
    builder: &'b mut FlatBufferBuilder<'a, A>,
    value: &str,
) -> Option<flatbuffers::WIPOffset<&'a str>> {
    (!value.is_empty()).then(|| builder.create_string(value))
}

async fn write_json(
    stream: &mut UnixStream,
    status: u16,
    value: &Value,
) -> Result<(), std::io::Error> {
    let body = serde_json::to_vec(value).map_err(std::io::Error::other)?;
    let reason = if status == 200 {
        "OK"
    } else if status == 202 {
        "Accepted"
    } else if status == 404 {
        "Not Found"
    } else {
        "Unprocessable Entity"
    };
    let header = format!("HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len());
    stream.write_all(header.as_bytes()).await?;
    stream.write_all(&body).await?;
    stream.shutdown().await
}

fn remove_socket(path: &Path) -> Result<(), std::io::Error> {
    match std::fs::symlink_metadata(path) {
        Ok(_) => std::fs::remove_file(path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}
