//! Runtime facade for connecting Market business state to provider feeds.

use std::time::Duration;

use crate::domain::freshness::FeedStatus;
use crate::domain::market::{MarketDescriptor, MarketSelectionQuery};
use crate::domain::reference::ReferenceChanged;
use crate::domain::snapshot::{MarketSnapshot, ReconcileResult};
use crate::domain::subscriptions::SubscriptionId;
use crate::services::connection::MarketConnectionManager;
use crate::services::feed::{MarketFeed, MarketOrderBookUpdate};
use crate::services::worker::MarketFeedWorker;

use super::{MarketApplication, MarketError};

pub struct MarketRuntime {
    application: MarketApplication,
    connection: Option<MarketConnectionManager>,
}

impl MarketRuntime {
    pub fn new(application: MarketApplication) -> Self {
        Self {
            application,
            connection: None,
        }
    }

    pub fn with_feed(application: MarketApplication, feed: Box<dyn MarketFeed>) -> Self {
        let mut runtime = Self::new(application);
        runtime.attach_feed(feed);
        runtime
    }

    pub fn application(&self) -> &MarketApplication {
        &self.application
    }

    pub fn snapshot(&self) -> MarketSnapshot {
        self.application.snapshot()
    }

    pub fn query(&self) -> super::MarketQueryResult {
        self.application.query()
    }

    pub fn drain_events(&mut self) -> Vec<(u64, crate::domain::observations::MarketObservation)> {
        self.application.drain_events()
    }

    pub fn drain_events_limited(
        &mut self,
        limit: usize,
    ) -> Vec<(u64, crate::domain::observations::MarketObservation)> {
        self.application.drain_events_limited(limit)
    }

    pub fn subscribe_static(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        market: MarketDescriptor,
    ) -> Result<(), MarketError> {
        self.application.subscribe_static(id, owner_id, market)
    }

    pub fn subscribe_dynamic(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        query: MarketSelectionQuery,
        markets: Vec<MarketDescriptor>,
    ) -> Result<ReconcileResult, MarketError> {
        self.application
            .subscribe_dynamic(id, owner_id, query, markets)
    }

    pub fn reconcile_reference(
        &mut self,
        markets: Vec<MarketDescriptor>,
    ) -> Result<std::collections::BTreeMap<SubscriptionId, ReconcileResult>, MarketError> {
        self.application.reconcile_reference(markets)
    }

    pub fn apply_reference_change(
        &mut self,
        change: ReferenceChanged,
    ) -> Result<std::collections::BTreeMap<SubscriptionId, ReconcileResult>, MarketError> {
        self.application.apply_reference_change(change)
    }

    pub fn unsubscribe(&mut self, id: &SubscriptionId) -> bool {
        self.application.unsubscribe(id)
    }

    pub fn attach_feed(&mut self, feed: Box<dyn MarketFeed>) {
        let connection = MarketConnectionManager::new(feed);
        self.application.actor.set_feed_status(connection.status());
        self.connection = Some(connection);
    }

    pub fn start_feed(&mut self) -> Result<(), MarketError> {
        let connection = self
            .connection
            .as_mut()
            .ok_or_else(|| MarketError::Invalid("market feed is not configured".into()))?;
        connection.start().map_err(MarketError::Invalid)?;
        self.application.actor.set_feed_status(connection.status());
        Ok(())
    }

    pub fn start_feed_worker(&mut self, poll_interval: Duration) -> Result<(), MarketError> {
        let connection = self
            .connection
            .take()
            .ok_or_else(|| MarketError::Invalid("market feed is not configured".into()))?;
        self.connection = Some(MarketConnectionManager::new(Box::new(
            MarketFeedWorker::start(connection.into_feed(), poll_interval),
        )));
        self.application.actor.set_feed_status(
            self.connection
                .as_ref()
                .expect("connection exists")
                .status(),
        );
        Ok(())
    }

    pub fn poll_feed(&mut self) -> Result<usize, MarketError> {
        let result = self.poll_connection();
        if let Err(error) = &result {
            tracing::warn!(event = "market_poll_failed", component = "market", error = %error, "market poll failed");
        }
        result
    }

