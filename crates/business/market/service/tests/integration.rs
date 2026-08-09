use kairos_market::composition::binance_spot_rest_feed;
use kairos_market::composition::MmapMarketSnapshotPublisher;
use kairos_market::SubscriptionId;
use kairos_market::Trade;
use kairos_market::{
    Bar, MarketApplication, MarketDescriptor, MarketObservation, OptionGreeks, OrderBook,
    PriceLevel, Quote,
};
use kairos_protocol::generated::kairos::market::v_1::root_as_market_data_snapshot;
use kairos_protocol::InstanceIdentity;
use kairos_transport::SharedSnapshotReader;

#[test]
fn binance_spot_feed_is_selected_in_composition_without_exposing_provider_types() {
    let feed = binance_spot_rest_feed("https://api.binance.com").unwrap();
    let _ = feed;
}

#[test]
fn market_snapshot_publishes_through_shared_memory_reader() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("market.snapshot");
    let mut actor = MarketApplication::new("market-1", 10).unwrap();
    actor
        .ingest(MarketObservation::Quote(Quote {
            market_id: "market:btc".into(),
            instrument_id: "instrument:btc".into(),
            bid_price: Some("100.25".into()),
            bid_quantity: Some("1".into()),
            ask_price: None,
            ask_quantity: None,
            observed_at_unix_nanos: 1,
            source_id: "binance".into(),
        }))
        .unwrap();
    let mut publisher = MmapMarketSnapshotPublisher::create_with_identity(
        &path,
        64 * 1024,
        "market-1",
        "market.events",
        InstanceIdentity::new("demo", "btc-sma", "run-001"),
    )
    .unwrap();
    publisher.publish(&actor.snapshot()).unwrap();
    let payload = SharedSnapshotReader::open(path)
        .unwrap()
        .read_payload()
        .unwrap();
    let snapshot = root_as_market_data_snapshot(&payload.payload).unwrap();
    assert_eq!(snapshot.header().owner_actor_id(), "market-1");
    assert_eq!(snapshot.header().workspace_id(), Some("demo"));
    assert_eq!(snapshot.header().launch_id(), Some("btc-sma"));
    assert_eq!(snapshot.header().instance_id(), Some("run-001"));
    assert_eq!(snapshot.payload().quotes().unwrap().len(), 1);
    assert_eq!(
        snapshot.payload().quotes().unwrap().get(0).instrument_id(),
        "instrument:btc"
    );
}

#[test]
fn market_publishes_quote_and_trade_as_independent_views() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("market.snapshot");
    let mut actor = MarketApplication::new("market-1", 10).unwrap();
    actor
        .ingest(MarketObservation::Quote(Quote {
            market_id: "market:btc".into(),
            instrument_id: "instrument:btc".into(),
            bid_price: Some("100".into()),
            bid_quantity: Some("1".into()),
            ask_price: Some("101".into()),
            ask_quantity: Some("1".into()),
            observed_at_unix_nanos: 1,
            source_id: "binance".into(),
        }))
        .unwrap();
    actor
        .ingest(MarketObservation::Trade(Trade {
            market_id: "market:btc".into(),
            instrument_id: "instrument:btc".into(),
            trade_id: Some("trade-1".into()),
            price: "100.5".into(),
            quantity: "0.2".into(),
            observed_at_unix_nanos: 2,
            source_id: "binance".into(),
        }))
        .unwrap();
    let mut publisher =
        MmapMarketSnapshotPublisher::create(&path, 64 * 1024, "market-1", "market.events").unwrap();
    publisher.publish(&actor.snapshot()).unwrap();

    for (kind, expected_count) in [("quote", 1), ("trade", 1)] {
        let view_path = directory
            .path()
            .join("views/binance/market:btc")
            .join(kind)
            .join("current.snapshot");
        let payload = SharedSnapshotReader::open(view_path)
            .unwrap()
            .read_payload()
            .unwrap();
        let snapshot = root_as_market_data_snapshot(&payload.payload).unwrap();
        assert_eq!(
            snapshot.header().view_key(),
            format!("market.view.binance.market:btc.{kind}")
        );
        let data = snapshot.payload();
        assert_eq!(
            data.quotes().map(|values| values.len()).unwrap_or(0),
            if kind == "quote" { expected_count } else { 0 }
        );
        assert_eq!(
            data.trades().map(|values| values.len()).unwrap_or(0),
            if kind == "trade" { expected_count } else { 0 }
        );
    }
}

