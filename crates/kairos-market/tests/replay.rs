use kairos_market::application::market::protocol::MarketFeed;
use kairos_market::{MarketDescriptor, MarketObservation, Quote, ReplayMarketFeed};

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
