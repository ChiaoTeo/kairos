use std::path::PathBuf;

use flatbuffers::FlatBufferBuilder;
use kairos_indexed_view::{EnvironmentOptions, IndexedViewWriter, Mutation};
use kairos_market_contract::{
    MARKET_MAP_SIZE, MARKET_QUOTES_DATABASE, MarketViewKey, MarketViewKind,
    market_indexed_environment_path, market_indexed_identity, market_indexed_key,
};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::generated::kairos::common::v_2::Decimal64;
use kairos_protocol::generated::kairos::market::v_2 as fb;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let root = PathBuf::from(std::env::args_os().nth(1).ok_or("missing output root")?);
    let identity = InstanceIdentity::new("workspace", "launch", "instance")?;
    let key = MarketViewKey::new(
        "market:fixture",
        "fixture",
        MarketViewKind::Quote,
        None::<String>,
    )?;
    let options = EnvironmentOptions::new(
        market_indexed_environment_path(&root, &identity)?,
        MARKET_MAP_SIZE,
    )?;
    let mut writer = IndexedViewWriter::create(&options, market_indexed_identity(&identity, 7))?;
    writer.apply(
        &[Mutation::Put {
            database: MARKET_QUOTES_DATABASE.to_owned(),
            key: market_indexed_key(&key)?,
            value: quote_value(&key),
        }],
        41,
        1_780_000_000_000_000_000,
    )?;
    Ok(())
}

fn quote_value(key: &MarketViewKey) -> Vec<u8> {
    let mut builder = FlatBufferBuilder::new();
    let market_id = builder.create_string(&key.scope_key);
    let scope = fb::ObservationScope::create(
        &mut builder,
        &fb::ObservationScopeArgs {
            kind: fb::ObservationScopeKind::MARKET,
            market_id: Some(market_id),
            ..Default::default()
        },
    );
    let instrument_id = builder.create_string("instrument:fixture");
    let provider = builder.create_string(key.provider.as_str());
    let bid = Decimal64::new(12345, 2);
    let ask = Decimal64::new(12355, 2);
    let quote = fb::Quote::create(
        &mut builder,
        &fb::QuoteArgs {
            scope: Some(scope),
            instrument_id: Some(instrument_id),
            provider: Some(provider),
            bid_price: Some(&bid),
            ask_price: Some(&ask),
            source_observed_at_unix_nanos: 1_779_999_999_000_000_000,
            ..Default::default()
        },
    );
    let scope_key = builder.create_string(&key.scope_key);
    let provider = builder.create_string(key.provider.as_str());
    let source_event_id = builder.create_string("market:41");
    let current_identity = fb::MarketCurrentIdentity::create(
        &mut builder,
        &fb::MarketCurrentIdentityArgs {
            scope_key: Some(scope_key),
            provider: Some(provider),
            source_event_id: Some(source_event_id),
            ..Default::default()
        },
    );
    let root = fb::MarketQuoteCurrent::create(
        &mut builder,
        &fb::MarketQuoteCurrentArgs {
            identity: Some(current_identity),
            value: Some(quote),
        },
    );
    fb::finish_market_quote_current_buffer(&mut builder, root);
    builder.finished_data().to_vec()
}
