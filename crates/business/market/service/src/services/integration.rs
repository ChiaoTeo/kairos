//! Adapter from the integration application's normalized stream to Market
//! observations. Provider clients and payloads stop at the integration crate.

use std::collections::{BTreeMap, VecDeque};

use kairos_domain_types::{Money, Price, Quantity, UnixNanos};
use kairos_integration::application::{
    MarketEventKind, MarketSubscription, SubscriptionId as ProviderId,
};
use kairos_integration::blocking::{MarketSnapshotConnection, MarketStreamConnection};

use super::feed::{MarketFeed, MarketOrderBookUpdate};
use crate::application::MarketDataKey;
use crate::domain::freshness::FeedStatus;
use crate::domain::market::MarketDescriptor;
use crate::domain::observations::{
    Bar, FundingRate, IndexPrice, InstrumentStatus, MarkPrice, MarketObservation, OpenInterest,
    OptionGreeks, Quote, QuoteBar, Rate, Ticker24h, Trade, TradeBar,
};
use crate::domain::orderbook::PriceLevel;
use crate::domain::subscriptions::SubscriptionId;

pub struct IntegrationMarketFeed {
    connection: Box<dyn MarketStreamConnection>,
    source_id: String,
    markets: BTreeMap<String, MarketDescriptor>,
    subscriptions: BTreeMap<SubscriptionId, ProviderId>,
    provider_refcounts: BTreeMap<ProviderId, usize>,
    subscription_symbols: BTreeMap<SubscriptionId, String>,
    next_id: u64,
    status: FeedStatus,
    orderbook_updates: VecDeque<MarketOrderBookUpdate>,
}

/// Market-owned subscription and polling policy around the concrete OKX
/// snapshot operation. Integration fetches provider facts; Market owns which
/// symbols are active and how provider symbols map to canonical markets.
pub struct OkxSnapshotMarketFeed {
    connection: kairos_integration::blocking::OkxMarketSnapshot,
    source_id: String,
    markets: BTreeMap<String, MarketDescriptor>,
    subscriptions: BTreeMap<SubscriptionId, String>,
    next_id: u64,
    status: FeedStatus,
}

impl OkxSnapshotMarketFeed {
    pub fn new(connection: kairos_integration::blocking::OkxMarketSnapshot) -> Self {
        Self::with_source(connection, "okx.public.rest")
    }

    pub fn with_source(
        connection: kairos_integration::blocking::OkxMarketSnapshot,
        source_id: impl Into<String>,
    ) -> Self {
        Self {
            connection,
            source_id: source_id.into(),
            markets: BTreeMap::new(),
            subscriptions: BTreeMap::new(),
            next_id: 1,
            status: FeedStatus::Disconnected,
        }
    }

    fn register(&mut self, market: &MarketDescriptor) -> Result<SubscriptionId, String> {
        market.validate()?;
        let symbol = market.source_symbol.to_ascii_uppercase();
        if let Some(existing) = self.markets.get(&symbol) {
            if existing != market {
                return Err(format!(
                    "source symbol {} maps to multiple market descriptors",
                    market.source_symbol
                ));
            }
        }
        let id = SubscriptionId::new(format!("okx-snapshot:{}", self.next_id))?;
        self.next_id += 1;
        self.markets.insert(symbol.clone(), market.clone());
        self.subscriptions.insert(id.clone(), symbol);
        Ok(id)
    }
}

impl MarketFeed for OkxSnapshotMarketFeed {
    fn start(&mut self) -> Result<(), String> {
        self.status = FeedStatus::Ready;
        Ok(())
    }

    fn status(&self) -> FeedStatus {
        self.status
    }

    fn subscribe(&mut self, market: &MarketDescriptor) -> Result<SubscriptionId, String> {
        self.register(market)
    }

