#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SnapshotEnvelope {
    pub view_key: String,
    pub producer_id: String,
    pub generation: u64,
    pub published_at_unix_nanos: u64,
    pub payload: Vec<u8>,
}

use crate::{ContractError, ContractResult};
use std::collections::BTreeMap;
use std::path::Path;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketSnapshotRead {
    pub generation: u64,
    pub quotes: Vec<crate::model::Quote>,
    pub trades: Vec<crate::model::Trade>,
    pub bars: Vec<crate::model::Bar>,
    pub trade_bars: Vec<crate::model::TradeBar>,
    pub quote_bars: Vec<crate::model::QuoteBar>,
    pub greeks: Vec<crate::model::OptionGreeks>,
    pub rates: Vec<crate::model::Rate>,
    pub ticker_24h: Vec<crate::model::Ticker24h>,
    pub mark_prices: Vec<crate::model::MarkPrice>,
    pub index_prices: Vec<crate::model::IndexPrice>,
    pub funding_rates: Vec<crate::model::FundingRate>,
    pub open_interests: Vec<crate::model::OpenInterest>,
    pub freshness: BTreeMap<String, MarketSnapshotFreshness>,
    pub instrument_statuses: Vec<crate::model::InstrumentStatus>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketSnapshotFreshness {
    pub source_id: String,
    pub market_id: String,
    pub data_kind: String,
    pub last_event_time_unix_nanos: u64,
    pub last_received_time_unix_nanos: u64,
    pub status: crate::model::DataFreshnessStatus,
}

/// Read the latest quote view from the module-owned mmap snapshot.
///
/// The FlatBuffers decode stays in the Market contract; consumers receive the
/// public `Quote` model and never need to know the generated schema layout.
pub fn read_latest_quotes(path: impl AsRef<Path>) -> ContractResult<Vec<crate::model::Quote>> {
    Ok(read_latest_market_snapshot(path)?.quotes)
}

pub fn read_latest_trades(path: impl AsRef<Path>) -> ContractResult<Vec<crate::model::Trade>> {
    Ok(read_latest_market_snapshot(path)?.trades)
}

pub fn read_latest_bars(path: impl AsRef<Path>) -> ContractResult<Vec<crate::model::Bar>> {
    Ok(read_latest_market_snapshot(path)?.bars)
}

pub fn read_latest_trade_bars(
    path: impl AsRef<Path>,
) -> ContractResult<Vec<crate::model::TradeBar>> {
    Ok(read_latest_market_snapshot(path)?.trade_bars)
}

pub fn read_latest_quote_bars(
    path: impl AsRef<Path>,
) -> ContractResult<Vec<crate::model::QuoteBar>> {
    Ok(read_latest_market_snapshot(path)?.quote_bars)
}

pub fn read_latest_greeks(
    path: impl AsRef<Path>,
) -> ContractResult<Vec<crate::model::OptionGreeks>> {
    Ok(read_latest_market_snapshot(path)?.greeks)
}

/// Read a per-view order-book snapshot published by the Market contract.
pub fn read_orderbooks(path: impl AsRef<Path>) -> ContractResult<Vec<crate::model::OrderBook>> {
    use kairos_protocol::generated::kairos::market::v_1::{
        order_book_snapshot_buffer_has_identifier, root_as_order_book_snapshot,
    };
    let reader = kairos_transport::SharedSnapshotReader::open(path)
        .map_err(|error| ContractError::Transport(error.to_string()))?;
    let payload = reader
        .read_payload()
        .map_err(ContractError::Transport)?
        .payload;
    if !order_book_snapshot_buffer_has_identifier(&payload) {
        return Err(ContractError::Invalid(
            "OrderBook snapshot has an invalid file identifier".into(),
        ));
    }
    let root = root_as_order_book_snapshot(&payload)
        .map_err(|error| ContractError::Invalid(format!("decode OrderBook snapshot: {error}")))?;
    Ok(root
        .payload()
        .books()
        .map(|books| {
            books
                .iter()
                .map(|book| crate::model::OrderBook {
                    source_id: book.source_id().unwrap_or_default().to_owned(),
                    market_id: book.market_id().to_owned(),
                    instrument_id: book.instrument_id().to_owned(),
                    sequence: book.sequence(),
                    event_time_unix_nanos: book.event_time_unix_nanos(),
                    bids: decode_levels(book.bids()),
                    asks: decode_levels(book.asks()),
                    synchronized: book.synchronized(),
                    depth_policy: decode_depth_policy(book.depth_policy()),
                    cursor: crate::model::DepthCursor {
                        first_sequence: book.first_sequence(),
                        last_sequence: book.last_sequence(),
                        checksum: book.checksum().map(ToOwned::to_owned),
                    },
                    checksum: book.checksum().map(ToOwned::to_owned),
                })
                .collect()
        })
        .unwrap_or_default())
}

/// Read the complete current Market data projection, including derivative
/// views. The legacy quote-only helpers remain as compatibility conveniences.
pub fn read_latest_market_snapshot(path: impl AsRef<Path>) -> ContractResult<MarketSnapshotRead> {
    use kairos_protocol::generated::kairos::market::v_1::{
        market_data_snapshot_buffer_has_identifier, root_as_market_data_snapshot,
    };
    let reader = kairos_transport::SharedSnapshotReader::open(path)
        .map_err(|error| ContractError::Transport(error.to_string()))?;
    let payload = reader
        .read_payload()
        .map_err(ContractError::Transport)?
        .payload;
    if !market_data_snapshot_buffer_has_identifier(&payload) {
        return Err(ContractError::Invalid(
            "Market snapshot has an invalid file identifier".into(),
        ));
    }
    let root = root_as_market_data_snapshot(&payload)
        .map_err(|error| ContractError::Invalid(format!("decode Market snapshot: {error}")))?;
    let header = root.header();
    let data = root.payload();
    let quotes = data
        .quotes()
        .map(|quotes| {
            quotes
                .iter()
                .map(|quote| crate::model::Quote {
                    market_id: quote.market_id().unwrap_or_default().to_owned(),
                    instrument_id: quote.instrument_id().to_owned(),
                    bid_price: quote.bid_price().map(decimal_string),
                    bid_quantity: quote.bid_quantity().map(decimal_string),
                    ask_price: quote.ask_price().map(decimal_string),
                    ask_quantity: quote.ask_quantity().map(decimal_string),
                    observed_at_unix_nanos: quote.event_time_unix_nanos(),
                    source_id: quote.source_id().unwrap_or_default().to_owned(),
                })
                .collect()
        })
        .unwrap_or_default();
    let trades = data
        .trades()
        .map(|values| {
            values
                .iter()
                .map(|value| crate::model::Trade {
                    market_id: value.market_id().unwrap_or_default().to_owned(),
                    instrument_id: value.instrument_id().to_owned(),
                    trade_id: value.trade_id().map(ToOwned::to_owned),
                    price: decimal_string(value.price()),
                    quantity: decimal_string(value.quantity()),
                    cost: value.cost().map(decimal_string),
                    aggressor_side: match value.aggressor_side().variant_name() {
                        Some("BUY") => Some("buy".into()),
                        Some("SELL") => Some("sell".into()),
                        _ => None,
                    },
                    observed_at_unix_nanos: value.event_time_unix_nanos(),
                    source_id: value.source_id().unwrap_or_default().to_owned(),
                })
                .collect()
        })
        .unwrap_or_default();
    let mut bars = Vec::new();
    let mut trade_bars = Vec::new();
    let mut quote_bars = Vec::new();
    if let Some(values) = data.bars() {
        for value in values.iter() {
            let bar = crate::model::Bar {
                market_id: value.market_id().to_owned(),
                instrument_id: value.instrument_id().to_owned(),
                timeframe: value.timeframe().to_owned(),
                open: decimal_string(value.open()),
                high: decimal_string(value.high()),
                low: decimal_string(value.low()),
                close: decimal_string(value.close()),
                volume: value.volume().map(decimal_string),
                observed_at_unix_nanos: value.event_time_unix_nanos(),
                source_id: value.source_id().unwrap_or_default().to_owned(),
                derivation: value.derivation().unwrap_or_default().to_owned(),
            };
            match value.bar_kind().unwrap_or("bar") {
                "trade_bar" => trade_bars.push(crate::model::TradeBar { bar }),
                "quote_bar" => quote_bars.push(crate::model::QuoteBar { bar }),
                _ => bars.push(bar),
            }
        }
    }
    let greeks = data
        .greeks()
        .map(|values| {
            values
                .iter()
                .map(|value| crate::model::OptionGreeks {
                    market_id: value.market_id().unwrap_or_default().to_owned(),
                    instrument_id: value.instrument_id().to_owned(),
                    expiry_unix_nanos: nonzero(value.expiry_unix_nanos()),
                    strike: value.strike().map(decimal_string),
                    delta: value.delta().map(decimal_string),
                    gamma: value.gamma().map(decimal_string),
                    vega: value.vega().map(decimal_string),
                    theta: value.theta().map(decimal_string),
                    implied_volatility: value.implied_volatility().map(decimal_string),
                    observed_at_unix_nanos: value.event_time_unix_nanos(),
                    source_id: value.source_id().unwrap_or_default().to_owned(),
                    derivation: value.derivation().unwrap_or_default().to_owned(),
                })
                .collect()
        })
        .unwrap_or_default();
    let rates = data
        .rates()
        .map(|values| {
            values
                .iter()
                .map(|value| crate::model::Rate {
                    rate_id: value.rate_id().to_owned(),
                    market_id: value.market_id().unwrap_or_default().to_owned(),
                    instrument_id: value.instrument_id().unwrap_or_default().to_owned(),
                    basis: value.basis().unwrap_or_default().to_owned(),
                    value: decimal_string(value.value()),
                    mark_price: value.mark_price().map(decimal_string),
                    observed_at_unix_nanos: value.event_time_unix_nanos(),
                    source_id: value.source_id().unwrap_or_default().to_owned(),
                })
                .collect()
        })
        .unwrap_or_default();
    let ticker_24h = data
        .ticker_24h()
        .map(|values| {
            values
                .iter()
                .map(|value| crate::model::Ticker24h {
                    market_id: value.market_id().to_owned(),
                    instrument_id: value.instrument_id().to_owned(),
                    last_price: value.last_price().map(decimal_string),
                    bid_price: value.bid_price().map(decimal_string),
                    bid_quantity: value.bid_quantity().map(decimal_string),
                    ask_price: value.ask_price().map(decimal_string),
                    ask_quantity: value.ask_quantity().map(decimal_string),
                    open_price: value.open_price().map(decimal_string),
                    high_price: value.high_price().map(decimal_string),
                    low_price: value.low_price().map(decimal_string),
                    volume_base: value.volume_base().map(decimal_string),
                    volume_quote: value.volume_quote().map(decimal_string),
                    price_change_abs: value.price_change_abs().map(decimal_string),
                    price_change_pct: value.price_change_pct().map(decimal_string),
                    vwap: value.vwap().map(decimal_string),
                    mark_price: value.mark_price().map(decimal_string),
                    observed_at_unix_nanos: value.event_time_unix_nanos(),
                    source_id: value.source_id().unwrap_or_default().to_owned(),
                })
                .collect()
        })
        .unwrap_or_default();
    let mark_prices = data
        .mark_prices()
        .map(|values| {
            values
                .iter()
                .map(|value| crate::model::MarkPrice {
                    market_id: value.market_id().to_owned(),
                    instrument_id: value.instrument_id().to_owned(),
                    mark_price: decimal_string(value.mark_price()),
                    index_price: value.index_price().map(decimal_string),
                    estimated_settlement_price: value
                        .estimated_settlement_price()
                        .map(decimal_string),
                    funding_rate: value.funding_rate().map(decimal_string),
                    next_funding_time_unix_nanos: nonzero(value.next_funding_time_unix_nanos()),
                    observed_at_unix_nanos: value.event_time_unix_nanos(),
                    source_id: value.source_id().unwrap_or_default().to_owned(),
                })
                .collect()
        })
        .unwrap_or_default();
    let index_prices = data
        .index_prices()
        .map(|values| {
            values
                .iter()
                .map(|value| crate::model::IndexPrice {
                    market_id: value.market_id().to_owned(),
                    instrument_id: value.instrument_id().to_owned(),
                    spot_index_price: value.spot_index_price().map(decimal_string),
                    contract_index_price: value.contract_index_price().map(decimal_string),
                    index_price: value.index_price().map(decimal_string),
                    funding_rate: value.funding_rate().map(decimal_string),
                    observed_at_unix_nanos: value.event_time_unix_nanos(),
                    source_id: value.source_id().unwrap_or_default().to_owned(),
                })
                .collect()
        })
        .unwrap_or_default();
    let funding_rates = data
        .funding_rates()
        .map(|values| {
            values
                .iter()
                .map(|value| crate::model::FundingRate {
                    market_id: value.market_id().to_owned(),
                    instrument_id: value.instrument_id().to_owned(),
                    funding_rate: decimal_string(value.funding_rate()),
                    funding_period_seconds: nonzero(value.funding_period_seconds()),
                    next_funding_time_unix_nanos: nonzero(value.next_funding_time_unix_nanos()),
                    observed_at_unix_nanos: value.event_time_unix_nanos(),
                    source_id: value.source_id().unwrap_or_default().to_owned(),
                })
                .collect()
        })
        .unwrap_or_default();
    let open_interests = data
        .open_interests()
        .map(|values| {
            values
                .iter()
                .map(|value| crate::model::OpenInterest {
                    market_id: value.market_id().to_owned(),
                    instrument_id: value.instrument_id().to_owned(),
                    contracts: decimal_string(value.contracts()),
                    quote_value: value.quote_value().map(decimal_string),
                    change_24h: value.change_24h().map(decimal_string),
                    change_pct_24h: value.change_pct_24h().map(decimal_string),
                    observed_at_unix_nanos: value.event_time_unix_nanos(),
                    source_id: value.source_id().unwrap_or_default().to_owned(),
                })
                .collect()
        })
        .unwrap_or_default();
    let freshness = data
        .freshness()
        .map(|values| {
            values
                .iter()
                .map(|value| {
                    let key = format!(
                        "{}:{}:{}",
                        value.source_id(),
                        value.market_id(),
                        value.data_kind()
                    );
                    let status = match value.status() {
                        "current" => crate::model::DataFreshnessStatus::Current,
                        "stale" => crate::model::DataFreshnessStatus::Stale,
                        _ => crate::model::DataFreshnessStatus::Unknown,
                    };
                    (
                        key,
                        MarketSnapshotFreshness {
                            source_id: value.source_id().to_owned(),
                            market_id: value.market_id().to_owned(),
                            data_kind: value.data_kind().to_owned(),
                            last_event_time_unix_nanos: value.last_event_time_unix_nanos(),
                            last_received_time_unix_nanos: value.last_received_time_unix_nanos(),
                            status,
                        },
                    )
                })
                .collect()
        })
        .unwrap_or_default();
    let instrument_statuses = data
        .instrument_statuses()
        .map(|values| {
            values
                .iter()
                .map(|value| crate::model::InstrumentStatus {
                    market_id: value.market_id().to_owned(),
                    instrument_id: value.instrument_id().to_owned(),
                    status: value.status().to_owned(),
                    reason: value.reason().map(ToOwned::to_owned),
                    effective_at_unix_nanos: nonzero(value.effective_at_unix_nanos()),
                    observed_at_unix_nanos: value.event_time_unix_nanos(),
                    source_id: value.source_id().unwrap_or_default().to_owned(),
                })
                .collect()
        })
        .unwrap_or_default();
    Ok(MarketSnapshotRead {
        generation: header.generation(),
        quotes,
        trades,
        bars,
        trade_bars,
        quote_bars,
        greeks,
        rates,
        ticker_24h,
        mark_prices,
        index_prices,
        funding_rates,
        open_interests,
        freshness,
        instrument_statuses,
    })
}

fn nonzero(value: u64) -> Option<u64> {
    (value != 0).then_some(value)
}

fn decode_levels(
    values: Option<
        flatbuffers::Vector<
            '_,
            flatbuffers::ForwardsUOffset<
                kairos_protocol::generated::kairos::market::v_1::OrderBookLevel<'_>,
            >,
        >,
    >,
) -> Vec<crate::model::PriceLevel> {
    values
        .map(|levels| {
            levels
                .iter()
                .map(|level| crate::model::PriceLevel {
                    price: decimal_string(level.price()),
                    quantity: decimal_string(level.quantity()),
                })
                .collect()
        })
        .unwrap_or_default()
}

fn decode_depth_policy(value: Option<&str>) -> crate::model::DepthPolicy {
    match value.unwrap_or("full") {
        "full" => crate::model::DepthPolicy::Full,
        value
            if value
                .strip_prefix("top_n:")
                .and_then(|limit| limit.parse::<u32>().ok())
                .is_some() =>
        {
            crate::model::DepthPolicy::TopN(
                value
                    .strip_prefix("top_n:")
                    .and_then(|limit| limit.parse::<u32>().ok())
                    .unwrap_or_default(),
            )
        }
        _ => crate::model::DepthPolicy::Full,
    }
}

fn decimal_string(value: &kairos_protocol::generated::kairos::common::v_1::Decimal64) -> String {
    let mantissa = value.mantissa();
    let scale = value.scale() as usize;
    let sign = if mantissa < 0 { "-" } else { "" };
    let digits = mantissa.unsigned_abs().to_string();
    if scale == 0 {
        return format!("{sign}{digits}");
    }
    let padded = format!("{digits:0>width$}", width = scale + 1);
    format!(
        "{sign}{}.{}",
        &padded[..padded.len() - scale],
        &padded[padded.len() - scale..]
    )
}
