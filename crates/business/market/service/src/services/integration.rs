//! Adapter from the integration application's normalized stream to Market
//! observations. Provider clients and payloads stop at the integration crate.

use std::collections::{BTreeMap, VecDeque};

use kairos_integration::{
    MarketEventKind, MarketStreamConnection, MarketSubscription, SubscriptionId as ProviderId,
};

use super::feed::{MarketFeed, MarketOrderBookUpdate};
use crate::domain::freshness::FeedStatus;
use crate::domain::market::MarketDescriptor;
use crate::domain::observations::{Bar, MarketObservation, OptionGreeks, Quote, Trade};
use crate::domain::orderbook::PriceLevel;
use crate::domain::subscriptions::SubscriptionId;

pub struct IntegrationMarketFeed {
    connection: Box<dyn MarketStreamConnection>,
    markets: BTreeMap<String, MarketDescriptor>,
    subscriptions: BTreeMap<SubscriptionId, ProviderId>,
    provider_refcounts: BTreeMap<ProviderId, usize>,
    subscription_symbols: BTreeMap<SubscriptionId, String>,
    next_id: u64,
    status: FeedStatus,
    orderbook_updates: VecDeque<MarketOrderBookUpdate>,
}

impl IntegrationMarketFeed {
    pub fn new(connection: Box<dyn MarketStreamConnection>) -> Result<Self, String> {
        Ok(Self {
            connection,
            markets: BTreeMap::new(),
            subscriptions: BTreeMap::new(),
            provider_refcounts: BTreeMap::new(),
            subscription_symbols: BTreeMap::new(),
            next_id: 1,
            status: FeedStatus::Disconnected,
            orderbook_updates: VecDeque::new(),
        })
    }

    pub fn status(&self) -> FeedStatus {
        self.status
    }
}

impl MarketFeed for IntegrationMarketFeed {
    fn start(&mut self) -> Result<(), String> {
        self.connection.start()?;
        self.status = FeedStatus::Ready;
        Ok(())
    }

    fn status(&self) -> FeedStatus {
        self.status
    }

    fn subscribe(&mut self, market: &MarketDescriptor) -> Result<SubscriptionId, String> {
        market.validate()?;
        let request = MarketSubscription::new([market.source_symbol.clone()])
            .map_err(|error| error.to_string())?;
        let provider_id = self
            .connection
            .subscribe(request)
            .map_err(|error| error.to_string())?;
        let id = SubscriptionId::new(format!("provider:{}", self.next_id))?;
        self.next_id += 1;
        self.markets
            .insert(market.source_symbol.to_ascii_uppercase(), market.clone());
        self.subscriptions.insert(id.clone(), provider_id);
        *self.provider_refcounts.entry(provider_id).or_default() += 1;
        self.subscription_symbols
            .insert(id.clone(), market.source_symbol.to_ascii_uppercase());
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
        }
        let request =
            MarketSubscription::new(markets.iter().map(|market| market.source_symbol.clone()))
                .map_err(|error| error.to_string())?;
        let provider_id = self
            .connection
            .subscribe(request)
            .map_err(|error| error.to_string())?;
        let mut ids = Vec::with_capacity(markets.len());
        for market in markets {
            let id = SubscriptionId::new(format!("provider:{}", self.next_id))?;
            self.next_id += 1;
            self.markets
                .insert(market.source_symbol.to_ascii_uppercase(), market.clone());
            self.subscriptions.insert(id.clone(), provider_id);
            *self.provider_refcounts.entry(provider_id).or_default() += 1;
            self.subscription_symbols
                .insert(id.clone(), market.source_symbol.to_ascii_uppercase());
            ids.push(id);
        }
        Ok(ids)
    }

    fn unsubscribe(&mut self, subscription: &SubscriptionId) -> Result<(), String> {
        let provider_id = *self
            .subscriptions
            .get(subscription)
            .ok_or_else(|| format!("unknown market subscription: {}", subscription.0))?;
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
        self.subscriptions.remove(subscription);
        self.subscription_symbols.remove(subscription);
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
                        bid_price: event.price.clone(),
                        bid_quantity: event.quantity.clone(),
                        ask_price: event.ask_price.clone(),
                        ask_quantity: event.ask_quantity.clone(),
                        observed_at_unix_nanos: event.observed_at_unix_nanos,
                        source_id: market.venue_id.clone(),
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
                    observed_at_unix_nanos: event.observed_at_unix_nanos,
                    source_id: market.venue_id.clone(),
                }),
                MarketEventKind::Bar => {
                    let bar = event
                        .bar
                        .ok_or_else(|| "bar event has no bar payload".to_string())?;
                    MarketObservation::Bar(Bar {
                        market_id: market.market_id.clone(),
                        instrument_id: market.instrument_id.clone(),
                        timeframe: bar.timeframe,
                        open: bar.open,
                        high: bar.high,
                        low: bar.low,
                        close: bar.close,
                        volume: bar.volume,
                        observed_at_unix_nanos: event.observed_at_unix_nanos,
                        source_id: market.venue_id.clone(),
                        derivation: bar.derivation,
                    })
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
                        source_id: market.venue_id.clone(),
                        derivation: greeks.derivation,
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
                        market_id: market.market_id.clone(),
                        instrument_id: market.instrument_id.clone(),
                        first_sequence,
                        last_sequence,
                        event_time_unix_nanos: event.observed_at_unix_nanos,
                        bids: event
                            .bids
                            .into_iter()
                            .map(|(price, quantity)| PriceLevel { price, quantity })
                            .collect(),
                        asks: event
                            .asks
                            .into_iter()
                            .map(|(price, quantity)| PriceLevel { price, quantity })
                            .collect(),
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
        self.connection.reconnect()?;
        self.status = FeedStatus::WarmingUp;
        Ok(())
    }

    fn resync_orderbook(&mut self, market_id: &str) -> Result<(), String> {
        let symbol = self
            .markets
            .values()
            .find(|market| market.market_id == market_id)
            .map(|market| market.source_symbol.to_ascii_uppercase())
            .ok_or_else(|| format!("unknown market for order book resync: {market_id}"))?;
        let matches = self
            .subscription_symbols
            .iter()
            .filter(|(_, value)| *value == &symbol)
            .filter_map(|(id, _)| self.subscriptions.get(id).map(|provider| (id, *provider)))
            .collect::<Vec<_>>();
        let (subscription, provider_id) = match matches.as_slice() {
            [] => {
                return Err(format!(
                    "market is not subscribed for order book resync: {market_id}"
                ))
            }
            [single] => ((*single.0).clone(), single.1),
            _ => {
                return Err(format!(
                    "market has multiple provider subscriptions for order book resync: {market_id}"
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