#[test]
fn market_publishes_bar_and_greeks_with_qualified_views() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("market.snapshot");
    let mut actor = MarketApplication::new("market-1", 10).unwrap();
    actor
        .ingest(MarketObservation::Bar(Bar {
            market_id: "market:btc".into(),
            instrument_id: "instrument:btc".into(),
            timeframe: "1m".into(),
            open: "100".into(),
            high: "105".into(),
            low: "99".into(),
            close: "103".into(),
            volume: Some("42.5".into()),
            observed_at_unix_nanos: 1,
            source_id: "binance".into(),
            derivation: "aggregated".into(),
        }))
        .unwrap();
    actor
        .ingest(MarketObservation::OptionGreeks(OptionGreeks {
            market_id: "market:btc-option".into(),
            instrument_id: "instrument:btc-option".into(),
            expiry_unix_nanos: Some(2),
            strike: Some("100000".into()),
            delta: Some("0.5".into()),
            gamma: Some("0.01".into()),
            vega: Some("12".into()),
            theta: Some("-3".into()),
            implied_volatility: Some("0.8".into()),
            observed_at_unix_nanos: 2,
            source_id: "deribit".into(),
            derivation: "direct".into(),
        }))
        .unwrap();
    let mut publisher =
        MmapMarketSnapshotPublisher::create(&path, 64 * 1024, "market-1", "market.events").unwrap();
    publisher.publish(&actor.snapshot()).unwrap();

    let bar_path = directory
        .path()
        .join("views/binance/market:btc/bar/1m/current.snapshot");
    let bar_payload = SharedSnapshotReader::open(bar_path)
        .unwrap()
        .read_payload()
        .unwrap();
    let bar_snapshot = root_as_market_data_snapshot(&bar_payload.payload).unwrap();
    assert_eq!(
        bar_snapshot.header().view_key(),
        "market.view.binance.market:btc.bar.1m"
    );
    assert_eq!(bar_snapshot.payload().bars().unwrap().len(), 1);
    assert_eq!(bar_snapshot.payload().bar_count(), 1);

    let greeks_path = directory
        .path()
        .join("views/deribit/market:btc-option/greek/current.snapshot");
    let greeks_payload = SharedSnapshotReader::open(greeks_path)
        .unwrap()
        .read_payload()
        .unwrap();
    let greeks_snapshot = root_as_market_data_snapshot(&greeks_payload.payload).unwrap();
    assert_eq!(
        greeks_snapshot.header().view_key(),
        "market.view.deribit.market:btc-option.greek"
    );
    assert_eq!(greeks_snapshot.payload().greeks().unwrap().len(), 1);
    assert_eq!(greeks_snapshot.payload().greeks_count(), 1);
}

#[test]
fn market_publishes_each_orderbook_as_a_separate_view() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("market.snapshot");
    let mut actor = MarketApplication::new("market-1", 10).unwrap();
    actor
        .subscribe_static(
            SubscriptionId::new("sub-1").unwrap(),
            "strategy-1",
            MarketDescriptor::new_with_asset_type(
                "market:btc",
                "instrument:btc",
                "binance",
                "spot",
                "crypto",
                "BTCUSDT",
            )
            .unwrap(),
        )
        .unwrap();
    actor
        .ingest_orderbook_snapshot(
            OrderBook::snapshot(
                "market:btc",
                "instrument:btc",
                7,
                1,
                vec![PriceLevel {
                    price: "100".into(),
                    quantity: "2".into(),
                }],
                vec![PriceLevel {
                    price: "101".into(),
                    quantity: "3".into(),
                }],
            )
            .unwrap(),
        )
        .unwrap();
    let mut publisher =
        MmapMarketSnapshotPublisher::create(&path, 64 * 1024, "market-1", "market.events").unwrap();
    publisher.publish(&actor.snapshot()).unwrap();

    let view_path = directory
        .path()
        .join("views/binance/market:btc/orderbook/current.snapshot");
    let payload = SharedSnapshotReader::open(view_path)
        .unwrap()
        .read_payload()
        .unwrap();
    let snapshot = kairos_protocol::generated::kairos::market::v_1::root_as_order_book_snapshot(
        &payload.payload,
    )
    .unwrap();
    assert_eq!(
        snapshot.header().view_key(),
        "market.view.binance.market:btc.orderbook"
    );
    assert_eq!(snapshot.payload().books().unwrap().len(), 1);
}
