use kairos_market::{
    MarketApplication, MarketDataKey, MarketDescriptor, MarketRuntime, OrderBook, OrderBookDelta,
    OrderBookSide, PriceLevel, SubscriptionId,
};
use kairos_market::{MarketFeed, MarketOrderBookUpdate};
use std::sync::{Arc, Mutex};

#[test]
fn orderbook_applies_contiguous_deltas() {
    let mut book = OrderBook::snapshot(
        "market:btc",
        "instrument:btc",
        10,
        1,
        vec![PriceLevel {
            price: "100".parse().unwrap(),
            quantity: "2".parse().unwrap(),
        }],
        vec![],
    )
    .unwrap();
    book.apply_delta(OrderBookDelta {
        source_id: "market".into(),
        market_id: kairos_domain_types::MarketId::new("market:btc").unwrap(),
        instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc").unwrap(),
        first_sequence: 11.into(),
        last_sequence: 11.into(),
        event_time_unix_nanos: 2.into(),
        bids: vec![PriceLevel {
            price: "100".parse().unwrap(),
            quantity: "3".parse().unwrap(),
        }],
        asks: vec![],
        checksum: None,
    })
    .unwrap();
    assert_eq!(book.sequence, 11.into());
    assert_eq!(book.bids[0].quantity.to_string(), "3");
}

#[test]
fn query_estimates_execution_with_decimal_vwap_and_slippage() {
    let descriptor =
        MarketDescriptor::new("market:btc", "instrument:btc", "binance", "spot", "BTCUSDT")
            .unwrap();
    let mut application = MarketApplication::new("market-1", 10).unwrap();
    application
        .subscribe_static(
            SubscriptionId::new("sub-1").unwrap(),
            "strategy-1",
            descriptor,
        )
        .unwrap();
    application
        .ingest_orderbook_snapshot(
            OrderBook::snapshot_with_source(
                "binance.spot",
                "market:btc",
                "instrument:btc",
                1,
                1,
                vec![PriceLevel {
                    price: "99".parse().unwrap(),
                    quantity: "5".parse().unwrap(),
                }],
                vec![
                    PriceLevel {
                        price: "101".parse().unwrap(),
                        quantity: "2".parse().unwrap(),
                    },
                    PriceLevel {
                        price: "102".parse().unwrap(),
                        quantity: "3".parse().unwrap(),
                    },
                ],
            )
            .unwrap(),
        )
        .unwrap();

    let estimate = application
        .query()
        .estimate_execution("binance.spot", "market:btc", OrderBookSide::Buy, "3")
        .unwrap();
    assert_eq!(estimate.vwap.to_string(), "101.3333333333333333");
    assert_eq!(estimate.filled_quantity.to_string(), "3");
    assert_eq!(estimate.slippage_abs.to_string(), "0.333333333333333333");
}

#[test]
fn orderbook_accepts_overlapping_provider_delta_ranges() {
    let mut book =
        OrderBook::snapshot("market:btc", "instrument:btc", 10, 1, vec![], vec![]).unwrap();
    book.apply_delta(OrderBookDelta {
        source_id: "market".into(),
        market_id: kairos_domain_types::MarketId::new("market:btc").unwrap(),
        instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc").unwrap(),
        first_sequence: 9.into(),
        last_sequence: 12.into(),
        event_time_unix_nanos: 2.into(),
        bids: vec![],
        asks: vec![],
        checksum: None,
    })
    .unwrap();
    assert_eq!(book.sequence, 12.into());
}

#[test]
fn orderbook_gap_marks_book_unsynchronized_until_snapshot() {
    let mut book =
        OrderBook::snapshot("market:btc", "instrument:btc", 10, 1, vec![], vec![]).unwrap();
    assert!(book
        .apply_delta(OrderBookDelta {
            source_id: "market".into(),
            market_id: kairos_domain_types::MarketId::new("market:btc").unwrap(),
            instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc").unwrap(),
            first_sequence: 12.into(),
            last_sequence: 12.into(),
            event_time_unix_nanos: 2.into(),
            bids: vec![],
            asks: vec![],
            checksum: None,
        })
        .is_err());
    assert!(!book.synchronized);
    assert!(book
        .apply_delta(OrderBookDelta {
            source_id: "market".into(),
            market_id: kairos_domain_types::MarketId::new("market:btc").unwrap(),
            instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc").unwrap(),
            first_sequence: 11.into(),
            last_sequence: 11.into(),
            event_time_unix_nanos: 3.into(),
            bids: vec![],
            asks: vec![],
            checksum: None,
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
                source_id: "test".into(),
                market_id: kairos_domain_types::MarketId::new("market:btc").unwrap(),
                instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc").unwrap(),
                first_sequence: 0.into(),
                last_sequence: 10.into(),
                event_time_unix_nanos: 1.into(),
                bids: vec![],
                asks: vec![],
                snapshot: true,
            }),
            1 => Some(MarketOrderBookUpdate {
                source_id: "test".into(),
                market_id: kairos_domain_types::MarketId::new("market:btc").unwrap(),
                instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc").unwrap(),
                first_sequence: 12.into(),
                last_sequence: 12.into(),
                event_time_unix_nanos: 2.into(),
                bids: vec![],
                asks: vec![],
                snapshot: false,
            }),
            _ => None,
        };
        self.phase += 1;
        Ok(update.into_iter().collect())
    }

    fn resync_orderbook(&mut self, key: &MarketDataKey) -> Result<(), String> {
        self.resyncs.lock().unwrap().push(key.market_id.to_string());
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
            source_id: "test".into(),
            market_id: kairos_domain_types::MarketId::new("market:btc").unwrap(),
            instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc").unwrap(),
            first_sequence: 12.into(),
            last_sequence: 12.into(),
            event_time_unix_nanos: 1.into(),
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
