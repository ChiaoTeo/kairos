use std::collections::BTreeMap;

use kairos_protocol::generated::kairos::market::v_1 as market_fb;

fn fixtures() -> BTreeMap<String, Vec<u8>> {
    let mut values: BTreeMap<String, String> = serde_json::from_str(include_str!(
        "../../../../../tests/fixtures/active_wire/v1.json"
    ))
    .expect("valid active Market fixture manifest");
    values.extend(
        serde_json::from_str::<BTreeMap<String, String>>(include_str!(
            "../../../../../tests/fixtures/active_wire/market_aux_v1.json"
        ))
        .expect("valid auxiliary Market fixture manifest"),
    );
    values
        .into_iter()
        .map(|(name, value)| (name, decode_hex(&value)))
        .collect()
}

fn decode_hex(value: &str) -> Vec<u8> {
    assert_eq!(value.len() % 2, 0);
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let pair = std::str::from_utf8(pair).expect("ASCII hex");
            u8::from_str_radix(pair, 16).expect("valid hex byte")
        })
        .collect()
}

macro_rules! rejects_bad_identifier {
    ($payload:expr, $identifier_check:path) => {{
        assert!($identifier_check(&$payload));
        let mut corrupted = $payload.clone();
        corrupted[4..8].copy_from_slice(b"BAD1");
        assert!(!$identifier_check(&corrupted));
    }};
}

#[test]
fn rust_decodes_the_same_active_market_roots_as_python() {
    let fixtures = fixtures();

    let quote = &fixtures["market.quote.MQT1"];
    assert!(market_fb::quote_message_buffer_has_identifier(quote));
    let quote = market_fb::root_as_quote_message(quote).unwrap();
    assert_eq!(quote.header().sequence(), 1);
    assert_eq!(quote.payload().market_id(), Some("market:fixture"));
    rejects_bad_identifier!(
        fixtures["market.quote.MQT1"],
        market_fb::quote_message_buffer_has_identifier
    );

    let trade = &fixtures["market.trade.MTR1"];
    assert!(market_fb::trade_message_buffer_has_identifier(trade));
    let trade = market_fb::root_as_trade_message(trade).unwrap();
    assert_eq!(trade.header().sequence(), 2);
    assert_eq!(trade.payload().trade_id(), Some("trade:fixture"));
    rejects_bad_identifier!(
        fixtures["market.trade.MTR1"],
        market_fb::trade_message_buffer_has_identifier
    );

    let bar = &fixtures["market.bar.MBA1"];
    assert!(market_fb::bar_message_buffer_has_identifier(bar));
    let bar = market_fb::root_as_bar_message(bar).unwrap();
    assert_eq!(bar.header().sequence(), 3);
    assert_eq!(bar.payload().timeframe(), "1m");
    rejects_bad_identifier!(
        fixtures["market.bar.MBA1"],
        market_fb::bar_message_buffer_has_identifier
    );

    let greeks = &fixtures["market.greeks.MGR1"];
    assert!(market_fb::greeks_message_buffer_has_identifier(greeks));
    let greeks = market_fb::root_as_greeks_message(greeks).unwrap();
    assert_eq!(greeks.header().sequence(), 4);
    assert_eq!(greeks.payload().delta().unwrap().mantissa(), 525);
    rejects_bad_identifier!(
        fixtures["market.greeks.MGR1"],
        market_fb::greeks_message_buffer_has_identifier
    );

    let current = &fixtures["market.current.PMC1"];
    assert!(market_fb::market_data_snapshot_buffer_has_identifier(
        current
    ));
    let current = market_fb::root_as_market_data_snapshot(current).unwrap();
    assert_eq!(current.header().version(), 1);
    assert_eq!(current.header().generation(), 7);
    assert_eq!(current.payload().quote_count(), 0);
    rejects_bad_identifier!(
        fixtures["market.current.PMC1"],
        market_fb::market_data_snapshot_buffer_has_identifier
    );

    let auxiliary = [
        ("market.rate.MRA1", b"MRA1"),
        ("market.ticker_24h.MT24", b"MT24"),
        ("market.mark_price.MMP1", b"MMP1"),
        ("market.orderbook.MOB1", b"MOB1"),
        ("market.index_price.MIP1", b"MIP1"),
        ("market.funding_rate.MFR1", b"MFR1"),
        ("market.open_interest.MOI1", b"MOI1"),
        ("market.instrument_status.MIS1", b"MIS1"),
        ("market.orderbook.current.PMB1", b"PMB1"),
    ];
    for (name, identifier) in auxiliary {
        assert_eq!(&fixtures[name][4..8], identifier, "{name}");
    }
    assert_eq!(
        market_fb::root_as_rate_message(&fixtures["market.rate.MRA1"])
            .unwrap()
            .header()
            .sequence(),
        5
    );
    assert_eq!(
        market_fb::root_as_order_book_message(&fixtures["market.orderbook.MOB1"])
            .unwrap()
            .payload()
            .sequence(),
        8
    );
    assert_eq!(
        market_fb::root_as_order_book_snapshot(&fixtures["market.orderbook.current.PMB1"])
            .unwrap()
            .header()
            .generation(),
        7
    );
    market_fb::root_as_ticker_24h_message(&fixtures["market.ticker_24h.MT24"]).unwrap();
    market_fb::root_as_mark_price_message(&fixtures["market.mark_price.MMP1"]).unwrap();
    market_fb::root_as_index_price_message(&fixtures["market.index_price.MIP1"]).unwrap();
    market_fb::root_as_funding_rate_message(&fixtures["market.funding_rate.MFR1"]).unwrap();
    market_fb::root_as_open_interest_message(&fixtures["market.open_interest.MOI1"]).unwrap();
    market_fb::root_as_instrument_status_message(&fixtures["market.instrument_status.MIS1"])
        .unwrap();
    rejects_bad_identifier!(
        fixtures["market.rate.MRA1"],
        market_fb::rate_message_buffer_has_identifier
    );
    rejects_bad_identifier!(
        fixtures["market.ticker_24h.MT24"],
        market_fb::ticker_24h_message_buffer_has_identifier
    );
    rejects_bad_identifier!(
        fixtures["market.mark_price.MMP1"],
        market_fb::mark_price_message_buffer_has_identifier
    );
    rejects_bad_identifier!(
        fixtures["market.orderbook.MOB1"],
        market_fb::order_book_message_buffer_has_identifier
    );
    rejects_bad_identifier!(
        fixtures["market.index_price.MIP1"],
        market_fb::index_price_message_buffer_has_identifier
    );
    rejects_bad_identifier!(
        fixtures["market.funding_rate.MFR1"],
        market_fb::funding_rate_message_buffer_has_identifier
    );
    rejects_bad_identifier!(
        fixtures["market.open_interest.MOI1"],
        market_fb::open_interest_message_buffer_has_identifier
    );
    rejects_bad_identifier!(
        fixtures["market.instrument_status.MIS1"],
        market_fb::instrument_status_message_buffer_has_identifier
    );
    rejects_bad_identifier!(
        fixtures["market.orderbook.current.PMB1"],
        market_fb::order_book_snapshot_buffer_has_identifier
    );
}
