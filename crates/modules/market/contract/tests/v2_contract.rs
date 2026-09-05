use kairos_market_contract::{
    MARKET_BARS_DATABASE, MARKET_MAP_SIZE, MarketIndexedValue, MarketIndexedView, MarketViewKey,
    MarketViewKind, market_database, market_indexed_environment_path, market_indexed_identity,
    market_indexed_key,
};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::generated::kairos::common::v_2::Decimal64;
use kairos_protocol::generated::kairos::market::v_2 as fb;

#[test]
fn quote_reader_accepts_pre_venue_identity_wire_layout() {
    let mut builder = flatbuffers::FlatBufferBuilder::new();
    let instrument = builder.create_string("instrument:btc");
    let provider = builder.create_string("fixture");
    let bid_code = builder.create_string("19");
    let ask_code = builder.create_string("11");
    let scope = fb::ObservationScope::create(
        &mut builder,
        &fb::ObservationScopeArgs {
            kind: fb::ObservationScopeKind::CONSOLIDATED,
            instrument_id: Some(instrument),
            ..Default::default()
        },
    );
    // Pin the original schema's slot numbers. Do not use the current QuoteArgs:
    // this fixture must remain an old writer even if the generated schema changes.
    let start = builder.start_table();
    builder.push_slot_always(6, scope);
    builder.push_slot_always(8, instrument);
    builder.push_slot_always(10, provider);
    builder.push_slot_always(20, bid_code);
    builder.push_slot_always(22, ask_code);
    builder.push_slot::<u64>(26, 7, 0);
    builder.push_slot::<u64>(28, 8, 0);
    let table = builder.end_table(start);
    builder.finish(table, None);
    let quote = flatbuffers::root::<fb::Quote<'_>>(builder.finished_data()).unwrap();
    assert_eq!(quote.instrument_id(), "instrument:btc");
    assert_eq!(quote.provider(), "fixture");
    assert_eq!(quote.bid_venue_code(), Some("19"));
    assert_eq!(quote.ask_venue_code(), Some("11"));
    assert_eq!(quote.source_observed_at_unix_nanos(), 7);
    assert_eq!(quote.received_at_unix_nanos(), 8);
    assert_eq!(quote.bid_venue_id(), None);
    assert_eq!(quote.ask_venue_id(), None);
}

#[test]
fn typed_quote_query_validates_each_optional_venue_identity() {
    for (bid, ask, valid) in [
        (None, None, true),
        (Some("venue:bid"), Some("venue:ask"), true),
        (Some(""), Some("venue:ask"), false),
        (Some("venue:bid"), Some(""), false),
    ] {
        let root = tempfile::tempdir().unwrap();
        let identity = InstanceIdentity::new("workspace", "launch", "instance").unwrap();
        let key = MarketViewKey::new(
            "market:fixture",
            "fixture",
            MarketViewKind::Quote,
            None::<String>,
        )
        .unwrap();
        let options = kairos_indexed_view::EnvironmentOptions::new(
            market_indexed_environment_path(root.path(), &identity).unwrap(),
            MARKET_MAP_SIZE,
        )
        .unwrap();
        let mut writer = kairos_indexed_view::IndexedViewWriter::create(
            &options,
            market_indexed_identity(&identity, 1),
        )
        .unwrap();
        let mut builder = flatbuffers::FlatBufferBuilder::new();
        let market_id = builder.create_string(&key.scope_key);
        let provider = builder.create_string("fixture");
        let instrument_id = builder.create_string("instrument:fixture");
        let bid_venue_id = bid.map(|value| builder.create_string(value));
        let ask_venue_id = ask.map(|value| builder.create_string(value));
        let scope = fb::ObservationScope::create(
            &mut builder,
            &fb::ObservationScopeArgs {
                kind: fb::ObservationScopeKind::MARKET,
                market_id: Some(market_id),
                ..Default::default()
            },
        );
        let quote = fb::Quote::create(
            &mut builder,
            &fb::QuoteArgs {
                scope: Some(scope),
                provider: Some(provider),
                instrument_id: Some(instrument_id),
                bid_venue_id,
                ask_venue_id,
                ..Default::default()
            },
        );
        let current_identity = fb::MarketCurrentIdentity::create(
            &mut builder,
            &fb::MarketCurrentIdentityArgs {
                scope_key: Some(market_id),
                provider: Some(provider),
                ..Default::default()
            },
        );
        let current = fb::MarketQuoteCurrent::create(
            &mut builder,
            &fb::MarketQuoteCurrentArgs {
                identity: Some(current_identity),
                value: Some(quote),
            },
        );
        fb::finish_market_quote_current_buffer(&mut builder, current);
        writer
            .apply(
                &[kairos_indexed_view::Mutation::Put {
                    database: kairos_market_contract::MARKET_QUOTES_DATABASE.into(),
                    key: market_indexed_key(&key).unwrap(),
                    value: builder.finished_data().to_vec(),
                }],
                7,
                99,
            )
            .unwrap();
        drop(writer);
        let view = MarketIndexedView::open(root.path(), &identity).unwrap();
        let result = view.quote(&key);
        drop(view);
        if valid {
            let quote = result.unwrap().unwrap();
            assert_eq!(quote.bid_venue_id.as_ref().map(|value| value.as_str()), bid);
            assert_eq!(quote.ask_venue_id.as_ref().map(|value| value.as_str()), ask);
            assert_eq!(quote.evidence.applied_event_sequence.get(), 7);
        } else {
            assert!(
                result.is_err(),
                "invalid side identity must fail at the typed boundary"
            );
        }
    }
}

