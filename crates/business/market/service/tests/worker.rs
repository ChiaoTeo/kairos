use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use kairos_market::composition::MarketFeed;
use kairos_market::{
    MarketApplication, MarketDescriptor, MarketObservation, MarketRuntime, Quote, SubscriptionId,
};

struct FakeFeed {
    events: VecDeque<MarketObservation>,
    unsubscribed: Arc<Mutex<Vec<String>>>,
}

impl MarketFeed for FakeFeed {
    fn subscribe(&mut self, market: &MarketDescriptor) -> Result<SubscriptionId, String> {
        SubscriptionId::new(format!("provider:{}", market.market_id))
    }

    fn unsubscribe(&mut self, subscription: &SubscriptionId) -> Result<(), String> {
        self.unsubscribed
            .lock()
            .expect("unsubscription log is not poisoned")
            .push(subscription.0.clone());
        Ok(())
    }

    fn poll(&mut self) -> Result<Vec<MarketObservation>, String> {
        Ok(self.events.drain(..).collect())
    }
}

#[test]
fn feed_worker_moves_polling_out_of_market_callers() {
    let descriptor =
        MarketDescriptor::new("market:btc", "instrument:btc", "binance", "spot", "BTCUSDT")
            .unwrap();
    let event = MarketObservation::Quote(Quote {
        market_id: descriptor.market_id.clone(),
        instrument_id: descriptor.instrument_id.clone(),
        bid_price: Some("100".into()),
        bid_quantity: Some("1".into()),
        ask_price: Some("101".into()),
        ask_quantity: Some("1".into()),
        observed_at_unix_nanos: 1,
        source_id: "fake".into(),
    });
    let application = MarketApplication::new("market-1", 10).unwrap();
    let mut application = MarketRuntime::with_feed(
        application,
        Box::new(FakeFeed {
            events: VecDeque::from([event]),
            unsubscribed: Arc::new(Mutex::new(Vec::new())),
        }),
    );
    application
        .start_feed_worker(Duration::from_millis(1))
        .unwrap();
    application
        .subscribe_static(
            SubscriptionId::new("subscription-1").unwrap(),
            "strategy-1",
            descriptor,
        )
        .unwrap();
    application.reconcile_feed().unwrap();

    for _ in 0..50 {
        if application.poll_feed().unwrap() == 1 {
            return;
        }
        std::thread::sleep(Duration::from_millis(2));
    }
    panic!("worker did not deliver the feed event");
}

#[test]
fn feed_worker_returns_the_original_provider_handle_for_unsubscribe() {
    let descriptor =
        MarketDescriptor::new("market:btc", "instrument:btc", "binance", "spot", "BTCUSDT")
            .unwrap();
    let unsubscribed = Arc::new(Mutex::new(Vec::new()));
    let application = MarketApplication::new("market-1", 10).unwrap();
    let mut application = MarketRuntime::with_feed(
        application,
        Box::new(FakeFeed {
            events: VecDeque::new(),
            unsubscribed: Arc::clone(&unsubscribed),
        }),
    );
    application
        .start_feed_worker(Duration::from_millis(10))
        .unwrap();
    application
        .subscribe_static(
            SubscriptionId::new("subscription-1").unwrap(),
            "strategy-1",
            descriptor,
        )
        .unwrap();
    application.reconcile_feed().unwrap();
    assert!(application.unsubscribe(&SubscriptionId::new("subscription-1").unwrap()));
    application.reconcile_feed().unwrap();

    for _ in 0..50 {
        if unsubscribed
            .lock()
            .expect("unsubscription log is not poisoned")
            .as_slice()
            == ["provider:market:btc"]
        {
            return;
        }
        std::thread::sleep(Duration::from_millis(2));
    }
    panic!("worker did not forward the original provider subscription handle");
}
