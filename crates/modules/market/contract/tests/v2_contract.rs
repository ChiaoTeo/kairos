use kairos_market_contract::{MarketViewKey, MarketViewKind};

#[test]
fn mmap_resources_are_isolated_by_market_source_and_view() {
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
        quote.resource_path("/runtime"),
        bar.resource_path("/runtime")
    );
    assert!(quote
        .resource_path("/runtime")
        .to_string_lossy()
        .ends_with(".e1.mmap"));
    assert!(bar
        .resource_path("/runtime")
        .to_string_lossy()
        .ends_with(".e1.mmap"));
    assert!(quote.resource_id().contains("quote"));
    assert!(bar.resource_id().contains("bar"));
}

#[test]
fn canonical_view_key_contains_all_partition_identity() {
    let key = MarketViewKey::new(
        "market:fixture",
        "source:fixture",
        MarketViewKind::OrderBook,
        None::<String>,
    )
    .unwrap();
    assert_eq!(
        key.canonical_key(),
        "scope=market:fixture;source=source:fixture;view=order-book;qualifier="
    );
}