    fn subscribe_many(
        &mut self,
        markets: &[MarketDescriptor],
    ) -> Result<Vec<SubscriptionId>, String> {
        let mut ids = Vec::with_capacity(markets.len());
        for market in markets {
            match self.register(market) {
                Ok(id) => ids.push(id),
                Err(error) => {
                    for id in ids {
                        let _ = self.unsubscribe(&id);
                    }
                    return Err(format!(
                        "OKX market batch subscription rolled back: {error}"
                    ));
                }
            }
        }
        Ok(ids)
    }

    fn unsubscribe(&mut self, subscription: &SubscriptionId) -> Result<(), String> {
        let symbol = self
            .subscriptions
            .remove(subscription)
            .ok_or_else(|| format!("unknown market subscription: {}", subscription.0))?;
        if !self.subscriptions.values().any(|value| value == &symbol) {
            self.markets.remove(&symbol);
        }
        Ok(())
    }

    fn poll(&mut self) -> Result<Vec<MarketObservation>, String> {
        let symbols = self
            .markets
            .keys()
            .map(|symbol| {
                kairos_domain_types::ProviderSymbol::new(symbol).map_err(|e| e.to_string())
            })
            .collect::<Result<Vec<_>, _>>()?;
        if symbols.is_empty() {
            return Ok(Vec::new());
        }
        let events = self.connection.fetch_snapshot(&symbols).map_err(|error| {
            self.status = FeedStatus::Degraded;
            error.to_string()
        })?;
        let mut values = Vec::with_capacity(events.len());
        for event in events {
            if !matches!(
                event.kind,
                MarketEventKind::Quote | MarketEventKind::Snapshot
            ) {
                return Err(format!(
                    "OKX snapshot returned unsupported event kind: {:?}",
                    event.kind
                ));
            }
            let market = self
                .markets
                .get(&event.symbol.to_ascii_uppercase())
                .ok_or_else(|| format!("OKX snapshot has unknown symbol: {}", event.symbol))?;
            values.push(MarketObservation::Quote(Quote {
                market_id: market.market_id.clone(),
                instrument_id: market.instrument_id.clone(),
                bid_price: event.price,
                bid_quantity: event.quantity,
                ask_price: event.ask_price,
                ask_quantity: event.ask_quantity,
                observed_at_unix_nanos: event.observed_at_unix_nanos,
                source_id: self.source_id.clone(),
            }));
        }
        if self.status == FeedStatus::WarmingUp {
            self.status = FeedStatus::Ready;
        }
        Ok(values)
    }

    fn recover(&mut self) -> Result<(), String> {
        self.status = FeedStatus::WarmingUp;
        Ok(())
    }
}

impl IntegrationMarketFeed {
    pub fn new(connection: Box<dyn MarketStreamConnection>) -> Result<Self, String> {
        let source_id = connection.descriptor().binding_id.clone();
        Self::with_source(connection, source_id)
    }

    pub fn with_source(
        connection: Box<dyn MarketStreamConnection>,
        source_id: impl Into<String>,
    ) -> Result<Self, String> {
        let source_id = source_id.into();
        if source_id.trim().is_empty() {
            return Err("market feed source id is required".into());
        }
        Ok(Self {
            connection,
            source_id,
            markets: BTreeMap::new(),
            subscriptions: BTreeMap::new(),
            provider_refcounts: BTreeMap::new(),
            subscription_symbols: BTreeMap::new(),
            next_id: 1,
            status: FeedStatus::Disconnected,
            orderbook_updates: VecDeque::new(),
        })
    }
}

impl MarketFeed for IntegrationMarketFeed {
    fn start(&mut self) -> Result<(), String> {
        self.connection
            .connect_channel()
            .map_err(|error| error.to_string())?;
        self.status = FeedStatus::Ready;
        Ok(())
    }

    fn status(&self) -> FeedStatus {
        self.status
    }

