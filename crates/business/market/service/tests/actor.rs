use flatbuffers::FlatBufferBuilder;
use kairos_market::application::wire::decode_reference_changed;
use kairos_market::composition::MarketFeed;
use kairos_market::{
    MarketApplication, MarketDescriptor, MarketObservation, MarketRuntime, MarketSelectionQuery,
    Quote, Rate, ReferenceChanged, SubscriptionId,
};
use kairos_protocol::generated::kairos::common::v_1::{MessageHeader, MessageHeaderArgs};
use kairos_protocol::generated::kairos::reference::v_1::{
    finish_reference_changed_buffer, LifecycleEvent, LifecycleEventArgs,
    ReferenceChanged as ReferenceChangedMessage, ReferenceChangedArgs,
};
use std::collections::VecDeque;

fn market(id: &str, symbol: &str) -> MarketDescriptor {
    MarketDescriptor::new(id, format!("instrument:{id}"), "binance", "spot", symbol).unwrap()
}

#[test]
fn rate_observation_has_a_qualified_view_and_freshness_watermark() {
    let mut actor = MarketApplication::new("market-1", 10).unwrap();
    actor
        .ingest(MarketObservation::Rate(Rate {
            rate_id: "funding:8h".into(),
            market_id: kairos_domain_types::MarketId::new("market:btc-perp").unwrap(),
            instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc-perp").unwrap(),
            basis: "funding".into(),
            value: "0.0001".parse().unwrap(),
            mark_price: Some("100.5".parse().unwrap()),
            observed_at_unix_nanos: kairos_domain_types::UnixNanos::new(7),
            source_id: "binance".into(),
        }))
        .unwrap();

    let query = actor.query();
    assert_eq!(
        query
            .latest_rate("market:btc-perp", "funding:8h")
            .unwrap()
            .value,
        "0.0001".parse().unwrap()
    );
    let freshness = query
        .freshness()
        .get("market.view.binance.market:btc-perp.rate.funding:8h")
        .unwrap();
    assert_eq!(
        freshness.last_event_time_unix_nanos,
        kairos_domain_types::UnixNanos::new(7)
    );
    assert_eq!(
        freshness.event_sequence,
        kairos_domain_types::Sequence::new(1)
    );
}

#[test]
fn actor_owns_sequence_and_latest_observation() {
    let mut actor = MarketApplication::new("market-1", 10).unwrap();
    let value = MarketObservation::Quote(Quote {
        market_id: kairos_domain_types::MarketId::new("market:btc").unwrap(),
        instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc").unwrap(),
        bid_price: Some("100".parse().unwrap()),
        bid_quantity: Some("1".parse().unwrap()),
        ask_price: None,
        ask_quantity: None,
        observed_at_unix_nanos: kairos_domain_types::UnixNanos::new(7),
        source_id: "test".into(),
    });
    assert_eq!(actor.ingest(value).unwrap(), 1);
    assert_eq!(actor.snapshot().event_sequence, 1.into());
    assert!(actor.snapshot().latest.contains_key("market:btc"));
}

#[test]
fn selectors_filter_ingestion_and_current_queries_are_typed() {
    let mut actor = MarketApplication::new("market-1", 10).unwrap();
    actor
        .subscribe_static_with_selectors(
            SubscriptionId::new("quote-only").unwrap(),
            "strategy",
            market("market:btc", "BTCUSDT"),
            vec!["quote".into()],
        )
        .unwrap();
    let quote = MarketObservation::Quote(Quote {
        market_id: kairos_domain_types::MarketId::new("market:btc").unwrap(),
        instrument_id: kairos_domain_types::InstrumentId::new("instrument:market:btc").unwrap(),
        bid_price: Some("100".parse().unwrap()),
        bid_quantity: None,
        ask_price: None,
        ask_quantity: None,
        observed_at_unix_nanos: kairos_domain_types::UnixNanos::new(2),
        source_id: "binance".into(),
    });
    assert_eq!(actor.ingest(quote).unwrap(), 1);
    assert!(actor.query().latest_quote("market:btc").is_some());
    let bar = MarketObservation::Bar(kairos_market::Bar {
        market_id: kairos_domain_types::MarketId::new("market:btc").unwrap(),
        instrument_id: kairos_domain_types::InstrumentId::new("instrument:market:btc").unwrap(),
        timeframe: "1m".into(),
        open: "1".parse().unwrap(),
        high: "2".parse().unwrap(),
        low: "1".parse().unwrap(),
        close: "1".parse().unwrap(),
        volume: None,
        observed_at_unix_nanos: kairos_domain_types::UnixNanos::new(3),
        source_id: "binance".into(),
        derivation: "direct".into(),
    });
    assert_eq!(actor.ingest(bar).unwrap(), 1);
    assert!(actor.query().latest_bar("market:btc", "1m").is_none());
}

