use kairos_market::{
    MarketApplication, MarketDescriptor, OrderBook, OrderBookDelta, OrderBookSide, PriceLevel,
    SubscriptionId,
};

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