    fn subscribe(&mut self, market: &MarketDescriptor) -> Result<SubscriptionId, String> {
        market.validate()?;
        let key = market.source_symbol.to_ascii_uppercase();
        if let Some(existing) = self.markets.get(&key) {
            if existing != market {
                return Err(format!(
                    "source symbol {} maps to multiple market descriptors",
                    market.source_symbol
                ));
            }
        }
        let request = MarketSubscription::new([market.source_symbol.to_string()])
            .map_err(|error| error.to_string())?;
        let provider_id = self
            .connection
            .subscribe(request)
            .map_err(|error| error.to_string())?;
        let id = SubscriptionId::new(format!("provider:{}", self.next_id))?;
        self.next_id += 1;
        self.markets.insert(key.clone(), market.clone());
        self.subscriptions.insert(id.clone(), provider_id);
        *self.provider_refcounts.entry(provider_id).or_default() += 1;
        self.subscription_symbols.insert(id.clone(), key);
        Ok(id)
    }

    fn subscribe_many(
        &mut self,
        markets: &[MarketDescriptor],
    ) -> Result<Vec<SubscriptionId>, String> {
        if markets.is_empty() {
            return Ok(Vec::new());
        }
        for market in markets {
            market.validate()?;
            let key = market.source_symbol.to_ascii_uppercase();
            if let Some(existing) = self.markets.get(&key) {
                if existing != market {
                    return Err(format!(
                        "source symbol {} maps to multiple market descriptors",
                        market.source_symbol
                    ));
                }
            }
        }
        let request = MarketSubscription::new(
            markets
                .iter()
                .map(|market| market.source_symbol.to_string()),
        )
        .map_err(|error| error.to_string())?;
        let provider_id = self
            .connection
            .subscribe(request)
            .map_err(|error| error.to_string())?;
        let mut ids = Vec::with_capacity(markets.len());
        for market in markets {
            let id = SubscriptionId::new(format!("provider:{}", self.next_id))?;
            self.next_id += 1;
            let key = market.source_symbol.to_ascii_uppercase();
            self.markets.insert(key.clone(), market.clone());
            self.subscriptions.insert(id.clone(), provider_id);
            *self.provider_refcounts.entry(provider_id).or_default() += 1;
            self.subscription_symbols.insert(id.clone(), key);
            ids.push(id);
        }
        Ok(ids)
    }

    fn unsubscribe(&mut self, subscription: &SubscriptionId) -> Result<(), String> {
        let provider_id = *self
            .subscriptions
            .get(subscription)
            .ok_or_else(|| format!("unknown market subscription: {}", subscription.0))?;
        let refcount = self
            .provider_refcounts
            .get(&provider_id)
            .copied()
            .unwrap_or(1);
        if refcount <= 1 {
            self.connection
                .unsubscribe(provider_id)
                .map_err(|error| error.to_string())?;
            self.provider_refcounts.remove(&provider_id);
        } else if let Some(count) = self.provider_refcounts.get_mut(&provider_id) {
            *count -= 1;
        }
        let symbol = self.subscription_symbols.get(subscription).cloned();
        self.subscriptions.remove(subscription);
        self.subscription_symbols.remove(subscription);
        if let Some(symbol) = symbol {
            if !self
                .subscription_symbols
                .values()
                .any(|value| value == &symbol)
            {
                self.markets.remove(&symbol);
            }
        }
        Ok(())
    }

