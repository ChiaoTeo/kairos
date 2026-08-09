//! Lazy multi-connection market feed routing.

use std::collections::BTreeMap;
use std::time::Duration;

use super::feed::{MarketFeed, MarketOrderBookUpdate};
use super::worker::MarketFeedWorker;
use crate::domain::freshness::FeedStatus;
use crate::domain::market::MarketDescriptor;
use crate::domain::observations::MarketObservation;
use crate::domain::subscriptions::SubscriptionId;

pub type MarketFeedFactory = Box<dyn Fn() -> Result<Box<dyn MarketFeed>, String> + Send + Sync>;

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct MarketRoute {
    pub venue_id: String,
    pub market_type: String,
    pub asset_type: Option<String>,
}

impl MarketRoute {
    pub fn new(venue_id: impl Into<String>, market_type: impl Into<String>) -> Self {
        Self {
            venue_id: venue_id.into().trim().to_ascii_lowercase(),
            market_type: market_type.into().trim().to_ascii_lowercase(),
            asset_type: None,
        }
    }

    pub fn with_asset_type(
        venue_id: impl Into<String>,
        market_type: impl Into<String>,
        asset_type: impl Into<String>,
    ) -> Self {
        let mut route = Self::new(venue_id, market_type);
        route.asset_type = Some(asset_type.into().trim().to_ascii_lowercase());
        route
    }

    pub fn from_market(market: &MarketDescriptor) -> Self {
        match market.asset_type.as_deref() {
            Some(asset_type) => {
                Self::with_asset_type(&market.venue_id, &market.market_type, asset_type)
            }
            None => Self::new(&market.venue_id, &market.market_type),
        }
    }
}

struct RoutedSubscription {
    route: MarketRoute,
    inner: SubscriptionId,
    market_id: String,
}

/// A Market-owned collection of lazily-created provider connections.
pub struct CompositeMarketFeed {
    factories: BTreeMap<MarketRoute, MarketFeedFactory>,
    workers: BTreeMap<MarketRoute, MarketFeedWorker>,
    subscriptions: BTreeMap<SubscriptionId, RoutedSubscription>,
    next_id: u64,
    status: FeedStatus,
}

impl CompositeMarketFeed {
    pub fn new(factories: BTreeMap<MarketRoute, MarketFeedFactory>) -> Result<Self, String> {
        if factories.is_empty() {
            return Err("composite market feed requires at least one route".into());
        }
        Ok(Self {
            factories,
            workers: BTreeMap::new(),
            subscriptions: BTreeMap::new(),
            next_id: 1,
            status: FeedStatus::Disconnected,
        })
    }

    fn worker_for(&mut self, route: &MarketRoute) -> Result<&mut MarketFeedWorker, String> {
        if !self.workers.contains_key(route) {
            let factory = self.factories.get(route).ok_or_else(|| {
                format!(
                    "no market connection route for {}:{}",
                    route.venue_id, route.market_type
                )
            })?;
            let feed = factory()?;
            self.workers.insert(
                route.clone(),
                MarketFeedWorker::start(feed, Duration::from_millis(10)),
            );
        }
        self.workers.get_mut(route).ok_or_else(|| {
            format!(
                "market connection was not created for {}:{}",
                route.venue_id, route.market_type
            )
        })
    }

    pub fn active_routes(&self) -> impl Iterator<Item = &MarketRoute> {
        self.workers.keys()
    }

    /// Routes declared by the workspace connection directory. This is useful
    /// for startup validation; a route still creates its concrete provider
    /// lazily on the first strategy subscription.
    pub fn configured_routes(&self) -> impl Iterator<Item = &MarketRoute> {
        self.factories.keys()
    }
}

impl MarketFeed for CompositeMarketFeed {
    fn start(&mut self) -> Result<(), String> {
        // Route workers are started lazily when their first subscription is
        // created. This method remains a lifecycle no-op for the composite.
        self.status = self.status();
        Ok(())
    }

    fn status(&self) -> FeedStatus {
        if self.workers.is_empty() {
            return self.status;
        }
        let mut statuses = self.workers.values().map(MarketFeed::status);
        if statuses
            .clone()
            .any(|status| status == FeedStatus::Degraded)
        {
            FeedStatus::Degraded
        } else if statuses
            .clone()
            .any(|status| status == FeedStatus::Reconnecting)
        {
            FeedStatus::Reconnecting
        } else if statuses
            .clone()
            .any(|status| status == FeedStatus::WarmingUp)
        {
            FeedStatus::WarmingUp
        } else if statuses.any(|status| status == FeedStatus::Ready) {
            FeedStatus::Ready
        } else {
            self.status
        }
    }

