use flatbuffers::FlatBufferBuilder;
use kairos_market::{
    MarketApplication, MarketDataRoute, MarketObservation, MarketSelectionQuery,
    ObservationSelector, Quote, Rate, ReconcileMarketUniverse, ResolvedMarket, SubscriptionId,
};
use kairos_protocol::generated::kairos::common::v_2::{EventMetadata, EventMetadataArgs};
use kairos_protocol::generated::kairos::reference::v_2::{
    Market as MarketMessage, MarketArgs, MarketUpserted as MarketUpsertedMessage,
    MarketUpsertedArgs, finish_market_upserted_buffer,
};

fn market(id: &str, symbol: &str) -> ResolvedMarket {
    ResolvedMarket::new(
        id,
        format!("instrument:{id}"),
        kairos_primitives::reference::InstrumentKind::Spot,
        "binance",
        MarketDataRoute::new(format!("test:{id}"), "binance", "spot", symbol).unwrap(),
    )
    .unwrap()
}

#[test]
fn rate_observation_has_a_qualified_view_and_freshness_watermark() {
    let mut actor = MarketApplication::new("market-1", 10).unwrap();
    actor
        .ingest(MarketObservation::Rate(Rate {
            rate_id: "funding:8h".into(),
            scope: kairos_market::ObservationScope::market("market:btc-perp").unwrap(),
            instrument_id: kairos_primitives::reference::InstrumentId::new("instrument:btc-perp")
                .unwrap(),
            basis: "funding".into(),
            value: "0.0001".parse().unwrap(),
            mark_price: Some("100.5".parse().unwrap()),
            observed_at_unix_nanos: kairos_primitives::time::UnixNanos::new(7),
            source_id: kairos_primitives::market::SourceId::new("binance").unwrap(),
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
        kairos_primitives::time::UnixNanos::new(7)
    );
    assert_eq!(actor.event_sequence(), 1);
}

#[test]
fn actor_owns_sequence_and_latest_observation() {
    let mut actor = MarketApplication::new("market-1", 10).unwrap();
    let value = MarketObservation::Quote(Quote {
        scope: kairos_market::ObservationScope::market("market:btc").unwrap(),
        instrument_id: kairos_primitives::reference::InstrumentId::new("instrument:btc").unwrap(),
        bid_price: Some("100".parse().unwrap()),
        bid_quantity: Some("1".parse().unwrap()),
        ask_price: None,
        ask_quantity: None,
        bid_venue_code: None,
        ask_venue_code: None,
        tape: None,
        observed_at_unix_nanos: kairos_primitives::time::UnixNanos::new(7),
        source_id: kairos_primitives::market::SourceId::new("test").unwrap(),
    });
    assert_eq!(actor.ingest(value).unwrap(), 1);
    assert_eq!(actor.event_sequence(), 1);
    assert_eq!(actor.current_view().views.len(), 1);
}

#[test]
fn selectors_filter_ingestion_and_current_queries_are_typed() {
    let mut actor = MarketApplication::new("market-1", 10).unwrap();
    actor
        .subscribe_static_with_selectors(
            SubscriptionId::new("quote-only").unwrap(),
            "strategy",
            market("market:btc", "BTCUSDT"),
            vec![ObservationSelector::parse("quote").unwrap()],
        )
        .unwrap();
    let quote = MarketObservation::Quote(Quote {
        scope: kairos_market::ObservationScope::market("market:btc").unwrap(),
        instrument_id: kairos_primitives::reference::InstrumentId::new("instrument:market:btc")
            .unwrap(),
        bid_price: Some("100".parse().unwrap()),
        bid_quantity: None,
        ask_price: None,
        ask_quantity: None,
        bid_venue_code: None,
        ask_venue_code: None,
        tape: None,
        observed_at_unix_nanos: kairos_primitives::time::UnixNanos::new(2),
        source_id: kairos_primitives::market::SourceId::new("binance").unwrap(),
    });
    assert_eq!(actor.ingest(quote).unwrap(), 1);
    assert!(actor.query().latest_quote("market:btc").is_some());
    let bar = MarketObservation::Bar(kairos_market::Bar {
        scope: kairos_market::ObservationScope::market("market:btc").unwrap(),
        instrument_id: kairos_primitives::reference::InstrumentId::new("instrument:market:btc")
            .unwrap(),
        timeframe: "1m".into(),
        open: "1".parse().unwrap(),
        high: "2".parse().unwrap(),
        low: "1".parse().unwrap(),
        close: "1".parse().unwrap(),
        volume: None,
        observed_at_unix_nanos: kairos_primitives::time::UnixNanos::new(3),
        source_id: kairos_primitives::market::SourceId::new("binance").unwrap(),
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
            scope: kairos_market::ObservationScope::market("market:btc").unwrap(),
            instrument_id: kairos_primitives::reference::InstrumentId::new("instrument:btc")
                .unwrap(),
            bid_price: Some(price.parse().unwrap()),
            bid_quantity: None,
            ask_price: None,
            ask_quantity: None,
            bid_venue_code: None,
            ask_venue_code: None,
            tape: None,
            observed_at_unix_nanos: kairos_primitives::time::UnixNanos::new(time),
            source_id: kairos_primitives::market::SourceId::new("test").unwrap(),
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
fn source_agnostic_typed_query_rejects_ambiguous_views() {
    let mut actor = MarketApplication::new("market-1", 10).unwrap();
    for source_id in ["source-a", "source-b"] {
        actor
            .ingest(MarketObservation::Quote(Quote {
                scope: kairos_market::ObservationScope::market("market:btc").unwrap(),
                instrument_id: kairos_primitives::reference::InstrumentId::new("instrument:btc")
                    .unwrap(),
                bid_price: Some("100".parse().unwrap()),
                bid_quantity: None,
                ask_price: None,
                ask_quantity: None,
                bid_venue_code: None,
                ask_venue_code: None,
                tape: None,
                observed_at_unix_nanos: kairos_primitives::time::UnixNanos::new(10),
                source_id: kairos_primitives::market::SourceId::new(source_id).unwrap(),
            }))
            .unwrap();
    }
    assert!(actor.query().latest_quote("market:btc").is_none());
    assert_eq!(actor.current_view().views.len(), 2);
}

#[test]
fn dynamic_subscription_reconciles_reference_changes_idempotently() {
    let first = market("market:one", "ONE");
    let second = market("market:two", "TWO");
    let third = market("market:three", "THREE");
    let mut actor = MarketApplication::new("market-1", 10).unwrap();
    let id = SubscriptionId::new("dynamic-1").unwrap();
    let query = MarketSelectionQuery {
        exchange_id: Some(kairos_primitives::reference::Exchange::new("binance").unwrap()),
        provider_product: Some(
            kairos_primitives::integration::ProviderProductCode::new("spot").unwrap(),
        ),
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
        .reconcile_market_universe(ReconcileMarketUniverse {
            generation: 1.into(),
            event_sequence: 1.into(),
            markets: vec![second.clone(), third.clone()],
        })
        .unwrap();
    let result = changes.get(&id).unwrap();
    assert_eq!(result.added, vec!["market:three"]);
    assert_eq!(result.removed, vec!["market:one"]);

    let repeated = actor
        .reconcile_market_universe(ReconcileMarketUniverse {
            generation: 2.into(),
            event_sequence: 2.into(),
            markets: vec![second, third],
        })
        .unwrap();
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
    actor
        .reconcile_market_universe(ReconcileMarketUniverse {
            generation: 1.into(),
            event_sequence: 1.into(),
            markets: vec![second],
        })
        .unwrap();
    let snapshot = actor.current_view();
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
        .reconcile_market_universe(ReconcileMarketUniverse {
            generation: 1.into(),
            event_sequence: 1.into(),
            markets: vec![first, second, third],
        })
        .unwrap();
    assert!(
        result
            .get(&id)
            .unwrap()
            .rejected
            .as_deref()
            .is_some_and(|value| value.contains("member limit"))
    );
    let state = actor.current_view().subscriptions.remove(0);
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
        .reconcile_market_universe(ReconcileMarketUniverse {
            generation: 2.into(),
            event_sequence: 3.into(),
            markets: vec![second.clone()],
        })
        .unwrap();
    let ignored = actor
        .reconcile_market_universe(ReconcileMarketUniverse {
            generation: 1.into(),
            event_sequence: 99.into(),
            markets: vec![first],
        })
        .unwrap();
    assert!(ignored.is_empty());
    let state = actor.current_view().subscriptions.remove(0);
    assert!(
        state
            .members
            .contains_key(second.market_id().unwrap().as_str())
    );
}

#[test]
fn reference_v2_wire_event_decodes_with_watermarks() {
    let mut builder = FlatBufferBuilder::new();
    let stream_id = builder.create_string("reference.lifecycle");
    let producer_id = builder.create_string("reference-1");
    let workspace_id = builder.create_string("workspace:test");
    let event_id = builder.create_string("lifecycle-1");
    let metadata = EventMetadata::create(
        &mut builder,
        &EventMetadataArgs {
            event_id: Some(event_id),
            stream_id: Some(stream_id),
            producer_id: Some(producer_id),
            workspace_id: Some(workspace_id),
            sequence: 9,
            occurred_at_unix_nanos: 9,
            ..Default::default()
        },
    );
    let market_id = builder.create_string("market:two");
    let instrument_id = builder.create_string("instrument:two");
    let listing_id = builder.create_string("listing:two");
    let exchange_id = builder.create_string("exchange:two");
    let instrument_kind = builder.create_string("spot");
    let venue_symbol = builder.create_string("TWO");
    let market = MarketMessage::create(
        &mut builder,
        &MarketArgs {
            market_id: Some(market_id),
            instrument_id: Some(instrument_id),
            listing_id: Some(listing_id),
            exchange_id: Some(exchange_id),
            instrument_kind: Some(instrument_kind),
            venue_symbol: Some(venue_symbol),
            ..Default::default()
        },
    );
    let root = MarketUpsertedMessage::create(
        &mut builder,
        &MarketUpsertedArgs {
            metadata: Some(metadata),
            catalog_revision: 2,
            market: Some(market),
        },
    );
    finish_market_upserted_buffer(&mut builder, root);
    let event = kairos_reference_contract::decode_event(builder.finished_data()).unwrap();
    match event {
        kairos_reference_contract::ReferenceEvent::MarketUpserted(value) => {
            assert_eq!(value.catalog_revision(), 2);
            assert_eq!(value.metadata().sequence(), 9);
            assert_eq!(value.market().market_id(), "market:two");
        },
        _ => panic!("unexpected Reference event"),
    }
}