    /// Retry provider reconciliation without changing business intent. A
    /// transient provider failure must not permanently leave actual
    /// subscriptions behind the actor's desired state.
    pub fn reconcile_feed(&mut self) -> Result<(), MarketError> {
        self.reconcile_connections().map_err(MarketError::Invalid)
    }

    pub fn recover_feed(&mut self) -> Result<(), MarketError> {
        let connection = self
            .connection
            .as_mut()
            .ok_or_else(|| MarketError::Invalid("market feed is not configured".into()))?;
        self.application
            .actor
            .set_feed_status(FeedStatus::Reconnecting);
        connection.recover().map_err(MarketError::Invalid)?;
        self.application
            .actor
            .set_feed_status(FeedStatus::WarmingUp);
        Ok(())
    }

    fn reconcile_connections(&mut self) -> Result<(), String> {
        let subscriptions = self.application.snapshot().subscriptions;
        if let Some(connection) = self.connection.as_mut() {
            connection.reconcile(&subscriptions)?;
            self.application.actor.set_feed_status(connection.status());
        }
        Ok(())
    }

    fn poll_connection(&mut self) -> Result<usize, MarketError> {
        let connection = self
            .connection
            .as_mut()
            .ok_or_else(|| MarketError::Invalid("market feed is not configured".into()))?;
        let batch = connection.poll().map_err(MarketError::Invalid)?;
        self.application.actor.set_feed_status(connection.status());
        let count = batch.observations.len() + batch.orderbooks.len();
        for observation in batch.observations {
            self.application.ingest(observation)?;
        }
        for update in batch.orderbooks {
            let market_id = update.market_id.clone();
            if let Err(error) = self.apply_orderbook_update(update) {
                if !is_orderbook_resync_error(&error.to_string()) {
                    return Err(error);
                }
                self.application
                    .actor
                    .set_feed_status(FeedStatus::WarmingUp);
                match self.resync_orderbook(&market_id) {
                    Ok(()) => tracing::warn!(
                        event = "market_orderbook_resync_requested",
                        component = "market",
                        market_id = %market_id,
                        "requested single market order book resync"
                    ),
                    Err(resync_error) => {
                        tracing::warn!(
                            event = "market_orderbook_resync_unavailable",
                            component = "market",
                            market_id = %market_id,
                            error = %resync_error,
                            "single market order book resync is unavailable"
                        );
                        self.application.actor.set_feed_status(FeedStatus::Degraded);
                        return Err(MarketError::Invalid(format!(
                            "order book resync unavailable for {market_id}: {resync_error}"
                        )));
                    }
                }
            }
        }
        Ok(count)
    }

    pub fn resync_orderbook(&mut self, market_id: &str) -> Result<(), MarketError> {
        let connection = self
            .connection
            .as_mut()
            .ok_or_else(|| MarketError::Invalid("market feed is not configured".into()))?;
        connection
            .resync_orderbook(market_id)
            .map_err(MarketError::Invalid)
    }

    fn apply_orderbook_update(&mut self, update: MarketOrderBookUpdate) -> Result<(), MarketError> {
        if update.snapshot {
            let book = crate::domain::orderbook::OrderBook::snapshot(
                update.market_id,
                update.instrument_id,
                update.last_sequence,
                update.event_time_unix_nanos,
                update.bids,
                update.asks,
            )
            .map_err(MarketError::Invalid)?;
            self.application.ingest_orderbook_snapshot(book).map(|_| ())
        } else {
            self.application
                .ingest_orderbook_delta(crate::domain::orderbook::OrderBookDelta {
                    market_id: update.market_id,
                    instrument_id: update.instrument_id,
                    first_sequence: update.first_sequence,
                    last_sequence: update.last_sequence,
                    event_time_unix_nanos: update.event_time_unix_nanos,
                    bids: update.bids,
                    asks: update.asks,
                })
                .map(|_| ())
        }
    }
}

fn is_orderbook_resync_error(error: &str) -> bool {
    error.contains("order book sequence gap")
        || error.contains("order book is not synchronized")
        || error.contains("order book snapshot is required")
}
