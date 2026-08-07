use kairos_market::composition::binance_spot_rest_feed;
use kairos_market::{MarketActor, MarketObservation, MmapMarketSnapshotPublisher, Quote};
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
    let mut actor = MarketActor::new("market-1", 10).unwrap();
    actor
        .apply_observation(MarketObservation::Quote(Quote {
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
    let mut publisher =
        MmapMarketSnapshotPublisher::create(&path, 64 * 1024, "market-1", "market.events").unwrap();
    publisher.publish(&actor.snapshot()).unwrap();
    let snapshot = SharedSnapshotReader::open(path)
        .unwrap()
        .read_market_data()
        .unwrap();
    assert_eq!(snapshot.owner_actor_id, "market-1");
    assert_eq!(snapshot.item_count, 1);
    assert_eq!(
        snapshot.first_instrument_id.as_deref(),
        Some("instrument:btc")
    );
}