    fn poll(&mut self) -> Result<Vec<MarketObservation>, String> {
        let mut values = Vec::new();
        let mut received_data = false;
        while let Some(event) = self.connection.next_event().map_err(|error| {
            self.status = FeedStatus::Degraded;
            error.to_string()
        })? {
            let key = event.symbol.to_ascii_uppercase();
            received_data = true;
            let market = self
                .markets
                .get(&key)
                .ok_or_else(|| format!("provider event has unknown symbol: {}", event.symbol))?;
            let value = match event.kind {
                MarketEventKind::Quote | MarketEventKind::Snapshot => {
                    MarketObservation::Quote(Quote {
                        market_id: market.market_id.clone(),
                        instrument_id: market.instrument_id.clone(),
                        bid_price: event.price,
                        bid_quantity: event.quantity,
                        ask_price: event.ask_price,
                        ask_quantity: event.ask_quantity,
                        observed_at_unix_nanos: event.observed_at_unix_nanos,
                        source_id: self.source_id.clone(),
                    })
                }
                MarketEventKind::Trade => MarketObservation::Trade(Trade {
                    market_id: market.market_id.clone(),
                    instrument_id: market.instrument_id.clone(),
                    trade_id: None,
                    price: event
                        .price
                        .ok_or_else(|| "trade event has no price".to_string())?,
                    quantity: event
                        .quantity
                        .ok_or_else(|| "trade event has no quantity".to_string())?,
                    cost: None,
                    aggressor_side: None,
                    observed_at_unix_nanos: event.observed_at_unix_nanos,
                    source_id: self.source_id.clone(),
                }),
                MarketEventKind::Bar | MarketEventKind::TradeBar | MarketEventKind::QuoteBar => {
                    let bar = event
                        .bar
                        .ok_or_else(|| "bar event has no bar payload".to_string())?;
                    let bar = Bar {
                        market_id: market.market_id.clone(),
                        instrument_id: market.instrument_id.clone(),
                        timeframe: bar.timeframe,
                        open: bar.open,
                        high: bar.high,
                        low: bar.low,
                        close: bar.close,
                        volume: bar.volume,
                        observed_at_unix_nanos: event.observed_at_unix_nanos,
                        source_id: self.source_id.clone(),
                        derivation: bar.derivation,
                    };
                    match event.kind {
                        MarketEventKind::TradeBar => MarketObservation::TradeBar(TradeBar { bar }),
                        MarketEventKind::QuoteBar => MarketObservation::QuoteBar(QuoteBar { bar }),
                        _ => MarketObservation::Bar(bar),
                    }
                }
                MarketEventKind::Greeks => {
                    let greeks = event
                        .greeks
                        .ok_or_else(|| "greeks event has no greeks payload".to_string())?;
                    MarketObservation::OptionGreeks(OptionGreeks {
                        market_id: market.market_id.clone(),
                        instrument_id: market.instrument_id.clone(),
                        expiry_unix_nanos: greeks.expiry_unix_nanos,
                        strike: greeks.strike,
                        delta: greeks.delta,
                        gamma: greeks.gamma,
                        vega: greeks.vega,
                        theta: greeks.theta,
                        implied_volatility: greeks.implied_volatility,
                        observed_at_unix_nanos: event.observed_at_unix_nanos,
                        source_id: self.source_id.clone(),
                        derivation: greeks.derivation,
                    })
                }
                MarketEventKind::Rate => MarketObservation::Rate(Rate {
                    rate_id: format!("funding:{}", market.market_id),
                    market_id: market.market_id.clone(),
                    instrument_id: market.instrument_id.clone(),
                    basis: "funding".into(),
                    value: event
                        .rate
                        .ok_or_else(|| "rate event has no value".to_string())?,
                    mark_price: event.ask_price,
                    observed_at_unix_nanos: event.observed_at_unix_nanos,
                    source_id: self.source_id.clone(),
                }),
                MarketEventKind::Ticker24h => MarketObservation::Ticker24h(Ticker24h {
                    market_id: market.market_id.clone(),
                    instrument_id: market.instrument_id.clone(),
                    last_price: event.price,
                    bid_price: None,
                    bid_quantity: None,
                    ask_price: event.ask_price,
                    ask_quantity: event.ask_quantity,
                    open_price: None,
                    high_price: None,
                    low_price: None,
                    volume_base: event.quantity,
                    volume_quote: None,
                    price_change_abs: None,
                    price_change_pct: None,
                    vwap: None,
                    mark_price: None,
                    observed_at_unix_nanos: event.observed_at_unix_nanos,
                    source_id: self.source_id.clone(),
                }),
                MarketEventKind::MarkPrice => MarketObservation::MarkPrice(MarkPrice {
                    market_id: market.market_id.clone(),
                    instrument_id: market.instrument_id.clone(),
                    mark_price: event
                        .price
                        .ok_or_else(|| "mark price event has no price".to_string())?,
                    index_price: event.ask_price,
                    estimated_settlement_price: None,
                    funding_rate: None,
                    next_funding_time_unix_nanos: None,
                    observed_at_unix_nanos: event.observed_at_unix_nanos,
                    source_id: self.source_id.clone(),
                }),
                MarketEventKind::IndexPrice => MarketObservation::IndexPrice(IndexPrice {
                    market_id: market.market_id.clone(),
                    instrument_id: market.instrument_id.clone(),
                    spot_index_price: event.price,
                    contract_index_price: None,
                    index_price: event.price,
                    funding_rate: None,
                    observed_at_unix_nanos: event.observed_at_unix_nanos,
                    source_id: self.source_id.clone(),
                }),
                MarketEventKind::FundingRate => MarketObservation::FundingRate(FundingRate {
                    market_id: market.market_id.clone(),
                    instrument_id: market.instrument_id.clone(),
                    funding_rate: event
                        .rate
                        .ok_or_else(|| "funding rate event has no value".to_string())?,
                    funding_period_seconds: None,
                    next_funding_time_unix_nanos: None,
                    observed_at_unix_nanos: event.observed_at_unix_nanos,
                    source_id: self.source_id.clone(),
                }),
                MarketEventKind::OpenInterest => MarketObservation::OpenInterest(OpenInterest {
                    market_id: market.market_id.clone(),
                    instrument_id: market.instrument_id.clone(),
                    contracts: event
                        .quantity
                        .ok_or_else(|| "open interest event has no quantity".to_string())?,
                    quote_value: event
                        .price
                        .map(|value| Money::new(value.mantissa(), value.scale())),
                    change_24h: None,
                    change_pct_24h: None,
                    observed_at_unix_nanos: event.observed_at_unix_nanos,
                    source_id: self.source_id.clone(),
                }),
                MarketEventKind::InstrumentStatus => {
                    MarketObservation::InstrumentStatus(InstrumentStatus {
                        market_id: market.market_id.clone(),
                        instrument_id: market.instrument_id.clone(),
                        status: event
                            .price
                            .map(|value| value.to_string())
                            .unwrap_or_else(|| "unknown".into())
                            .into(),
                        reason: event.quantity.map(|value| value.to_string()),
                        effective_at_unix_nanos: event
                            .sequence
                            .map(|value| UnixNanos::new(value.get())),
                        observed_at_unix_nanos: event.observed_at_unix_nanos,
                        source_id: self.source_id.clone(),
                    })
                }
                MarketEventKind::BookSnapshot | MarketEventKind::BookDelta => {
                    let first_sequence = event
                        .first_sequence
                        .ok_or_else(|| "order book delta has no first sequence".to_string())?;
                    let last_sequence = event
                        .last_sequence
                        .or(event.sequence)
                        .ok_or_else(|| "order book delta has no last sequence".to_string())?;
                    self.orderbook_updates.push_back(MarketOrderBookUpdate {
                        source_id: self.source_id.clone(),
                        market_id: market.market_id.clone(),
                        instrument_id: market.instrument_id.clone(),
                        first_sequence,
                        last_sequence,
                        event_time_unix_nanos: event.observed_at_unix_nanos,
                        bids: event
                            .bids
                            .into_iter()
                            .map(parse_price_level)
                            .collect::<Result<Vec<_>, _>>()?,
                        asks: event
                            .asks
                            .into_iter()
                            .map(parse_price_level)
                            .collect::<Result<Vec<_>, _>>()?,
                        snapshot: event.kind == MarketEventKind::BookSnapshot,
                    });
                    continue;
                }
                MarketEventKind::Heartbeat => continue,
            };
            values.push(value);
        }
        if received_data && self.status == FeedStatus::WarmingUp {
            self.status = FeedStatus::Ready;
        }
        Ok(values)
    }