    fn subscribe(&mut self, market: &MarketDescriptor) -> Result<SubscriptionId, String> {
        market.validate()?;
        let route = MarketRoute::from_market(market);
        let worker = self.worker_for(&route)?;
        let inner = worker.subscribe(market)?;
        let id = SubscriptionId::new(format!("composite:{}", self.next_id))?;
        self.next_id += 1;
        self.subscriptions.insert(
            id.clone(),
            RoutedSubscription {
                route,
                inner,
                market_id: market.market_id.clone(),
            },
        );
        self.status = FeedStatus::Ready;
        Ok(id)
    }

    fn recover(&mut self) -> Result<(), String> {
        for worker in self.workers.values_mut() {
            worker.recover()?;
        }
        self.status = self.status();
        Ok(())
    }

    fn resync_orderbook(&mut self, market_id: &str) -> Result<(), String> {
        let routes = self
            .subscriptions
            .values()
            .filter(|subscription| subscription.market_id == market_id)
            .collect::<Vec<_>>();
        let routed = match routes.as_slice() {
            [] => {
                return Err(format!(
                    "unknown composite market for order book resync: {market_id}"
                ))
            }
            [single] => *single,
            _ => {
                return Err(format!(
                    "composite market has multiple routes for order book resync: {market_id}"
                ))
            }
        };
        self.workers
            .get_mut(&routed.route)
            .ok_or_else(|| "composite route worker is not running".to_string())?
            .resync_orderbook(market_id)
    }

    fn unsubscribe(&mut self, subscription: &SubscriptionId) -> Result<(), String> {
        let routed = self
            .subscriptions
            .remove(subscription)
            .ok_or_else(|| format!("unknown composite market subscription: {}", subscription.0))?;
        self.worker_for(&routed.route)?.unsubscribe(&routed.inner)
    }

    fn poll(&mut self) -> Result<Vec<MarketObservation>, String> {
        let mut values = Vec::new();
        let mut first_error = None;
        for worker in self.workers.values_mut() {
            match worker.poll() {
                Ok(events) => values.extend(events),
                Err(error) => {
                    self.status = FeedStatus::Degraded;
                    first_error.get_or_insert(error);
                }
            }
        }
        if values.is_empty() {
            if let Some(error) = first_error {
                return Err(error);
            }
        }
        Ok(values)
    }

    fn poll_orderbooks(&mut self) -> Result<Vec<MarketOrderBookUpdate>, String> {
        let mut values = Vec::new();
        let mut first_error = None;
        for worker in self.workers.values_mut() {
            match worker.poll_orderbooks() {
                Ok(updates) => values.extend(updates),
                Err(error) => {
                    self.status = FeedStatus::Degraded;
                    first_error.get_or_insert(error);
                }
            }
        }
        if values.is_empty() {
            if let Some(error) = first_error {
                return Err(error);
            }
        }
        Ok(values)
    }
}

#[cfg(test)]
mod tests {
    use super::{CompositeMarketFeed, MarketFeedFactory, MarketRoute};
    use crate::domain::freshness::FeedStatus;
    use crate::domain::market::MarketDescriptor;
    use crate::domain::observations::MarketObservation;
    use crate::domain::subscriptions::SubscriptionId;
    use crate::services::feed::{MarketFeed, MarketOrderBookUpdate};
    use std::collections::BTreeMap;

    struct FakeFeed {
        source: String,
        subscribed: usize,
    }

    impl MarketFeed for FakeFeed {
        fn status(&self) -> FeedStatus {
            FeedStatus::Ready
        }
        fn subscribe(&mut self, _market: &MarketDescriptor) -> Result<SubscriptionId, String> {
            self.subscribed += 1;
            SubscriptionId::new(format!("{}-{}", self.source, self.subscribed))
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
    }

    struct StartAwareFeed {
        started: bool,
    }

    impl MarketFeed for StartAwareFeed {
        fn start(&mut self) -> Result<(), String> {
            self.started = true;
            Ok(())
        }

        fn status(&self) -> FeedStatus {
            if self.started {
                FeedStatus::Ready
            } else {
                FeedStatus::Disconnected
            }
        }

