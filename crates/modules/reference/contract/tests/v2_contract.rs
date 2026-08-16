use flatbuffers::FlatBufferBuilder;
use kairos_protocol::generated::kairos::common::v_2::{EventMetadata, EventMetadataArgs};
use kairos_protocol::generated::kairos::reference::v_2::{
    finish_market_upserted_buffer, Market as FbMarket, MarketArgs, MarketUpserted,
    MarketUpsertedArgs,
};
use kairos_protocol::InstanceIdentity;
use kairos_reference_contract::Market;
use kairos_reference_contract::{decode_event, EncodeContext, ReferenceEncoder, ReferenceEvent};

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
    let market_key = builder.create_string("BTCUSDT");
    let instrument_id = builder.create_string("instrument:spot:BTC");
    let listing_id = builder.create_string("listing:binance:spot:BTCUSDT");
    let exchange_id = builder.create_string("exchange:binance");
    let market_type = builder.create_string("spot");
    let source_symbol = builder.create_string("BTCUSDT");
    let market = FbMarket::create(
        &mut builder,
        &MarketArgs {
            market_id: Some(market_id),
            market_key: Some(market_key),
            instrument_id: Some(instrument_id),
            listing_id: Some(listing_id),
            exchange_id: Some(exchange_id),
            market_type: Some(market_type),
            source_symbol: Some(source_symbol),
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
        }
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
        market_id: "market:binance:spot:BTCUSDT".into(),
        market_key: "BTCUSDT".into(),
        instrument_id: "instrument:spot:BTC".into(),
        listing_id: "listing:binance:spot:BTCUSDT".into(),
        exchange_id: "exchange:binance".into(),
        market_type: kairos_primitives::ProviderProductCode::new("spot").unwrap(),
        source_symbol: "BTCUSDT".into(),
        status: "active".into(),
        price_tick: Some("0.01".into()),
        quantity_tick: Some("0.00001".into()),
        ..Market::default()
    };
    let context = EncodeContext::event(
        "reference-actor",
        InstanceIdentity::default(),
        7,
        "reference:event:7",
        3,
    );
    let payload = ReferenceEncoder::market_upserted(&market, &context, 42)
        .expect("encode typed Reference market event");
    match decode_event(&payload).expect("decode typed Reference market event") {
        ReferenceEvent::MarketUpserted(event) => {
            assert_eq!(event.catalog_revision(), 3);
            assert_eq!(event.metadata().sequence(), 7);
            assert_eq!(event.market().status().variant_name(), Some("ACTIVE"));
            assert_eq!(event.market().price_tick().unwrap().mantissa(), 1);
            assert_eq!(event.market().price_tick().unwrap().scale(), 2);
        }
        _ => panic!("unexpected Reference event variant"),
    }
}
