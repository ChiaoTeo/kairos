mod continuity;

use super::super::MarketActor;
use crate::domain::events::{MarketChange, MarketEvent, MarketViewUpdate};
use crate::domain::freshness::{DataFreshnessStatus, MarketFreshness};
use crate::domain::observation::order_book::{OrderBook, OrderBookDelta};
use crate::domain::subscription::selector_matches_orderbook;

impl MarketActor {
    pub(crate) fn apply_order_book_snapshot(&mut self, book: OrderBook) -> Result<u64, String> {
        if !self.order_book_is_selected(&book.market_id) {
            return Ok(self.event_sequence.get());
        }
        if self.pending_changes.len() >= Self::MAX_PENDING_EVENTS {
            return Err(format!(
                "market event backlog exceeded limit {}",
                Self::MAX_PENDING_EVENTS
            ));
        }
        let book_key = book.key();
        if let Some(current) = self.order_books.get(&book_key) {
            if current.source_id != book.source_id {
                return Err("order book snapshot source does not match existing book".into());
            }
            if current.instrument_id != book.instrument_id {
                return Err("order book snapshot identity does not match existing book".into());
            }
            if current.synchronized && book.sequence < current.sequence {
                return Err("stale order book snapshot".into());
            }
        }
        let source_id = book.source_id.clone();
        let market_id = book.market_id.clone();
        let event_time = book.event_time_unix_nanos;
        let synchronized = book.synchronized;
        self.order_books.insert(book_key, book.clone());
        self.event_sequence += 1;
        self.record_order_book_freshness(
            &source_id,
            &market_id,
            event_time.get(),
            self.event_sequence.get(),
            synchronized,
        );
        self.pending_changes.push(MarketChange {
            sequence: self.event_sequence,
            event: Some(MarketEvent::OrderBookSnapshot(book.clone())),
            view: Some(MarketViewUpdate::OrderBook(book)),
        });
        Ok(self.event_sequence.get())
    }

    pub(crate) fn apply_order_book_delta(&mut self, delta: OrderBookDelta) -> Result<u64, String> {
        if !self.order_book_is_selected(&delta.market_id) {
            return Ok(self.event_sequence.get());
        }
        if self.pending_changes.len() >= Self::MAX_PENDING_EVENTS {
            return Err(format!(
                "market event backlog exceeded limit {}",
                Self::MAX_PENDING_EVENTS
            ));
        }
        let book = self
            .order_books
            .get_mut(&format!("{}:{}", delta.source_id, delta.market_id))
            .ok_or_else(|| "order book snapshot is required before delta".to_string())?;
        if delta.last_sequence <= book.sequence {
            return Ok(self.event_sequence.get());
        }
        let delta_for_event = delta.clone();
        book.apply_delta(delta)?;
        let freshness = (
            book.source_id.clone(),
            book.market_id.clone(),
            book.event_time_unix_nanos,
            book.sequence,
        );
        let event = book.clone();
        self.event_sequence += 1;
        self.record_order_book_freshness(
            &freshness.0,
            &freshness.1,
            freshness.2.get(),
            self.event_sequence.get(),
            true,
        );
        self.pending_changes.push(MarketChange {
            sequence: self.event_sequence,
            event: Some(MarketEvent::OrderBookDelta(delta_for_event)),
            view: Some(MarketViewUpdate::OrderBook(event)),
        });
        Ok(self.event_sequence.get())
    }

    fn order_book_is_selected(&self, market_id: &str) -> bool {
        self.static_subscriptions.values().any(|subscription| {
            subscription.members.contains_key(market_id)
                && selector_matches_orderbook(&subscription.selectors)
        }) || self.dynamic_intents.values().any(|intent| {
            intent.members.contains_key(market_id) && selector_matches_orderbook(&intent.selectors)
        }) || (self.static_subscriptions.is_empty() && self.dynamic_intents.is_empty())
    }

    fn record_order_book_freshness(
        &mut self,
        source_id: &crate::SourceId,
        market_id: &str,
        event_time_unix_nanos: u64,
        sequence: u64,
        synchronized: bool,
    ) {
        self.freshness.insert(
            format!("{source_id}:{market_id}:order_book"),
            MarketFreshness {
                source_id: source_id.clone(),
                scope: crate::ObservationScope::market(market_id.to_owned())
                    .expect("validated order book market id"),
                data_kind: crate::ObservationKind::OrderBook,
                last_event_time_unix_nanos: kairos_primitives::UnixNanos::new(
                    event_time_unix_nanos,
                ),
                last_received_time_unix_nanos: kairos_primitives::UnixNanos::new(
                    super::super::now_unix_nanos(),
                ),
                event_sequence: kairos_primitives::Sequence::new(sequence),
                status: if synchronized {
                    DataFreshnessStatus::Current
                } else {
                    DataFreshnessStatus::Stale
                },
            },
        );
    }
}
