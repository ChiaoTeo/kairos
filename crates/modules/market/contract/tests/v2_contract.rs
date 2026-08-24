use kairos_market_contract::{MarketViewKey, MarketViewKind, market_view_path};

#[test]
fn mmap_resources_are_isolated_by_market_provider_and_view() {
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
        MarketViewKind::BarWindow,
        Some("1m"),
    )
    .unwrap();
    assert_ne!(
        market_view_path("/runtime", &quote).unwrap(),
        market_view_path("/runtime", &bar).unwrap()
    );
    assert!(
        market_view_path("/runtime", &quote)
            .unwrap()
            .to_string_lossy()
            .ends_with(".e1.mmap")
    );
    assert!(
        market_view_path("/runtime", &bar)
            .unwrap()
            .to_string_lossy()
            .ends_with(".e1.mmap")
    );
    assert!(quote.resource_id().contains("quote"));
    assert!(bar.resource_id().contains("bar"));
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