    fn poll_orderbooks(&mut self) -> Result<Vec<MarketOrderBookUpdate>, String> {
        let updates = self.orderbook_updates.drain(..).collect::<Vec<_>>();
        if !updates.is_empty() && self.status == FeedStatus::WarmingUp {
            self.status = FeedStatus::Ready;
        }
        Ok(updates)
    }

    fn recover(&mut self) -> Result<(), String> {
        self.status = FeedStatus::Reconnecting;
        self.connection
            .reconnect_channel()
            .map_err(|error| error.to_string())?;
        self.status = FeedStatus::WarmingUp;
        Ok(())
    }

    fn resync_orderbook(&mut self, key: &MarketDataKey) -> Result<(), String> {
        let symbol = self
            .markets
            .values()
            .find(|market| {
                market.market_id == key.market_id
                    && (market.source_id.as_deref() == Some(self.source_id.as_str())
                        || market.source_id.is_none())
            })
            .map(|market| market.source_symbol.to_ascii_uppercase())
            .ok_or_else(|| format!("unknown market for order book resync: {}", key.market_id))?;
        let matches = self
            .subscription_symbols
            .iter()
            .filter(|(_, value)| *value == &symbol)
            .filter_map(|(id, _)| self.subscriptions.get(id).map(|provider| (id, *provider)))
            .collect::<Vec<_>>();
        let (subscription, provider_id) = match matches.as_slice() {
            [] => {
                return Err(format!(
                    "market is not subscribed for order book resync: {}",
                    key.market_id
                ))
            }
            [single] => ((*single.0).clone(), single.1),
            _ => {
                return Err(format!(
                    "market has multiple provider subscriptions for order book resync: {}",
                    key.market_id
                ))
            }
        };
        let should_unsubscribe = match self.provider_refcounts.get_mut(&provider_id) {
            Some(count) if *count > 1 => {
                *count -= 1;
                false
            }
            Some(_) => {
                self.provider_refcounts.remove(&provider_id);
                true
            }
            None => true,
        };
        if should_unsubscribe {
            self.connection
                .unsubscribe(provider_id)
                .map_err(|error| error.to_string())?;
        }
        let replacement = self
            .connection
            .subscribe(MarketSubscription::new([symbol]).map_err(|error| error.to_string())?)
            .map_err(|error| error.to_string())?;
        *self.provider_refcounts.entry(replacement).or_default() += 1;
        self.subscriptions.insert(subscription, replacement);
        self.status = FeedStatus::WarmingUp;
        Ok(())
    }
}

