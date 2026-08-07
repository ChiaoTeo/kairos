//! Adapter from the integration application's normalized stream to Market
//! observations. Provider clients and payloads stop at the integration crate.

use std::collections::{BTreeMap, VecDeque};

use kairos_integration::{
    MarketEventKind, MarketStreamConnection, MarketSubscription, SubscriptionId as ProviderId,
};

use crate::application::market::protocol::{MarketFeed, MarketOrderBookUpdate};
use crate::domain::freshness::FeedStatus;
use crate::domain::market::MarketDescriptor;
use crate::domain::observations::{MarketObservation, Quote, Trade};
use crate::domain::orderbook::PriceLevel;
use crate::domain::subscriptions::SubscriptionId;

pub struct IntegrationMarketFeed {
    connection: Box<dyn MarketStreamConnection>,
    markets: BTreeMap<String, MarketDescriptor>,
    subscriptions: BTreeMap<SubscriptionId, ProviderId>,
    next_id: u64,
    status: FeedStatus,
    orderbook_updates: VecDeque<MarketOrderBookUpdate>,
}

impl IntegrationMarketFeed {
    pub fn new(mut connection: Box<dyn MarketStreamConnection>) -> Result<Self, String> {
        connection.start()?;
        Ok(Self {
            connection,
            markets: BTreeMap::new(),
            subscriptions: BTreeMap::new(),
            next_id: 1,
            status: FeedStatus::Ready,
            orderbook_updates: VecDeque::new(),
        })
    }

    pub fn status(&self) -> FeedStatus {
        self.status
    }
}

impl MarketFeed for IntegrationMarketFeed {
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
        Ok(id)
    }

    fn unsubscribe(&mut self, subscription: &SubscriptionId) -> Result<(), String> {
        let provider_id = self
            .subscriptions
            .remove(subscription)
            .ok_or_else(|| format!("unknown market subscription: {}", subscription.0))?;
        self.connection
            .unsubscribe(provider_id)
            .map_err(|error| error.to_string())
    }

    fn poll(&mut self) -> Result<Vec<MarketObservation>, String> {
        let mut values = Vec::new();
        while let Some(event) = self.connection.next_event().map_err(|error| {
            self.status = FeedStatus::Degraded;
            error.to_string()
        })? {
            let key = event.symbol.to_ascii_uppercase();
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
        Ok(values)
    }

    fn poll_orderbooks(&mut self) -> Result<Vec<MarketOrderBookUpdate>, String> {
        Ok(self.orderbook_updates.drain(..).collect())
    }

    fn recover(&mut self) -> Result<(), String> {
        self.status = FeedStatus::Reconnecting;
        self.connection.reconnect()?;
        self.status = FeedStatus::WarmingUp;
        self.status = FeedStatus::Ready;
        Ok(())
    }
}
