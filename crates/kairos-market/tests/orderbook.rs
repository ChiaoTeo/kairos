use kairos_market::{OrderBook, OrderBookDelta, PriceLevel};

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