fn parse_price_level((price, quantity): (Price, Quantity)) -> Result<PriceLevel, String> {
    Ok(PriceLevel { price, quantity })
}

#[cfg(test)]
mod tests {
    use super::{MarketFeed, OkxSnapshotMarketFeed};
    use crate::domain::market::MarketDescriptor;
    use crate::domain::observations::MarketObservation;
    use kairos_integration::participants::okx::{
        InstrumentType as OkxInstrumentType, OkxConnection, OkxConnectionConfig,
    };
    use std::io::{Read, Write};

    #[test]
    fn okx_snapshot_adapter_keeps_subscription_and_canonical_mapping_in_market() {
        let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 4096];
            let read = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..read]);
            assert!(request.starts_with("GET /api/v5/market/ticker?instId=BTC-USDT "));
            let body = r#"{"code":"0","data":[{"instId":"BTC-USDT","bidPx":"60000.1","bidSz":"2","askPx":"60000.2","askSz":"3"}]}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            );
            stream.write_all(response.as_bytes()).unwrap();
        });
        let provider = OkxConnection::connect(OkxConnectionConfig {
            environment: "public".into(),
            rest_base_url: format!("http://{address}"),
            shared_quota: None,
        })
        .unwrap();
        let mut feed =
            OkxSnapshotMarketFeed::new(provider.blocking_market_snapshot(OkxInstrumentType::Spot));
        feed.start().unwrap();
        feed.subscribe(
            &MarketDescriptor::new(
                "market:okx:spot:BTC-USDT",
                "instrument:spot:BTC",
                "okx",
                "spot",
                "BTC-USDT",
            )
            .unwrap(),
        )
        .unwrap();

        let values = feed.poll().unwrap();
        assert!(matches!(
            &values[0],
            MarketObservation::Quote(value)
                if value.market_id == "market:okx:spot:BTC-USDT"
                    && value.source_id == "okx"
        ));
        server.join().unwrap();
    }
}
