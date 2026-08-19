use flatbuffers::FlatBufferBuilder;
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::generated::kairos::common::v_2::{EventMetadata, EventMetadataArgs};
use kairos_protocol::generated::kairos::reference::v_2::{
    Market as FbMarket, MarketArgs, MarketUpserted, MarketUpsertedArgs,
    finish_market_upserted_buffer,
};
use kairos_reference_contract::{
    EncodeContext, Market, ReferenceEncoder, ReferenceEvent, decode_event,
};

#[test]
fn decodes_a_typed_reference_v2_event() {
    let mut builder = FlatBufferBuilder::new();
    let event_id = builder.create_string("reference:event:1");
    let stream_id = builder.create_string("reference.events");
    let producer_id = builder.create_string("reference-actor");
    let workspace_id = builder.create_string("workspace:test");
    let metadata = EventMetadata::create(
        &mut builder,
        &EventMetadataArgs {
            event_id: Some(event_id),
            stream_id: Some(stream_id),
            sequence: 1,
            producer_id: Some(producer_id),
            workspace_id: Some(workspace_id),
            occurred_at_unix_nanos: 10,
            published_at_unix_nanos: 11,
            ..Default::default()
        },
    );
    let market_id = builder.create_string("market:binance:spot:BTCUSDT");
    let instrument_id = builder.create_string("instrument:spot:BTC");
    let listing_id = builder.create_string("listing:binance:spot:BTCUSDT");
    let exchange_id = builder.create_string("exchange:binance");
    let instrument_kind = builder.create_string("spot");
    let venue_symbol = builder.create_string("BTCUSDT");
    let market = FbMarket::create(
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
    let root = MarketUpserted::create(
        &mut builder,
        &MarketUpsertedArgs {
            metadata: Some(metadata),
            catalog_revision: 3,
            market: Some(market),
        },
    );
    finish_market_upserted_buffer(&mut builder, root);

    match decode_event(builder.finished_data()).expect("decode Reference event") {
        ReferenceEvent::MarketUpserted(event) => {
            assert_eq!(event.catalog_revision(), 3);
            assert_eq!(event.metadata().sequence(), 1);
            assert_eq!(event.market().market_id(), "market:binance:spot:BTCUSDT");
        },
        _ => panic!("unexpected Reference event variant"),
    }
}

#[test]
fn rejects_non_reference_v2_payloads() {
    assert!(decode_event(b"not-a-flatbuffer").is_err());
}

#[test]
fn encoder_emits_typed_market_upsert_without_json_adapter() {
    let market = kairos_reference_contract::Market {
        market_id: kairos_primitives::MarketId::new("market:binance:spot:BTCUSDT").unwrap(),
        instrument_id: kairos_primitives::InstrumentId::new("instrument:spot:BTC").unwrap(),
        listing_id: Some(
            kairos_primitives::ListingId::new("listing:binance:spot:BTCUSDT").unwrap(),
        ),
        exchange_id: kairos_primitives::Exchange::new("exchange:binance").unwrap(),
        instrument_kind: kairos_primitives::InstrumentKind::Spot,
        venue_symbol: Some(kairos_primitives::Symbol::new("BTCUSDT").unwrap()),
        status: kairos_primitives::ReferenceStatus::Active,
        price_tick: Some("0.01".parse().unwrap()),
        quantity_tick: Some("0.00001".parse().unwrap()),
        ..Market::default()
    };
    let context = EncodeContext::event(
        "reference-actor",
        InstanceIdentity::default(),
        7,
        "reference:event:7",
        3,
    )
    .expect("valid reference event encoding context");
    let payload = ReferenceEncoder::market_upserted(&market, &context, 42)
        .expect("encode typed Reference market event");
    match decode_event(&payload).expect("decode typed Reference market event") {
        ReferenceEvent::MarketUpserted(event) => {
            assert_eq!(event.catalog_revision(), 3);
            assert_eq!(event.metadata().sequence(), 7);
            assert_eq!(event.market().status().variant_name(), Some("ACTIVE"));
            assert_eq!(event.market().price_tick().unwrap().mantissa(), 1);
            assert_eq!(event.market().price_tick().unwrap().scale(), 2);
        },
        _ => panic!("unexpected Reference event variant"),
    }
}

#[test]
fn consumer_projections_are_active_bounded_and_keep_one_watermark() {
    let active_market = Market {
        market_id: kairos_primitives::MarketId::new("market:active").unwrap(),
        instrument_id: kairos_primitives::InstrumentId::new("instrument:active").unwrap(),
        status: kairos_primitives::ReferenceStatus::Active,
        ..Default::default()
    };
    let inactive_market = Market {
        market_id: kairos_primitives::MarketId::new("market:inactive").unwrap(),
        instrument_id: kairos_primitives::InstrumentId::new("instrument:inactive").unwrap(),
        status: kairos_primitives::ReferenceStatus::Inactive,
        ..Default::default()
    };
    let snapshot = kairos_reference_contract::ReferenceProjectionSnapshot {
        actor_id: "reference-actor".into(),
        generation: 9.into(),
        event_sequence: 14.into(),
        instruments: vec![
            kairos_reference_contract::Instrument {
                instrument_id: kairos_primitives::InstrumentId::new("instrument:active").unwrap(),
                status: kairos_primitives::ReferenceStatus::Active,
                ..Default::default()
            },
            kairos_reference_contract::Instrument {
                instrument_id: kairos_primitives::InstrumentId::new("instrument:inactive").unwrap(),
                status: kairos_primitives::ReferenceStatus::Inactive,
                ..Default::default()
            },
        ],
        markets: vec![active_market, inactive_market],
        provider_health: vec![kairos_reference_contract::ProviderHealthState::default()],
        option_underlyings: vec!["SPY".into()],
        lifecycle_events: vec![kairos_reference_contract::LifecycleEntry::default()],
        ..Default::default()
    };

    let market = snapshot.market_projection();
    assert_eq!(
        (market.generation, market.event_sequence),
        (9.into(), 14.into())
    );
    assert_eq!(market.markets.len(), 1);
    assert_eq!(market.instruments.len(), 1);
    assert!(market.provider_health.is_empty());
    assert!(market.option_underlyings.is_empty());
    assert!(market.lifecycle_events.is_empty());

    let execution = snapshot.execution_projection();
    assert_eq!(execution.markets.len(), 1);

    let account = snapshot.account_projection();
    assert_eq!(account.markets.len(), 1);
    assert_eq!(account.instruments.len(), 1);
}