#[test]
fn out_of_order_observation_does_not_regress_current_projection() {
    let mut actor = MarketApplication::new("market-1", 10).unwrap();
    let quote = |time: u64, price: &str| {
        MarketObservation::Quote(Quote {
            market_id: kairos_domain_types::MarketId::new("market:btc").unwrap(),
            instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc").unwrap(),
            bid_price: Some(price.parse().unwrap()),
            bid_quantity: None,
            ask_price: None,
            ask_quantity: None,
            observed_at_unix_nanos: kairos_domain_types::UnixNanos::new(time),
            source_id: "test".into(),
        })
    };
    actor.ingest(quote(10, "100")).unwrap();
    actor.ingest(quote(9, "99")).unwrap();
    assert_eq!(
        actor
            .query()
            .latest_quote("market:btc")
            .unwrap()
            .bid_price
            .map(|price| (price.mantissa(), price.scale())),
        Some((100, 0))
    );
    assert_eq!(actor.drain_events().len(), 2);
}

#[test]
fn dynamic_subscription_reconciles_reference_changes_idempotently() {
    let first = market("market:one", "ONE");
    let second = market("market:two", "TWO");
    let third = market("market:three", "THREE");
    let mut actor = MarketApplication::new("market-1", 10).unwrap();
    let id = SubscriptionId::new("dynamic-1").unwrap();
    let query = MarketSelectionQuery {
        exchange_id: Some(kairos_domain_types::Exchange::new("binance").unwrap()),
        market_type: Some("spot".into()),
        active_only: true,
        ..Default::default()
    };
    let initial = actor
        .subscribe_dynamic(
            id.clone(),
            "strategy",
            query,
            vec![first.clone(), second.clone()],
        )
        .unwrap();
    assert_eq!(initial.added, vec!["market:one", "market:two"]);

    let changes = actor
        .reconcile_reference(vec![second.clone(), third.clone()])
        .unwrap();
    let result = changes.get(&id).unwrap();
    assert_eq!(result.added, vec!["market:three"]);
    assert_eq!(result.removed, vec!["market:one"]);

    let repeated = actor.reconcile_reference(vec![second, third]).unwrap();
    assert!(repeated.get(&id).unwrap().added.is_empty());
    assert!(repeated.get(&id).unwrap().removed.is_empty());
}

#[test]
fn static_subscription_is_not_changed_by_reference_reconcile() {
    let first = market("market:one", "ONE");
    let second = market("market:two", "TWO");
    let mut actor = MarketApplication::new("market-1", 10).unwrap();
    let static_id = SubscriptionId::new("static-1").unwrap();
    actor
        .subscribe_static(static_id.clone(), "strategy", first.clone())
        .unwrap();
    let dynamic_id = SubscriptionId::new("dynamic-1").unwrap();
    actor
        .subscribe_dynamic(
            dynamic_id,
            "strategy",
            MarketSelectionQuery::default(),
            vec![first],
        )
        .unwrap();
    actor.reconcile_reference(vec![second]).unwrap();
    let snapshot = actor.snapshot();
    let static_state = snapshot
        .subscriptions
        .iter()
        .find(|value| value.id == static_id)
        .unwrap();
    assert!(static_state.members.contains_key("market:one"));
}

#[test]
fn dynamic_budget_rejection_keeps_previous_members() {
    let first = market("market:one", "ONE");
    let second = market("market:two", "TWO");
    let third = market("market:three", "THREE");
    let mut actor = MarketApplication::new("market-1", 2).unwrap();
    let id = SubscriptionId::new("dynamic-1").unwrap();
    actor
        .subscribe_dynamic(
            id.clone(),
            "strategy",
            MarketSelectionQuery::default(),
            vec![first.clone(), second.clone()],
        )
        .unwrap();
    let result = actor
        .reconcile_reference(vec![first, second, third])
        .unwrap();
    assert!(result
        .get(&id)
        .unwrap()
        .rejected
        .as_deref()
        .is_some_and(|value| value.contains("member limit")));
    let state = actor.snapshot().subscriptions.remove(0);
    assert_eq!(state.members.len(), 2);
}

