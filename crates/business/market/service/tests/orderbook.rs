use kairos_market::composition::{MarketFeed, MarketOrderBookUpdate};
use kairos_market::{
    MarketApplication, MarketDescriptor, MarketRuntime, OrderBook, OrderBookDelta, PriceLevel,
    SubscriptionId,
};
use std::sync::{Arc, Mutex};

#[test]
fn orderbook_applies_contiguous_deltas() {
    let mut book = OrderBook::snapshot(
        "market:btc",
        "instrument:btc",
        10,
        1,
        vec![PriceLevel {
            price: "100".into(),
            quantity: "2".into(),
        }],
        vec![],
    )
    .unwrap();
    book.apply_delta(OrderBookDelta {
        market_id: "market:btc".into(),
        instrument_id: "instrument:btc".into(),
        first_sequence: 11,
        last_sequence: 11,
        event_time_unix_nanos: 2,
        bids: vec![PriceLevel {
            price: "100".into(),
            quantity: "3".into(),
        }],
        asks: vec![],
    })
    .unwrap();
    assert_eq!(book.sequence, 11);
    assert_eq!(book.bids[0].quantity, "3");
}

#[test]
fn orderbook_gap_marks_book_unsynchronized_until_snapshot() {
    let mut book =
        OrderBook::snapshot("market:btc", "instrument:btc", 10, 1, vec![], vec![]).unwrap();
    assert!(book
        .apply_delta(OrderBookDelta {
            market_id: "market:btc".into(),
            instrument_id: "instrument:btc".into(),
            first_sequence: 12,
            last_sequence: 12,
            event_time_unix_nanos: 2,
            bids: vec![],
            asks: vec![],
        })
        .is_err());
    assert!(!book.synchronized);
    assert!(book
        .apply_delta(OrderBookDelta {
            market_id: "market:btc".into(),
            instrument_id: "instrument:btc".into(),
            first_sequence: 11,
            last_sequence: 11,
            event_time_unix_nanos: 3,
            bids: vec![],
            asks: vec![],
        })
        .is_err());
}

struct ResyncFeed {
    phase: u8,
    resyncs: Arc<Mutex<Vec<String>>>,
}

impl MarketFeed for ResyncFeed {
    fn subscribe(&mut self, _market: &MarketDescriptor) -> Result<SubscriptionId, String> {
        SubscriptionId::new("provider:1")
    }

    fn unsubscribe(&mut self, _subscription: &SubscriptionId) -> Result<(), String> {
        Ok(())
    }

    fn poll(&mut self) -> Result<Vec<kairos_market::MarketObservation>, String> {
        Ok(Vec::new())
    }

    fn poll_orderbooks(&mut self) -> Result<Vec<MarketOrderBookUpdate>, String> {
        let update = match self.phase {
            0 => Some(MarketOrderBookUpdate {
                market_id: "market:btc".into(),
                instrument_id: "instrument:btc".into(),
                first_sequence: 0,
                last_sequence: 10,
                event_time_unix_nanos: 1,
                bids: vec![],
                asks: vec![],
                snapshot: true,
            }),
            1 => Some(MarketOrderBookUpdate {
                market_id: "market:btc".into(),
                instrument_id: "instrument:btc".into(),
                first_sequence: 12,
                last_sequence: 12,
                event_time_unix_nanos: 2,
                bids: vec![],
                asks: vec![],
                snapshot: false,
            }),
            _ => None,
        };
        self.phase += 1;
        Ok(update.into_iter().collect())
    }

    fn resync_orderbook(&mut self, market_id: &str) -> Result<(), String> {
        self.resyncs.lock().unwrap().push(market_id.into());
        Ok(())
    }
}

struct NoFallbackFeed {
    recoveries: Arc<Mutex<u32>>,
}

impl MarketFeed for NoFallbackFeed {
    fn subscribe(&mut self, _market: &MarketDescriptor) -> Result<SubscriptionId, String> {
        SubscriptionId::new("provider:no-fallback")
    }

    fn unsubscribe(&mut self, _subscription: &SubscriptionId) -> Result<(), String> {
        Ok(())
    }

    fn poll(&mut self) -> Result<Vec<kairos_market::MarketObservation>, String> {
        Ok(Vec::new())
    }

    fn poll_orderbooks(&mut self) -> Result<Vec<MarketOrderBookUpdate>, String> {
        Ok(vec![MarketOrderBookUpdate {
            market_id: "market:btc".into(),
            instrument_id: "instrument:btc".into(),
            first_sequence: 12,
            last_sequence: 12,
            event_time_unix_nanos: 1,
            bids: vec![],
            asks: vec![],
            snapshot: false,
        }])
    }

    fn recover(&mut self) -> Result<(), String> {
        *self.recoveries.lock().unwrap() += 1;
        Ok(())
    }
}

#[test]
fn runtime_requests_single_orderbook_resync_after_a_sequence_gap() {
    let resyncs = Arc::new(Mutex::new(Vec::new()));
    let application = MarketApplication::new("market-1", 10).unwrap();
    let mut runtime = MarketRuntime::with_feed(
        application,
        Box::new(ResyncFeed {
            phase: 0,
            resyncs: Arc::clone(&resyncs),
        }),
    );
    runtime.start_feed().unwrap();
    runtime
        .subscribe_static(
            SubscriptionId::new("subscription-1").unwrap(),
            "strategy-1",
            MarketDescriptor::new("market:btc", "instrument:btc", "binance", "spot", "BTCUSDT")
                .unwrap(),
        )
        .unwrap();
    runtime.reconcile_feed().unwrap();
    runtime.poll_feed().unwrap();
    runtime.poll_feed().unwrap();
    assert_eq!(*resyncs.lock().unwrap(), vec!["market:btc"]);
}

#[test]
fn runtime_does_not_fallback_to_full_feed_recovery_for_orderbook_gap() {
    let recoveries = Arc::new(Mutex::new(0));
    let application = MarketApplication::new("market-1", 10).unwrap();
    let mut runtime = MarketRuntime::with_feed(
        application,
        Box::new(NoFallbackFeed {
            recoveries: Arc::clone(&recoveries),
        }),
    );
    runtime.start_feed().unwrap();
    runtime
        .subscribe_static(
            SubscriptionId::new("subscription-1").unwrap(),
            "strategy-1",
            MarketDescriptor::new("market:btc", "instrument:btc", "binance", "spot", "BTCUSDT")
                .unwrap(),
        )
        .unwrap();
    runtime.reconcile_feed().unwrap();

    let error = runtime.poll_feed().unwrap_err();
    assert!(error.to_string().contains("order book resync unavailable"));
    assert_eq!(*recoveries.lock().unwrap(), 0);
}