        fn subscribe(&mut self, _market: &MarketDescriptor) -> Result<SubscriptionId, String> {
            if !self.started {
                return Err("feed was not started".into());
            }
            SubscriptionId::new("started-subscription")
        }

        fn unsubscribe(&mut self, _subscription: &SubscriptionId) -> Result<(), String> {
            Ok(())
        }

        fn poll(&mut self) -> Result<Vec<MarketObservation>, String> {
            Ok(Vec::new())
        }
    }

    #[test]
    fn starts_a_lazy_route_before_subscribing() {
        let mut factories = BTreeMap::new();
        factories.insert(
            MarketRoute::new("binance", "spot"),
            Box::new(|| Ok(Box::new(StartAwareFeed { started: false }) as Box<dyn MarketFeed>))
                as MarketFeedFactory,
        );
        let mut feed = CompositeMarketFeed::new(factories).unwrap();
        feed.subscribe(&MarketDescriptor::new("m1", "i1", "binance", "spot", "BTCUSDT").unwrap())
            .unwrap();
        assert_eq!(feed.status(), FeedStatus::Ready);
    }

    #[test]
    fn creates_and_reuses_independent_routes() {
        let mut factories: BTreeMap<MarketRoute, MarketFeedFactory> = BTreeMap::new();
        for (venue, market_type) in [("binance", "spot"), ("okx", "options")] {
            let source = format!("{venue}-{market_type}");
            factories.insert(
                MarketRoute::new(venue, market_type),
                Box::new(move || {
                    Ok(Box::new(FakeFeed {
                        source: source.clone(),
                        subscribed: 0,
                    }) as Box<dyn MarketFeed>)
                }),
            );
        }
        let mut feed = CompositeMarketFeed::new(factories).unwrap();
        feed.subscribe(&MarketDescriptor::new("m1", "i1", "binance", "spot", "BTCUSDT").unwrap())
            .unwrap();
        feed.subscribe(&MarketDescriptor::new("m2", "i2", "binance", "spot", "ETHUSDT").unwrap())
            .unwrap();
        feed.subscribe(
            &MarketDescriptor::new("m3", "i3", "okx", "options", "BTC-USD-240628-50000-C").unwrap(),
        )
        .unwrap();
        assert_eq!(feed.active_routes().count(), 2);
    }

    #[test]
    fn separates_okx_spot_crypto_and_equity_routes() {
        let mut factories: BTreeMap<MarketRoute, MarketFeedFactory> = BTreeMap::new();
        for asset_type in ["crypto", "equity"] {
            let route = MarketRoute::with_asset_type("okx", "spot", asset_type);
            factories.insert(
                route,
                Box::new(|| {
                    Ok(Box::new(FakeFeed {
                        source: "okx-spot".into(),
                        subscribed: 0,
                    }) as Box<dyn MarketFeed>)
                }),
            );
        }
        let mut feed = CompositeMarketFeed::new(factories).unwrap();
        feed.subscribe(
            &MarketDescriptor::new_with_asset_type("m1", "i1", "okx", "spot", "crypto", "BTC-USDT")
                .unwrap(),
        )
        .unwrap();
        feed.subscribe(
            &MarketDescriptor::new_with_asset_type("m2", "i2", "okx", "spot", "equity", "AAPL")
                .unwrap(),
        )
        .unwrap();
        assert_eq!(feed.active_routes().count(), 2);
    }

    #[test]
    fn one_market_process_can_subscribe_every_required_product_family() {
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
        let mut factories = BTreeMap::new();
        for (venue, market_type, asset_type) in routes {
            factories.insert(
                MarketRoute::with_asset_type(venue, market_type, asset_type),
                Box::new(|| {
                    Ok(Box::new(FakeFeed {
                        source: "all-products".into(),
                        subscribed: 0,
                    }) as Box<dyn MarketFeed>)
                }) as MarketFeedFactory,
            );
        }
        let mut feed = CompositeMarketFeed::new(factories).unwrap();
        for (index, (venue, market_type, asset_type)) in routes.into_iter().enumerate() {
            let descriptor = MarketDescriptor::new_with_asset_type(
                format!("market:{index}"),
                format!("instrument:{index}"),
                venue,
                market_type,
                asset_type,
                format!("SYMBOL{index}"),
            )
            .unwrap();
            feed.subscribe(&descriptor).unwrap();
        }
        assert_eq!(feed.active_routes().count(), routes.len());
    }
}