#[test]
fn stale_reference_changes_are_ignored_by_watermark() {
    let first = market("market:one", "ONE");
    let second = market("market:two", "TWO");
    let mut actor = MarketApplication::new("market-1", 10).unwrap();
    let id = SubscriptionId::new("dynamic-1").unwrap();
    actor
        .subscribe_dynamic(
            id.clone(),
            "strategy",
            MarketSelectionQuery::default(),
            vec![first.clone()],
        )
        .unwrap();
    actor
        .apply_reference_change(ReferenceChanged {
            generation: 2.into(),
            event_sequence: 3.into(),
            markets: vec![second.clone()],
        })
        .unwrap();
    let ignored = actor
        .apply_reference_change(ReferenceChanged {
            generation: 1.into(),
            event_sequence: 99.into(),
            markets: vec![first],
        })
        .unwrap();
    assert!(ignored.is_empty());
    let state = actor.snapshot().subscriptions.remove(0);
    assert!(state.members.contains_key(second.market_id.as_str()));
}

struct FakeFeed {
    events: VecDeque<MarketObservation>,
    subscriptions: usize,
}

impl MarketFeed for FakeFeed {
    fn subscribe(&mut self, _market: &MarketDescriptor) -> Result<SubscriptionId, String> {
        self.subscriptions += 1;
        SubscriptionId::new(format!("provider:{}", self.subscriptions))
    }

    fn unsubscribe(&mut self, _subscription: &SubscriptionId) -> Result<(), String> {
        Ok(())
    }

    fn poll(&mut self) -> Result<Vec<MarketObservation>, String> {
        Ok(self.events.drain(..).collect())
    }
}

#[test]
fn application_coordinates_provider_feed_and_actor_ingestion() {
    let descriptor = market("market:one", "ONE");
    let event = MarketObservation::Quote(Quote {
        market_id: descriptor.market_id.clone(),
        instrument_id: descriptor.instrument_id.clone(),
        bid_price: Some("100".parse().unwrap()),
        bid_quantity: None,
        ask_price: None,
        ask_quantity: None,
        observed_at_unix_nanos: kairos_domain_types::UnixNanos::new(1),
        source_id: "fake".into(),
    });
    let application = MarketApplication::new("market-1", 10).unwrap();
    let mut application = MarketRuntime::with_feed(
        application,
        Box::new(FakeFeed {
            events: VecDeque::from([event]),
            subscriptions: 0,
        }),
    );
    application
        .subscribe_static(
            SubscriptionId::new("static-1").unwrap(),
            "strategy",
            descriptor,
        )
        .unwrap();
    application.reconcile_feed().unwrap();
    assert_eq!(application.poll_feed().unwrap(), 1);
    assert_eq!(application.snapshot().event_sequence, 1.into());
}

#[test]
fn reference_changed_wire_notice_decodes_with_watermarks() {
    let mut builder = FlatBufferBuilder::new();
    let message_id = builder.create_string("change-1");
    let stream_id = builder.create_string("reference.lifecycle");
    let producer_id = builder.create_string("reference-1");
    let snapshot_id = builder.create_string("reference:2");
    let market_id = builder.create_string("market:two");
    let kind = builder.create_string("listed");
    let event_id = builder.create_string("lifecycle-1");
    let event_type = builder.create_string("listing_added");
    let lifecycle_event = LifecycleEvent::create(
        &mut builder,
        &LifecycleEventArgs {
            event_id: Some(event_id),
            event_type: Some(event_type),
            event_time_unix_nanos: 9,
            ..Default::default()
        },
    );
    let events = builder.create_vector(&[lifecycle_event]);
    let market_ids = builder.create_vector(&[market_id]);
    let change_kinds = builder.create_vector(&[kind]);
    let header = MessageHeader::create(
        &mut builder,
        &MessageHeaderArgs {
            message_id: Some(message_id),
            stream_id: Some(stream_id),
            producer_id: Some(producer_id),
            sequence: 9,
            ..Default::default()
        },
    );
    let root = ReferenceChangedMessage::create(
        &mut builder,
        &ReferenceChangedArgs {
            header: Some(header),
            generation: 2,
            event_sequence: 9,
            snapshot_id: Some(snapshot_id),
            events: Some(events),
            affected_market_ids: Some(market_ids),
            change_kinds: Some(change_kinds),
        },
    );
    finish_reference_changed_buffer(&mut builder, root);
    let notice = decode_reference_changed(builder.finished_data()).unwrap();
    assert_eq!(notice.generation, 2);
    assert_eq!(notice.event_sequence, 9);
    assert_eq!(notice.affected_market_ids, vec!["market:two"]);
}
