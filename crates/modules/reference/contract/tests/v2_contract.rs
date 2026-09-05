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
        market_id: kairos_primitives::reference::MarketId::new("market:binance:spot:BTCUSDT")
            .unwrap(),
        instrument_id: kairos_primitives::reference::InstrumentId::new("instrument:spot:BTC")
            .unwrap(),
        listing_id: Some(
            kairos_primitives::reference::ListingId::new("listing:binance:spot:BTCUSDT").unwrap(),
        ),
        exchange_id: kairos_primitives::reference::ExchangeId::new("exchange:binance").unwrap(),
        instrument_kind: kairos_primitives::reference::InstrumentKind::Spot,
        venue_symbol: Some(kairos_primitives::reference::Symbol::new("BTCUSDT").unwrap()),
        status: kairos_primitives::reference::ReferenceStatus::Active,
        price_tick: Some("0.01".parse().unwrap()),
        quantity_tick: Some("0.00001".parse().unwrap()),
        ..Market::default()
    };
    let context = EncodeContext::event(
        "reference-actor",
        1,
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
fn encoder_emits_typed_v3_venue_and_market_events() {
    use std::collections::BTreeSet;

    let context = EncodeContext::event(
        "reference-actor",
        1,
        InstanceIdentity::default(),
        8,
        "reference:event:8",
        4,
    )
    .unwrap();
    let venue = kairos_reference_contract::Venue {
        venue_id: kairos_primitives::reference::VenueId::new("venue:xiex").unwrap(),
        name: "IEX".into(),
        venue_kind: kairos_reference_contract::VenueKind::RegulatedExchange,
        roles: BTreeSet::from([kairos_reference_contract::VenueRole::Execution]),
        mic: Some(kairos_primitives::reference::Mic::new("IEXG").unwrap()),
        operating_mic: Some(kairos_primitives::reference::Mic::new("IEXG").unwrap()),
        parent_venue_id: None,
        jurisdiction: Some(kairos_primitives::reference::JurisdictionCode::new("US").unwrap()),
        status: kairos_primitives::reference::ReferenceStatus::Active,
    };
    let payload = ReferenceEncoder::venue_upserted(&venue, &context, 42).unwrap();
    match decode_event(&payload).unwrap() {
        ReferenceEvent::VenueUpserted(event) => {
            assert_eq!(event.catalog_revision(), 4);
            assert_eq!(event.venue().venue_id(), "venue:xiex");
            assert_eq!(event.venue().roles().len(), 1);
        },
        _ => panic!("unexpected Reference event variant"),
    }

    let market = kairos_reference_contract::VenueMarket {
        market_id: kairos_primitives::reference::MarketId::new("market:xiex:equity:AAPL").unwrap(),
        instrument_id: kairos_primitives::reference::InstrumentId::new(
            "instrument:equity:US:AAPL:common",
        )
        .unwrap(),
        execution_venue_id: venue.venue_id,
        origin_listing_id: Some(
            kairos_primitives::reference::ListingId::new("listing:xnas:equity:AAPL").unwrap(),
        ),
        market_segment_id: None,
        venue_symbol: Some(kairos_primitives::reference::Symbol::new("AAPL").unwrap()),
        trading_calendar_id: None,
        trading_session_ids: Vec::new(),
        base_asset_id: None,
        quote_asset_id: None,
        status: kairos_primitives::reference::ReferenceStatus::Active,
        trading_rules: kairos_reference_contract::TradingRules::default(),
        effective_from_unix_nanos: 0.into(),
        effective_to_unix_nanos: None,
    };
    let payload = ReferenceEncoder::venue_market_upserted(&market, &context, 43).unwrap();
    match decode_event(&payload).unwrap() {
        ReferenceEvent::VenueMarketUpserted(event) => {
            assert_eq!(event.market().execution_venue_id(), "venue:xiex");
            assert_eq!(
                event.market().origin_listing_id(),
                Some("listing:xnas:equity:AAPL")
            );
        },
        _ => panic!("unexpected Reference event variant"),
    }
}

#[test]
fn encoder_emits_coverage_conclusion_changing_event() {
    use std::collections::BTreeSet;

    let context = EncodeContext::event(
        "reference-actor",
        1,
        InstanceIdentity::default(),
        9,
        "reference:event:9",
        5,
    )
    .unwrap();
    let coverage = kairos_reference_contract::ReferenceCoverage {
        coverage_id: kairos_primitives::reference::ReferenceCoverageId::new(
            "coverage:binance-spot",
        )
        .unwrap(),
        source_id: kairos_primitives::reference::ReferenceSourceId::new("binance-spot").unwrap(),
        fact_kinds: BTreeSet::from([
            kairos_reference_contract::ReferenceFactKind::Instrument,
            kairos_reference_contract::ReferenceFactKind::Market,
        ]),
        scope: kairos_reference_contract::ReferenceCoverageScope::ProviderCatalog {
            binding: kairos_reference_contract::ReferenceSourceBinding::Binance(
                kairos_reference_contract::BinanceReferenceSource::Spot,
            ),
        },
        completeness: kairos_reference_contract::CoverageCompleteness::CompleteForDeclaredScope,
        state: kairos_reference_contract::CoverageState::Usable,
        generation: Some(5.into()),
        event_sequence: Some(9.into()),
        last_attempt_unix_nanos: Some(40.into()),
        last_success_unix_nanos: Some(41.into()),
        stale_after_unix_nanos: Some(100.into()),
        has_last_known_good: true,
    };
    let payload = ReferenceEncoder::coverage_state_changed(
        &coverage,
        kairos_reference_contract::CoverageState::Scanning,
        &context,
        42,
    )
    .unwrap();
    match decode_event(&payload).unwrap() {
        ReferenceEvent::CoverageStateChanged(event) => {
            assert_eq!(event.coverage().source_id(), "binance-spot");
            assert_eq!(event.previous_state().variant_name(), Some("SCANNING"));
            assert_eq!(event.coverage().state().variant_name(), Some("USABLE"));
        },
        _ => panic!("unexpected Reference event variant"),
    }
}
