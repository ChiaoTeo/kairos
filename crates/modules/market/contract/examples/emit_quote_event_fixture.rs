use flatbuffers::FlatBufferBuilder;
use kairos_market_contract::{EncodeContext, MarketEvent, decode_event, event_metadata};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::generated::kairos::common::v_2::Decimal64;
use kairos_protocol::generated::kairos::market::v_2 as fb;

fn main() {
    let mut builder = FlatBufferBuilder::new();
    let context = EncodeContext::event(
        "market:fixture",
        InstanceIdentity::new("workspace", "launch", "instance").unwrap(),
        17,
        "market-event-17",
    )
    .unwrap();
    let metadata = event_metadata(&mut builder, &context, 18);
    let market_id = builder.create_string("market:fixture");
    let scope = fb::ObservationScope::create(
        &mut builder,
        &fb::ObservationScopeArgs {
            kind: fb::ObservationScopeKind::MARKET,
            market_id: Some(market_id),
            ..Default::default()
        },
    );
    let instrument_id = builder.create_string("instrument:fixture");
    let provider = builder.create_string("fixture");
    let bid_price = Decimal64::new(12345, 2);
    let ask_price = Decimal64::new(12355, 2);
    let quote = fb::Quote::create(
        &mut builder,
        &fb::QuoteArgs {
            scope: Some(scope),
            instrument_id: Some(instrument_id),
            provider: Some(provider),
            bid_price: Some(&bid_price),
            ask_price: Some(&ask_price),
            source_observed_at_unix_nanos: 18,
            ..Default::default()
        },
    );
    let root = fb::QuoteUpdated::create(
        &mut builder,
        &fb::QuoteUpdatedArgs {
            metadata: Some(metadata),
            quote: Some(quote),
        },
    );
    fb::finish_quote_updated_buffer(&mut builder, root);
    let payload = builder.finished_data();
    assert!(matches!(
        decode_event(payload).unwrap(),
        MarketEvent::QuoteUpdated(_)
    ));
    for byte in payload {
        print!("{byte:02x}");
    }
}
