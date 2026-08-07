//! Market process runtime and strategy command boundary.

use crate::application::market::protocol::MarketEventPublisher;
use crate::application::market::MarketApplication;
use crate::domain::observations::MarketObservation;
use crate::{MarketDescriptor, MmapMarketSnapshotPublisher, SubscriptionId};
use flatbuffers::FlatBufferBuilder;
use kairos_protocol::generated::kairos::common::v_1::{
    Decimal64, MessageHeader, MessageHeaderArgs,
};
use kairos_protocol::generated::kairos::market::v_1::{
    finish_quote_message_buffer, finish_trade_message_buffer, Quote as FbQuote,
    QuoteArgs as FbQuoteArgs, QuoteMessage, QuoteMessageArgs, Trade as FbTrade,
    TradeArgs as FbTradeArgs, TradeMessage, TradeMessageArgs,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{UnixListener, UnixStream};
use tokio::sync::mpsc::{self, UnboundedReceiver, UnboundedSender};
use tokio::time::{self, MissedTickBehavior};

#[derive(Debug, Deserialize)]
struct SubscribeRequest {
    request_id: String,
    strategy_id: String,
    instance_id: String,
    subject: String,
    selectors: Vec<String>,
    exchange: Option<String>,
    market_type: Option<String>,
    identity: Option<String>,
    dynamic: bool,
}

#[derive(Debug, Deserialize)]
struct UnsubscribeRequest {
    request_id: String,
    subscription_id: String,
}

pub struct MarketProcess {
    application: MarketApplication,
    publisher: MmapMarketSnapshotPublisher,
    socket_path: PathBuf,
    event_socket_path: PathBuf,
    event_receiver: UnboundedReceiver<Vec<u8>>,
    interval: Duration,
    feed_enabled: bool,
    stop_requested: bool,
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
        if interval.is_zero() {
            return Err("market process interval must be positive".into());
        }
        let (event_sender, event_receiver) = mpsc::unbounded_channel();
        let actor_id = application.snapshot().actor_id;
        let mut application = application;
        application.attach_event_publisher(Box::new(UnixMarketEventPublisher {
            sender: event_sender,
            actor_id,
        }));
        Ok(Self {
            application,
            publisher,
            socket_path: socket_path.into(),
            event_socket_path: event_socket_path.into(),
            event_receiver,
            interval,
            feed_enabled,
            stop_requested: false,
        })
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
                    if self.feed_enabled {
                        self.application.poll_feed().map_err(|error| format!("market feed failed: {error}"))?;
                    }
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
            "/v1/health" => (200, self.health()),
            "/v1/snapshot" => (200, serde_json::to_value(self.application.snapshot())?),
            "/v1/subscribe" => self.subscribe(body),
            "/v1/unsubscribe" => self.unsubscribe(body),
            "/v1/refresh" | "/v1/recover" => match self.application.recover_feed() {
                Ok(()) => (202, json!({"status":"recovering"})),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            "/v1/stop" => {
                self.stop_requested = true;
                (202, json!({"status":"stopping"}))
            }
            _ => (404, json!({"error":"unknown market control path"})),
        };
        write_json(&mut stream, status, &payload).await?;
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
        let request: SubscribeRequest = match serde_json::from_str(body) {
            Ok(value) => value,
            Err(error) => {
                return (
                    400,
                    json!({"error": format!("invalid subscribe request: {error}")}),
                )
            }
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
        let market_id = request
            .identity
            .clone()
            .unwrap_or_else(|| request.subject.clone());
        let venue = request.exchange.clone().unwrap_or_else(|| "unknown".into());
        let market_type = request.market_type.clone().unwrap_or_else(|| "spot".into());
        let descriptor = match MarketDescriptor::new(
            market_id,
            request.subject.clone(),
            venue,
            market_type,
            request.subject.clone(),
        ) {
            Ok(value) => value,
            Err(error) => return (422, json!({"error": error})),
        };
        let subscription_id = match SubscriptionId::new(request.request_id.clone()) {
            Ok(value) => value,
            Err(error) => return (422, json!({"error": error})),
        };
        match self
            .application
            .subscribe_static(subscription_id, request.strategy_id, descriptor)
        {
            Ok(()) => (
                202,
                json!({
                    "request_id": request.request_id,
                    "instance_id": request.instance_id,
                    "status": "accepted",
                    "subscription_id": request.request_id,
                    "selectors": request.selectors,
                }),
            ),
            Err(error) => (
                422,
                json!({"request_id": request.request_id, "error": error.to_string()}),
            ),
        }
    }

    fn unsubscribe(&mut self, body: &str) -> (u16, Value) {
        let request: UnsubscribeRequest = match serde_json::from_str(body) {
            Ok(value) => value,
            Err(error) => {
                return (
                    400,
                    json!({"error": format!("invalid unsubscribe request: {error}")}),
                )
            }
        };
        let id = match SubscriptionId::new(request.subscription_id.clone()) {
            Ok(value) => value,
            Err(error) => return (422, json!({"error": error})),
        };
        if self.application.unsubscribe(&id) {
            (
                202,
                json!({"request_id": request.request_id, "status":"accepted"}),
            )
        } else {
            (
                404,
                json!({"request_id": request.request_id, "error":"subscription not found"}),
            )
        }
    }
}

struct UnixMarketEventPublisher {
    sender: UnboundedSender<Vec<u8>>,
    actor_id: String,
}

impl MarketEventPublisher for UnixMarketEventPublisher {
    fn publish(
        &mut self,
        event_sequence: u64,
        observation: &MarketObservation,
    ) -> Result<(), String> {
        self.sender
            .send(encode_event(&self.actor_id, event_sequence, observation)?)
            .map_err(|_| "market event endpoint is closed".to_string())
    }
}

fn encode_event(
    actor_id: &str,
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

fn event_header<'a, A: flatbuffers::Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    actor_id: &str,
    stream_id: &str,
    sequence: u64,
    event_time: u64,
) -> flatbuffers::WIPOffset<MessageHeader<'a>> {
    let message_id = builder.create_string(&format!("market:{sequence}"));
    let stream = builder.create_string(stream_id);
    let actor = builder.create_string(actor_id);
    MessageHeader::create(
        builder,
        &MessageHeaderArgs {
            message_id: Some(message_id),
            stream_id: Some(stream),
            producer_id: Some(actor),
            workspace_id: None,
            launch_id: None,
            instance_id: None,
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
    stream.write_all(&body).await
}

fn remove_socket(path: &Path) -> Result<(), std::io::Error> {
    match std::fs::symlink_metadata(path) {
        Ok(_) => std::fs::remove_file(path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}
