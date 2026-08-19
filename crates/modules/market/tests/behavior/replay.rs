use kairos_market::composition::{attach_replay_source, attach_replay_source_with_checkpoint};
use kairos_market::{
    MarketApplication, MarketDataRoute, MarketObservation, Quote, ResolvedMarket, SubscriptionId,
};

fn fixture() -> (ResolvedMarket, Vec<MarketObservation>) {
    let descriptor = ResolvedMarket::new(
        "market:btc",
        "instrument:btc",
        kairos_primitives::InstrumentKind::Spot,
        "binance",
        MarketDataRoute::new("test:btc", "binance", "spot", "BTCUSDT").unwrap(),
    )
    .unwrap();
    let events = (1..=2)
        .map(|time| {
            MarketObservation::Quote(Quote {
                scope: kairos_market::ObservationScope::from(
                    descriptor.market_id().unwrap().clone(),
                ),
                instrument_id: descriptor.instrument_id.clone(),
                bid_price: Some(kairos_primitives::Price::new(time as i64, 0).unwrap()),
                bid_quantity: None,
                ask_price: None,
                ask_quantity: None,
                bid_venue_code: None,
                ask_venue_code: None,
                tape: None,
                observed_at_unix_nanos: kairos_primitives::UnixNanos::new(time),
                source_id: kairos_primitives::SourceId::new("recorded").unwrap(),
            })
        })
        .collect();
    (descriptor, events)
}

#[tokio::test]
async fn replay_source_wakes_actor_and_completes_without_polling() {
    let (descriptor, events) = fixture();
    let mut runtime = MarketApplication::new("market", 10).unwrap();
    attach_replay_source(&mut runtime, events).unwrap();
    runtime
        .subscribe_static(SubscriptionId::new("replay").unwrap(), "test", descriptor)
        .unwrap();
    runtime.sync_source_subscriptions().await.unwrap();
    let mut applied = 0;
    while !runtime.sources_complete() {
        applied += runtime.drive_next_source_input().await.unwrap();
    }
    assert_eq!(applied, 2);
    assert_eq!(runtime.event_sequence(), 2);
}

#[tokio::test]
async fn replay_checkpoint_resumes_completed_cursor() {
    let (descriptor, events) = fixture();
    let directory = tempfile::tempdir().unwrap();
    let checkpoint = directory.path().join("instance/market/cursor.json");
    let mut first = MarketApplication::new("first", 10).unwrap();
    attach_replay_source_with_checkpoint(&mut first, events.clone(), None, None, &checkpoint)
        .unwrap();
    first
        .subscribe_static(
            SubscriptionId::new("replay").unwrap(),
            "test",
            descriptor.clone(),
        )
        .unwrap();
    first.sync_source_subscriptions().await.unwrap();
    while !first.sources_complete() {
        first.drive_next_source_input().await.unwrap();
    }
    assert!(checkpoint.is_file());

    let mut resumed = MarketApplication::new("resumed", 10).unwrap();
    attach_replay_source_with_checkpoint(&mut resumed, events, None, None, checkpoint).unwrap();
    resumed
        .subscribe_static(SubscriptionId::new("replay").unwrap(), "test", descriptor)
        .unwrap();
    resumed.sync_source_subscriptions().await.unwrap();
    while !resumed.sources_complete() {
        resumed.drive_next_source_input().await.unwrap();
    }
    assert_eq!(resumed.event_sequence(), 0);
}
