use kairos_market::application::MarketFeed;
use kairos_market::composition::ReplayMarketFeed;
use kairos_market::{MarketDescriptor, MarketObservation, Quote};

#[test]
fn replay_feed_provides_deterministic_warmup_events() {
    let descriptor =
        MarketDescriptor::new("market:btc", "instrument:btc", "binance", "spot", "BTCUSDT")
            .unwrap();
    let event = MarketObservation::Quote(Quote {
        market_id: descriptor.market_id.clone(),
        instrument_id: descriptor.instrument_id.clone(),
        bid_price: Some("100".into()),
        bid_quantity: None,
        ask_price: None,
        ask_quantity: None,
        observed_at_unix_nanos: 1,
        source_id: "replay".into(),
    });
    let mut feed = ReplayMarketFeed::new([event]);
    feed.subscribe(&descriptor).unwrap();
    assert_eq!(feed.poll().unwrap().len(), 1);
    assert_eq!(feed.remaining(), 0);
}

#[test]
fn replay_checkpoint_is_instance_owned_and_resumes_cursor() {
    let descriptor =
        MarketDescriptor::new("market:btc", "instrument:btc", "binance", "spot", "BTCUSDT")
            .unwrap();
    let events = (1..=2)
        .map(|time| {
            MarketObservation::Quote(Quote {
                market_id: descriptor.market_id.clone(),
                instrument_id: descriptor.instrument_id.clone(),
                bid_price: Some(time.to_string()),
                bid_quantity: None,
                ask_price: None,
                ask_quantity: None,
                observed_at_unix_nanos: time,
                source_id: "replay".into(),
            })
        })
        .collect::<Vec<_>>();
    let directory = tempfile::tempdir().unwrap();
    let checkpoint = directory
        .path()
        .join("launches/backtest/run-1/market/cursor.json");
    let mut first =
        ReplayMarketFeed::with_checkpoint(events.clone(), None, None, &checkpoint).unwrap();
    first.subscribe(&descriptor).unwrap();
    assert_eq!(first.poll().unwrap().len(), 2);
    assert!(checkpoint.is_file());

    let mut resumed = ReplayMarketFeed::with_checkpoint(events, None, None, &checkpoint).unwrap();
    resumed.subscribe(&descriptor).unwrap();
    assert!(resumed.poll().unwrap().is_empty());
    assert_eq!(resumed.remaining(), 0);
}
