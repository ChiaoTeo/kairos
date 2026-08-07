use flatbuffers::FlatBufferBuilder;
use kairos_market::application::wire::decode_reference_changed;
use kairos_market::application::MarketFeed;
use kairos_market::composition::MarketActor;
use kairos_market::{
    MarketDescriptor, MarketObservation, MarketSelectionQuery, Quote, ReferenceChanged,
    SubscriptionId,
};
use kairos_protocol::generated::kairos::common::v_1::{MessageHeader, MessageHeaderArgs};
use kairos_protocol::generated::kairos::reference::v_1::{
    finish_reference_changed_buffer, ReferenceChanged as ReferenceChangedMessage,
    ReferenceChangedArgs,
};
use std::collections::VecDeque;

fn market(id: &str, symbol: &str) -> MarketDescriptor {
    MarketDescriptor::new(id, format!("instrument:{id}"), "binance", "spot", symbol).unwrap()
}

#[test]
fn actor_owns_sequence_and_latest_observation() {
    let mut actor = MarketActor::new("market-1", 10).unwrap();
    let value = MarketObservation::Quote(Quote {
        market_id: "market:btc".into(),
        instrument_id: "instrument:btc".into(),
        bid_price: Some("100".into()),
        bid_quantity: Some("1".into()),
        ask_price: None,
        ask_quantity: None,
        observed_at_unix_nanos: 7,
        source_id: "test".into(),
    });
    assert_eq!(actor.apply_observation(value).unwrap(), 1);
    assert_eq!(actor.snapshot().event_sequence, 1);
    assert!(actor.snapshot().latest.contains_key("market:btc"));
}

#[test]
fn dynamic_subscription_reconciles_reference_changes_idempotently() {
    let first = market("market:one", "ONE");
    let second = market("market:two", "TWO");
    let third = market("market:three", "THREE");
    let mut actor = MarketActor::new("market-1", 10).unwrap();
    let id = SubscriptionId::new("dynamic-1").unwrap();
    let query = MarketSelectionQuery {
        venue_id: Some("binance".into()),
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
    let mut actor = MarketActor::new("market-1", 10).unwrap();
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
    let mut actor = MarketActor::new("market-1", 2).unwrap();
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
    assert_eq!(result.get(&id).unwrap(), &Default::default());
    let state = actor.snapshot().subscriptions.remove(0);
    assert_eq!(state.members.len(), 2);
}

#[test]
fn stale_reference_changes_are_ignored_by_watermark() {
    let first = market("market:one", "ONE");
    let second = market("market:two", "TWO");
    let mut actor = MarketActor::new("market-1", 10).unwrap();
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
            generation: 2,
            event_sequence: 3,
            markets: vec![second.clone()],
        })
        .unwrap();
    let ignored = actor
        .apply_reference_change(ReferenceChanged {
            generation: 1,
            event_sequence: 99,
            markets: vec![first],
        })
        .unwrap();
    assert!(ignored.is_empty());
    let state = actor.snapshot().subscriptions.remove(0);
    assert!(state.members.contains_key(&second.market_id));
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
fn actor_owns_provider_feed_loop_and_ingests_events() {
    let descriptor = market("market:one", "ONE");
    let event = MarketObservation::Quote(Quote {
        market_id: descriptor.market_id.clone(),
        instrument_id: descriptor.instrument_id.clone(),
        bid_price: Some("100".into()),
        bid_quantity: None,
        ask_price: None,
        ask_quantity: None,
        observed_at_unix_nanos: 1,
        source_id: "fake".into(),
    });
    let mut actor = MarketActor::new("market-1", 10).unwrap();
    actor.attach_feed(Box::new(FakeFeed {
        events: VecDeque::from([event]),
        subscriptions: 0,
    }));
    actor
        .subscribe_static(
            SubscriptionId::new("static-1").unwrap(),
            "strategy",
            descriptor,
        )
        .unwrap();
    assert_eq!(actor.poll_feed().unwrap(), 1);
    assert_eq!(actor.snapshot().event_sequence, 1);
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