#[test]
fn indexed_environment_is_owner_scoped_and_keys_are_semantic() {
    let identity = InstanceIdentity::new("workspace", "launch", "instance").unwrap();
    let quote = MarketViewKey::new(
        "market:binance:spot:BTCUSDT",
        "binance.spot",
        MarketViewKind::Quote,
        None::<String>,
    )
    .unwrap();
    let bar = MarketViewKey::new(
        "market:binance:spot:BTCUSDT",
        "binance.spot",
        MarketViewKind::Bar,
        Some("1m"),
    )
    .unwrap();
    let path = market_indexed_environment_path("/runtime", &identity).unwrap();
    assert!(path.ends_with("views/v3/Market/market-main/epoch-1/current.lmdb"));
    assert_ne!(market_database(&quote.kind), market_database(&bar.kind));
    assert_ne!(
        market_indexed_key(&quote).unwrap(),
        market_indexed_key(&bar).unwrap()
    );
}

#[test]
fn canonical_view_key_contains_all_partition_identity() {
    let key = MarketViewKey::new(
        "market:fixture",
        "fixture",
        MarketViewKind::OrderBook,
        None::<String>,
    )
    .unwrap();
    assert_eq!(
        key.canonical_key(),
        "scope=market:fixture;provider=fixture;view=order-book;qualifier="
    );
}

#[test]
fn latest_bar_is_read_from_its_dedicated_database_and_root() {
    let root = tempfile::tempdir().unwrap();
    let identity = InstanceIdentity::new("workspace", "launch", "instance").unwrap();
    let key =
        MarketViewKey::new("market:fixture", "fixture", MarketViewKind::Bar, Some("1m")).unwrap();
    let sequence = 7;
    let options = kairos_indexed_view::EnvironmentOptions::new(
        market_indexed_environment_path(root.path(), &identity).unwrap(),
        MARKET_MAP_SIZE,
    )
    .unwrap();
    let mut writer = kairos_indexed_view::IndexedViewWriter::create(
        &options,
        market_indexed_identity(&identity, 1),
    )
    .unwrap();
    writer
        .apply(
            &[kairos_indexed_view::Mutation::Put {
                database: MARKET_BARS_DATABASE.into(),
                key: market_indexed_key(&key).unwrap(),
                value: bar_value(&key),
            }],
            sequence,
            99,
        )
        .unwrap();
    drop(writer);

    let snapshot = MarketIndexedView::open(root.path(), &identity)
        .unwrap()
        .get(&key)
        .unwrap()
        .unwrap();
    assert_eq!(snapshot.metadata().applied_event_sequence, sequence);
    let MarketIndexedValue::Bar(bar) = snapshot.value().unwrap() else {
        panic!("bars database must return MarketBarCurrent");
    };
    assert_eq!(bar.bar_spec_id(), "1m");
}

#[test]
fn quote_database_rejects_a_valid_bar_root() {
    let root = tempfile::tempdir().unwrap();
    let identity = InstanceIdentity::new("workspace", "launch", "instance").unwrap();
    let key = MarketViewKey::new(
        "market:fixture",
        "fixture",
        MarketViewKind::Quote,
        None::<String>,
    )
    .unwrap();
    let options = kairos_indexed_view::EnvironmentOptions::new(
        market_indexed_environment_path(root.path(), &identity).unwrap(),
        MARKET_MAP_SIZE,
    )
    .unwrap();
    let mut writer = kairos_indexed_view::IndexedViewWriter::create(
        &options,
        market_indexed_identity(&identity, 1),
    )
    .unwrap();
    writer
        .apply(
            &[kairos_indexed_view::Mutation::Put {
                database: kairos_market_contract::MARKET_QUOTES_DATABASE.into(),
                key: market_indexed_key(&key).unwrap(),
                value: bar_value(&key),
            }],
            8,
            100,
        )
        .unwrap();
    drop(writer);

    let error = MarketIndexedView::open(root.path(), &identity)
        .unwrap()
        .get(&key)
        .err()
        .expect("quotes must reject a bar root");
    assert!(error.to_string().contains("MQC3 MarketQuoteCurrent"));
}

fn bar_value(key: &MarketViewKey) -> Vec<u8> {
    let mut builder = flatbuffers::FlatBufferBuilder::new();
    let market_id = builder.create_string(&key.scope_key);
    let scope = fb::ObservationScope::create(
        &mut builder,
        &fb::ObservationScopeArgs {
            kind: fb::ObservationScopeKind::MARKET,
            market_id: Some(market_id),
            ..Default::default()
        },
    );
    let instrument = builder.create_string("instrument:fixture");
    let provider = builder.create_string(key.provider.as_str());
    let bar_spec = builder.create_string("1m");
    let decimal = Decimal64::new(1, 0);
    let bar = fb::Bar::create(
        &mut builder,
        &fb::BarArgs {
            scope: Some(scope),
            instrument_id: Some(instrument),
            provider: Some(provider),
            bar_spec_id: Some(bar_spec),
            open: Some(&decimal),
            high: Some(&decimal),
            low: Some(&decimal),
            close: Some(&decimal),
            ..Default::default()
        },
    );
    let scope_key = builder.create_string(&key.scope_key);
    let provider = builder.create_string(key.provider.as_str());
    let qualifier = builder.create_string("1m");
    let identity = fb::MarketCurrentIdentity::create(
        &mut builder,
        &fb::MarketCurrentIdentityArgs {
            scope_key: Some(scope_key),
            provider: Some(provider),
            qualifier: Some(qualifier),
            ..Default::default()
        },
    );
    let root = fb::MarketBarCurrent::create(
        &mut builder,
        &fb::MarketBarCurrentArgs {
            identity: Some(identity),
            value: Some(bar),
        },
    );
    fb::finish_market_bar_current_buffer(&mut builder, root);
    builder.finished_data().to_vec()
}
