//! Aeron consumer for Reference change notifications.

use std::collections::VecDeque;
use std::ffi::CString;
use std::sync::{Arc, Mutex};

use aeron::aeron::Aeron;
use aeron::concurrent::atomic_buffer::{AlignedBuffer, AtomicBuffer};
use aeron::context::Context;
use aeron::publication::Publication;
use aeron::subscription::Subscription;

use crate::application::market::protocol::MarketEventPublisher;
use crate::application::market::protocol::ReferenceChangeSource;
use crate::application::market::wire::{decode_reference_changed, ReferenceChangeNotice};
use crate::domain::observations::MarketObservation;
use kairos_protocol::generated::kairos::common::v_1::{
    Decimal64, MessageHeader, MessageHeaderArgs,
};
use kairos_protocol::generated::kairos::market::v_1::{
    finish_quote_message_buffer, finish_trade_message_buffer, Quote as FbQuote,
    QuoteArgs as FbQuoteArgs, QuoteMessage, QuoteMessageArgs, Trade as FbTrade,
    TradeArgs as FbTradeArgs, TradeMessage, TradeMessageArgs,
};

pub struct AeronReferenceChangeSource {
    _aeron: Aeron,
    subscription: Arc<Mutex<Subscription>>,
    queue: VecDeque<ReferenceChangeNotice>,
}

impl AeronReferenceChangeSource {
    pub fn connect(aeron_dir: Option<&str>, channel: &str, stream_id: i32) -> Result<Self, String> {
        let mut context = Context::new();
        if let Some(directory) = aeron_dir {
            context.set_aeron_dir(directory.to_string());
        }
        let mut aeron = Aeron::new(context).map_err(|error| format!("connect Aeron: {error:?}"))?;
        let channel = CString::new(channel).map_err(|error| error.to_string())?;
        let registration_id = aeron
            .add_subscription(channel, stream_id)
            .map_err(|error| format!("add Reference subscription: {error:?}"))?;
        let subscription = wait_for_subscription(&mut aeron, registration_id)?;
        Ok(Self {
            _aeron: aeron,
            subscription,
            queue: VecDeque::new(),
        })
    }

    fn poll_frames(&mut self) -> Result<(), String> {
        let mut frames = Vec::new();
        self.subscription
            .lock()
            .map_err(|_| "Aeron subscription mutex poisoned".to_string())?
            .poll(
                &mut |buffer, offset, length, _header| {
                    let mut payload = vec![0_u8; length as usize];
                    buffer.get_bytes(offset, payload.as_mut_ptr(), length);
                    frames.push(payload);
                },
                64,
            );
        for frame in frames {
            self.queue.push_back(decode_reference_changed(&frame)?);
        }
        Ok(())
    }
}

impl ReferenceChangeSource for AeronReferenceChangeSource {
    fn next_change(&mut self) -> Result<Option<ReferenceChangeNotice>, String> {
        self.poll_frames()?;
        Ok(self.queue.pop_front())
    }
}

fn wait_for_subscription(
    aeron: &mut Aeron,
    registration_id: i64,
) -> Result<Arc<Mutex<Subscription>>, String> {
    for _ in 0..10_000 {
        if let Ok(subscription) = aeron.find_subscription(registration_id) {
            return Ok(subscription);
        }
        std::thread::yield_now();
    }
    Err(format!(
        "Reference Aeron subscription {registration_id} was not acknowledged"
    ))
}

pub struct AeronMarketEventPublisher {
    _aeron: Aeron,
    quotes: Arc<Mutex<Publication>>,
    trades: Arc<Mutex<Publication>>,
    quote_buffer: AlignedBuffer,
    trade_buffer: AlignedBuffer,
    actor_id: String,
    stream_id: String,
}

impl AeronMarketEventPublisher {
    pub fn connect(
        aeron_dir: Option<&str>,
        channel: &str,
        quote_stream_id: i32,
        trade_stream_id: i32,
        actor_id: impl Into<String>,
        stream_id: impl Into<String>,
    ) -> Result<Self, String> {
        let mut context = Context::new();
        if let Some(directory) = aeron_dir {
            context.set_aeron_dir(directory.to_string());
        }
        let mut aeron = Aeron::new(context).map_err(|error| format!("connect Aeron: {error:?}"))?;
        let channel = CString::new(channel).map_err(|error| error.to_string())?;
        let quote_registration = aeron
            .add_publication(channel.clone(), quote_stream_id)
            .map_err(|error| format!("add quote publication: {error:?}"))?;
        let trade_registration = aeron
            .add_publication(channel, trade_stream_id)
            .map_err(|error| format!("add trade publication: {error:?}"))?;
        let quotes = wait_for_publication(&mut aeron, quote_registration)?;
        let trades = wait_for_publication(&mut aeron, trade_registration)?;
        Ok(Self {
            _aeron: aeron,
            quotes,
            trades,
            quote_buffer: AlignedBuffer::with_capacity(4 * 1024 * 1024),
            trade_buffer: AlignedBuffer::with_capacity(4 * 1024 * 1024),
            actor_id: actor_id.into(),
            stream_id: stream_id.into(),
        })
    }
}

impl MarketEventPublisher for AeronMarketEventPublisher {
    fn publish(
        &mut self,
        event_sequence: u64,
        observation: &MarketObservation,
    ) -> Result<(), String> {
        match observation {
            MarketObservation::Quote(value) => {
                let mut builder = flatbuffers::FlatBufferBuilder::new();
                let header = header(
                    &mut builder,
                    &self.actor_id,
                    &self.stream_id,
                    event_sequence,
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
                offer(&self.quotes, &self.quote_buffer, builder.finished_data())
            }
            MarketObservation::Trade(value) => {
                let mut builder = flatbuffers::FlatBufferBuilder::new();
                let header = header(
                    &mut builder,
                    &self.actor_id,
                    &self.stream_id,
                    event_sequence,
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
                offer(&self.trades, &self.trade_buffer, builder.finished_data())
            }
        }
    }
}

fn wait_for_publication(
    aeron: &mut Aeron,
    registration_id: i64,
) -> Result<Arc<Mutex<Publication>>, String> {
    for _ in 0..10_000 {
        if let Ok(publication) = aeron.find_publication(registration_id) {
            return Ok(publication);
        }
        std::thread::yield_now();
    }
    Err(format!(
        "Aeron publication {registration_id} was not acknowledged"
    ))
}

fn offer(
    publication: &Arc<Mutex<Publication>>,
    buffer: &AlignedBuffer,
    payload: &[u8],
) -> Result<(), String> {
    let atomic = AtomicBuffer::from_aligned(buffer);
    atomic.put_bytes(0, payload);
    for _ in 0..10_000 {
        if publication
            .lock()
            .map_err(|_| "publication mutex poisoned".to_string())?
            .offer_part(atomic, 0, payload.len() as i32)
            .is_ok()
        {
            return Ok(());
        }
        std::thread::yield_now();
    }
    Err("Aeron publication remained back-pressured".into())
}

fn header<'a, A: flatbuffers::Allocator + 'a>(
    builder: &mut flatbuffers::FlatBufferBuilder<'a, A>,
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
