use super::{
    binance_equity_provider_catalog, binance_provider_catalog, hyperliquid_provider_catalog,
    massive_provider_catalog, okx_provider_catalog, provider_catalog_uses_current_canonical_shape,
    BinanceProduct, BinanceSpotSource, CompositeSource, HyperliquidProduct, HyperliquidSource,
    MassiveEquitySource, MassiveOptionsCoverageSource, OkxProduct, OkxSource, ReferenceSource,
};
use crate::domain::{Asset, Entity, Instrument, Market, ProviderCatalog, ReferenceResult};
use crate::services::actor::ReferenceActor;
use crate::services::sqlx_storage::{SqlxCatalogStore, SqlxProviderSyncStore};
use kairos_integration::{
    ExternalInstrument, ExternalInstrumentCatalog, ExternalInstrumentKind, ParticipantKind,
    ParticipantRef,
};
use kairos_primitives::{
    AssetClass, Currency, InstrumentId, InstrumentKind, MarketId,
    ParticipantSymbol as ProviderSymbol, Symbol,
};
use std::sync::{
    atomic::{AtomicUsize, Ordering},
    Arc,
};
use std::time::Duration;
use std::{io::Read, io::Write, net::TcpListener};

fn typed_market_id(value: &str) -> MarketId {
    MarketId::new(value).unwrap()
}

struct FlakySource {
    calls: Arc<AtomicUsize>,
}

struct RefreshingPagedSource {
    calls: usize,
}

struct PagedSource {
    calls: usize,
}

struct AlwaysFailSource;

struct FixedSource {
    id: &'static str,
    catalog: ProviderCatalog,
}

struct CountingSource {
    id: &'static str,
    catalog: ProviderCatalog,
    calls: Arc<AtomicUsize>,
}

struct BarrierSource {
    id: &'static str,
    barrier: Arc<tokio::sync::Barrier>,
}

enum TestProviderSource {
    Flaky(FlakySource),
    RefreshingPaged(RefreshingPagedSource),
    Paged(PagedSource),
    AlwaysFail(AlwaysFailSource),
    Fixed(FixedSource),
    Counting(CountingSource),
    Barrier(BarrierSource),
}

macro_rules! test_source_from {
    ($type:ty, $variant:ident) => {
        impl From<$type> for TestProviderSource {
            fn from(source: $type) -> Self {
                Self::$variant(source)
            }
        }
    };
}

test_source_from!(FlakySource, Flaky);
test_source_from!(RefreshingPagedSource, RefreshingPaged);
test_source_from!(PagedSource, Paged);
test_source_from!(AlwaysFailSource, AlwaysFail);
test_source_from!(FixedSource, Fixed);
test_source_from!(CountingSource, Counting);
test_source_from!(BarrierSource, Barrier);

#[async_trait::async_trait]
impl ReferenceSource for TestProviderSource {
    fn source_id(&self) -> &str {
        match self {
            Self::Flaky(source) => source.source_id(),
            Self::RefreshingPaged(source) => source.source_id(),
            Self::Paged(source) => source.source_id(),
            Self::AlwaysFail(source) => source.source_id(),
            Self::Fixed(source) => source.source_id(),
            Self::Counting(source) => source.source_id(),
            Self::Barrier(source) => source.source_id(),
        }
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        match self {
            Self::Flaky(source) => source.fetch_catalog().await,
            Self::RefreshingPaged(source) => source.fetch_catalog().await,
            Self::Paged(source) => source.fetch_catalog().await,
            Self::AlwaysFail(source) => source.fetch_catalog().await,
            Self::Fixed(source) => source.fetch_catalog().await,
            Self::Counting(source) => source.fetch_catalog().await,
            Self::Barrier(source) => source.fetch_catalog().await,
        }
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<super::ProviderUpdate> {
        match self {
            Self::Flaky(source) => source.fetch_catalog_step().await,
            Self::RefreshingPaged(source) => source.fetch_catalog_step().await,
            Self::Paged(source) => source.fetch_catalog_step().await,
            Self::AlwaysFail(source) => source.fetch_catalog_step().await,
            Self::Fixed(source) => source.fetch_catalog_step().await,
            Self::Counting(source) => source.fetch_catalog_step().await,
            Self::Barrier(source) => source.fetch_catalog_step().await,
        }
    }
}

#[async_trait::async_trait]
impl ReferenceSource for FlakySource {
    fn source_id(&self) -> &str {
        "test-flaky"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        if self.calls.fetch_add(1, Ordering::SeqCst) == 0 {
            Ok(ProviderCatalog {
                markets: vec![Market {
                    market_id: typed_market_id("market:test"),
                    status: "active".into(),
                    ..Default::default()
                }],
                ..Default::default()
            })
        } else {
            Err(crate::domain::ReferenceError::Provider(
                "test provider unavailable".into(),
            ))
        }
    }
}

#[tokio::test]
async fn normalized_composite_persists_facts_without_returning_a_full_catalog() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let source = FixedSource {
        id: "provider-a",
        catalog: ProviderCatalog {
            entities: vec![Entity {
                entity_id: "provider:a".into(),
                entity_type: "data_provider".into(),
                name: "Provider A".into(),
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        },
    };
    let mut composite =
        CompositeSource::new_with_sync_store(vec![TestProviderSource::from(source)], Some(store))
            .await
            .unwrap();

    assert!(composite.normalized_facts_authoritative());
    assert_eq!(
        composite.fetch_catalog().await.unwrap(),
        ProviderCatalog::default()
    );
    let mut reopened = SqlxProviderSyncStore::open(&path).await.unwrap();
    assert!(reopened.has_last_good("provider-a").await.unwrap());
    assert!(reopened
        .load_last_good("provider-a")
        .await
        .unwrap()
        .is_none());
}

#[tokio::test]
async fn actor_commits_normalized_composite_facts_without_catalog_materialization() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let provider_store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let source = FixedSource {
        id: "provider-a",
        catalog: ProviderCatalog {
            entities: vec![Entity {
                entity_id: "provider:a".into(),
                entity_type: "data_provider".into(),
                name: "Provider A".into(),
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        },
    };
    let composite = CompositeSource::new_with_sync_store(
        vec![TestProviderSource::from(source)],
        Some(provider_store),
    )
    .await
    .unwrap();
    let catalog_store = SqlxCatalogStore::open(&path).await.unwrap();
    let mut actor = ReferenceActor::new_test("reference-test", composite, catalog_store)
        .await
        .unwrap();

    let result = actor.refresh().await.unwrap();
    assert!(result.changed);
    assert_eq!(result.generation.get(), 1);
    assert_eq!(result.event_sequence.get(), 1);
    assert_eq!(result.events.len(), 1);
    let reader = kairos_reference_contract::ReferenceSqliteReader::open(&path).unwrap();
    assert!(reader.record("provider:a").unwrap().is_some());
}

#[async_trait::async_trait]
impl ReferenceSource for PagedSource {
    fn source_id(&self) -> &str {
        "test-paged"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        Ok(ProviderCatalog::default())
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<super::ProviderUpdate> {
        self.calls += 1;
        let mut markets = vec![Market {
            market_id: typed_market_id("market:page-1"),
            status: "active".into(),
            ..Default::default()
        }];
        if self.calls >= 2 {
            markets.push(Market {
                market_id: typed_market_id("market:page-2"),
                status: "active".into(),
                ..Default::default()
            });
        }
        Ok(super::ProviderUpdate {
            catalog: ProviderCatalog {
                markets,
                ..Default::default()
            },
            complete: self.calls >= 2,
            page_count: 1,
            facts_persisted: false,
        })
    }
}

#[async_trait::async_trait]
impl ReferenceSource for RefreshingPagedSource {
    fn source_id(&self) -> &str {
        "test-refreshing-paged"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        Ok(ProviderCatalog::default())
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<super::ProviderUpdate> {
        self.calls += 1;
        let market_id = if self.calls == 1 {
            "market:complete-old"
        } else {
            "market:complete-new"
        };
        Ok(super::ProviderUpdate {
            catalog: ProviderCatalog {
                markets: vec![Market {
                    market_id: typed_market_id(market_id),
                    status: "active".into(),
                    ..Default::default()
                }],
                ..Default::default()
            },
            complete: self.calls != 2,
            page_count: 1,
            facts_persisted: false,
        })
    }
}

#[async_trait::async_trait]
impl ReferenceSource for AlwaysFailSource {
    fn source_id(&self) -> &str {
        "test-flaky"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        Err(crate::domain::ReferenceError::Provider(
            "test provider unavailable after restart".into(),
        ))
    }
}

#[async_trait::async_trait]
impl ReferenceSource for FixedSource {
    fn source_id(&self) -> &str {
        self.id
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        Ok(self.catalog.clone())
    }
}

#[async_trait::async_trait]
impl ReferenceSource for CountingSource {
    fn source_id(&self) -> &str {
        self.id
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        Ok(self.catalog.clone())
    }
}

#[async_trait::async_trait]
impl ReferenceSource for BarrierSource {
    fn source_id(&self) -> &str {
        self.id
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        self.barrier.wait().await;
        Ok(ProviderCatalog::default())
    }
}

#[test]
fn okx_provider_facts_receive_canonical_identity_only_in_reference() {
    let facts = ExternalInstrumentCatalog {
        participant: ParticipantRef::new(ParticipantKind::Exchange, "okx").unwrap(),
        instruments: vec![
            ExternalInstrument {
                source_symbol: ProviderSymbol::new("BTC-USDT").unwrap(),
                source_venue: None,
                kind: ExternalInstrumentKind::Spot,
                base_currency: Some(Currency::new("BTC").unwrap()),
                quote_currency: Some(Currency::new("USDT").unwrap()),
                settlement_currency: None,
                underlying: None,
                expiry_unix_nanos: None,
                strike: None,
                option_right: None,
                active: true,
                price_tick: Some("0.1".into()),
                quantity_tick: Some("0.00001".into()),
                minimum_quantity: Some("0.00001".into()),
                minimum_notional: Some("10".into()),
                contract_value: None,
                price_precision: Some(2),
                quantity_precision: Some(5),
            },
            ExternalInstrument {
                source_symbol: ProviderSymbol::new("BTC-USDT-SWAP").unwrap(),
                source_venue: None,
                kind: ExternalInstrumentKind::Perpetual,
                base_currency: None,
                quote_currency: None,
                settlement_currency: Some(Currency::new("USDT").unwrap()),
                underlying: Some(ProviderSymbol::new("BTC-USDT").unwrap()),
                expiry_unix_nanos: None,
                strike: None,
                option_right: None,
                active: true,
                price_tick: Some("0.1".into()),
                quantity_tick: Some("0.01".into()),
                minimum_quantity: Some("0.01".into()),
                minimum_notional: None,
                contract_value: Some("0.01".into()),
                price_precision: None,
                quantity_precision: None,
            },
        ],
    };

    let catalog = okx_provider_catalog(facts).unwrap();
    assert!(catalog
        .instruments
        .iter()
        .any(|value| value.instrument_id == "instrument:spot:BTC"));
    assert!(catalog
        .instruments
        .iter()
        .any(|value| value.instrument_id == "instrument:perpetual:BTC-USDT"));
    assert!(catalog
        .markets
        .iter()
        .any(|value| value.market_id == "market:okx:perpetual:BTC-USDT-SWAP"));
    assert!(catalog
        .markets
        .iter()
        .all(|value| value.exchange_id == "exchange:okx"));
}

#[test]
fn spot_listing_expiry_does_not_split_or_mutate_the_canonical_instrument() {
    let first_expiry = kairos_primitives::UnixNanos::new(1_786_694_400_000_000_000);
    let second_expiry = kairos_primitives::UnixNanos::new(1_786_953_600_000_000_000);
    let spot = |symbol: &str, quote: &str, expiry| ExternalInstrument {
        source_symbol: ProviderSymbol::new(symbol).unwrap(),
        source_venue: None,
        kind: ExternalInstrumentKind::Spot,
        base_currency: Some(Currency::new("DUCK").unwrap()),
        quote_currency: Some(Currency::new(quote).unwrap()),
        settlement_currency: None,
        underlying: None,
        expiry_unix_nanos: Some(expiry),
        strike: None,
        option_right: None,
        active: true,
        price_tick: Some("0.0001".into()),
        quantity_tick: Some("1".into()),
        minimum_quantity: Some("1".into()),
        minimum_notional: None,
        contract_value: None,
        price_precision: Some(4),
        quantity_precision: Some(0),
    };
    let catalog = okx_provider_catalog(ExternalInstrumentCatalog {
        participant: ParticipantRef::new(ParticipantKind::Exchange, "okx").unwrap(),
        instruments: vec![
            spot("DUCK-USD", "USD", first_expiry),
            spot("DUCK-USDT", "USDT", second_expiry),
        ],
    })
    .unwrap();

    let instruments = catalog
        .instruments
        .iter()
        .filter(|value| value.instrument_id == "instrument:spot:DUCK")
        .collect::<Vec<_>>();
    assert_eq!(instruments.len(), 1);
    assert_eq!(instruments[0].symbol.as_str(), "DUCK");
    assert_eq!(instruments[0].expiry_unix_nanos, None);
    assert_eq!(
        catalog
            .listings
            .iter()
            .map(|value| value.effective_to_unix_nanos)
            .collect::<Vec<_>>(),
        vec![Some(first_expiry), Some(second_expiry)]
    );
}

#[test]
fn binance_provider_facts_receive_canonical_identity_only_in_reference() {
    let mut facts = ExternalInstrumentCatalog {
        participant: ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
        instruments: vec![
            ExternalInstrument {
                source_symbol: ProviderSymbol::new("BTCUSDT").unwrap(),
                source_venue: None,
                kind: ExternalInstrumentKind::Spot,
                base_currency: Some(Currency::new("BTC").unwrap()),
                quote_currency: Some(Currency::new("USDT").unwrap()),
                settlement_currency: None,
                underlying: None,
                expiry_unix_nanos: None,
                strike: None,
                option_right: None,
                active: true,
                price_tick: Some("0.01".into()),
                quantity_tick: Some("0.000001".into()),
                minimum_quantity: Some("0.00001".into()),
                minimum_notional: Some("10".into()),
                contract_value: None,
                price_precision: Some(2),
                quantity_precision: Some(6),
            },
            ExternalInstrument {
                source_symbol: ProviderSymbol::new("BTC-260821-50000-C").unwrap(),
                source_venue: None,
                kind: ExternalInstrumentKind::Option,
                base_currency: Some(Currency::new("BTC").unwrap()),
                quote_currency: Some(Currency::new("USDT").unwrap()),
                settlement_currency: Some(Currency::new("USDT").unwrap()),
                underlying: Some(ProviderSymbol::new("BTCUSDT").unwrap()),
                expiry_unix_nanos: Some(kairos_primitives::UnixNanos::new(
                    1_780_000_000_000_000_000,
                )),
                strike: Some("50000".into()),
                option_right: Some("call".into()),
                active: true,
                price_tick: None,
                quantity_tick: None,
                minimum_quantity: None,
                minimum_notional: None,
                contract_value: Some("1".into()),
                price_precision: None,
                quantity_precision: None,
            },
        ],
    };
    let option = facts.instruments.pop().expect("option facts");
    let spot_catalog = binance_provider_catalog(facts, BinanceProduct::Spot).unwrap();
    let option_catalog = binance_provider_catalog(
        ExternalInstrumentCatalog {
            participant: ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            instruments: vec![option],
        },
        BinanceProduct::Option,
    )
    .unwrap();
    assert!(spot_catalog
        .instruments
        .iter()
        .any(|value| value.instrument_id == "instrument:spot:BTC"));
    assert!(option_catalog.instruments.iter().any(|value| {
        value.instrument_id == "instrument:option:BTC-USDT:20260528:50000:C"
            && value.underlying_instrument_id.as_deref() == Some("instrument:spot:BTC")
    }));
    assert!(spot_catalog.markets.iter().any(|value| {
        value.market_id == "market:binance:spot:BTCUSDT"
            && value.minimum_notional.as_deref() == Some("10")
    }));
}

#[test]
fn binance_derivative_facts_keep_product_selection_but_not_canonical_identity() {
    let expiry = kairos_primitives::UnixNanos::new(1_782_432_000_000_000_000);
    let catalog = binance_provider_catalog(
        ExternalInstrumentCatalog {
            participant: ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            instruments: vec![ExternalInstrument {
                source_symbol: ProviderSymbol::new("BTCUSDT_260626").unwrap(),
                source_venue: None,
                kind: ExternalInstrumentKind::Future,
                base_currency: Some(Currency::new("BTC").unwrap()),
                quote_currency: Some(Currency::new("USDT").unwrap()),
                settlement_currency: Some(Currency::new("USDT").unwrap()),
                underlying: Some(ProviderSymbol::new("BTCUSDT").unwrap()),
                expiry_unix_nanos: Some(expiry),
                strike: None,
                option_right: None,
                active: true,
                price_tick: Some("0.1".into()),
                quantity_tick: Some("0.001".into()),
                minimum_quantity: Some("0.001".into()),
                minimum_notional: None,
                contract_value: None,
                price_precision: Some(1),
                quantity_precision: Some(3),
            }],
        },
        BinanceProduct::UsdMFutures,
    )
    .unwrap();
    assert_eq!(
        catalog.instruments[0].instrument_id,
        "instrument:future:BTC-USDT:20260626"
    );
    assert_eq!(
        catalog.markets[0].market_id,
        "market:binance:future:BTCUSDT_260626"
    );
    assert_eq!(catalog.markets[0].effective_to_unix_nanos, Some(expiry));
}

#[test]
fn binance_equity_perpetual_has_no_expiry_and_links_canonical_equity() {
    let catalog = binance_provider_catalog(
        ExternalInstrumentCatalog {
            participant: ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            instruments: vec![ExternalInstrument {
                source_symbol: ProviderSymbol::new("AAPLUSDT").unwrap(),
                source_venue: None,
                kind: ExternalInstrumentKind::EquityPerpetual,
                base_currency: Some(Currency::new("AAPL").unwrap()),
                quote_currency: Some(Currency::new("USDT").unwrap()),
                settlement_currency: Some(Currency::new("USDT").unwrap()),
                underlying: Some(ProviderSymbol::new("AAPL").unwrap()),
                expiry_unix_nanos: None,
                strike: None,
                option_right: None,
                active: true,
                price_tick: Some("0.01".into()),
                quantity_tick: Some("0.01".into()),
                minimum_quantity: Some("0.01".into()),
                minimum_notional: None,
                contract_value: None,
                price_precision: Some(2),
                quantity_precision: Some(2),
            }],
        },
        BinanceProduct::UsdMFutures,
    )
    .unwrap();
    let market = &catalog.markets[0];
    assert_eq!(market.market_id, "market:binance:perpetual:AAPLUSDT");
    assert_eq!(market.asset_type, Some(AssetClass::Equity));
    assert_eq!(
        market.underlying_instrument_id.as_deref(),
        Some("instrument:equity:US:AAPL:common")
    );
    assert_eq!(market.effective_to_unix_nanos, None);
    let derivative = catalog
        .instruments
        .iter()
        .find(|value| value.instrument_type == "perpetual")
        .unwrap();
    assert_eq!(
        derivative.instrument_id,
        "instrument:perpetual:equity:US:AAPL:USDT"
    );
    assert_eq!(
        derivative.underlying_instrument_id.as_deref(),
        Some("instrument:equity:US:AAPL:common")
    );
    assert_eq!(derivative.expiry_unix_nanos, None);
}

#[test]
fn binance_equity_broker_catalog_does_not_invent_exchange_listing_or_market() {
    let catalog = binance_equity_provider_catalog(ExternalInstrumentCatalog {
        participant: ParticipantRef::new(ParticipantKind::Broker, "binance").unwrap(),
        instruments: vec![ExternalInstrument {
            source_symbol: ProviderSymbol::new("AAPL").unwrap(),
            source_venue: None,
            kind: ExternalInstrumentKind::Equity,
            base_currency: None,
            quote_currency: None,
            settlement_currency: None,
            underlying: None,
            expiry_unix_nanos: None,
            strike: None,
            option_right: None,
            active: true,
            price_tick: None,
            quantity_tick: Some("0.000000001".into()),
            minimum_quantity: None,
            minimum_notional: Some("5.00000000".into()),
            contract_value: None,
            price_precision: None,
            quantity_precision: Some(9),
        }],
    })
    .unwrap();
    assert_eq!(
        catalog.instruments[0].instrument_id,
        "instrument:equity:US:AAPL:common"
    );
    assert!(catalog.entities.is_empty());
    assert!(catalog.listings.is_empty());
    assert!(catalog.markets.is_empty());
}

#[test]
fn massive_provider_facts_receive_canonical_identity_only_in_reference() {
    let expiry = kairos_primitives::UnixNanos::new(1_800_000_000_000_000_000);
    let catalog = massive_provider_catalog(ExternalInstrumentCatalog {
        participant: ParticipantRef::new(ParticipantKind::DataProvider, "massive").unwrap(),
        instruments: vec![ExternalInstrument {
            source_symbol: ProviderSymbol::new("O:SPY260821C00500000").unwrap(),
            source_venue: Some("BATO".into()),
            kind: ExternalInstrumentKind::Option,
            base_currency: None,
            quote_currency: Some(Currency::new("USD").unwrap()),
            settlement_currency: None,
            underlying: Some(ProviderSymbol::new("SPY").unwrap()),
            expiry_unix_nanos: Some(expiry),
            strike: Some("500".into()),
            option_right: Some("call".into()),
            active: true,
            price_tick: Some("0.01".into()),
            quantity_tick: Some("1".into()),
            minimum_quantity: None,
            minimum_notional: None,
            contract_value: Some("100".into()),
            price_precision: Some(2),
            quantity_precision: Some(0),
        }],
    })
    .unwrap();
    assert!(catalog.entities.iter().any(|value| {
        value.entity_id == "exchange:cboe-bzx-options" && value.name == "Cboe BZX Options Exchange"
    }));
    assert!(catalog
        .instruments
        .iter()
        .any(|value| { value.instrument_id == "instrument:equity:US:SPY:common" }));
    assert!(catalog.instruments.iter().any(|value| {
        value.instrument_id == "instrument:option:SPY:20270115:500:C"
            && value.underlying_instrument_id.as_deref() == Some("instrument:equity:US:SPY:common")
    }));
    assert!(catalog.listings.iter().any(|value| {
        value.listing_id == "listing:exchange:cboe-bzx-options:option:SPY-20270115-500-C"
            && value.exchange_id == "exchange:cboe-bzx-options"
    }));
    assert!(catalog.markets.is_empty());
}

#[test]
fn massive_same_ticker_on_distinct_primary_venues_has_distinct_listings() {
    let equity = |venue: &str| ExternalInstrument {
        source_symbol: ProviderSymbol::new("BCPC").unwrap(),
        source_venue: Some(venue.into()),
        kind: ExternalInstrumentKind::Equity,
        base_currency: None,
        quote_currency: Some(Currency::new("USD").unwrap()),
        settlement_currency: None,
        underlying: None,
        expiry_unix_nanos: None,
        strike: None,
        option_right: None,
        active: true,
        price_tick: Some("0.01".into()),
        quantity_tick: Some("1".into()),
        minimum_quantity: None,
        minimum_notional: None,
        contract_value: Some("1".into()),
        price_precision: Some(2),
        quantity_precision: Some(0),
    };
    let catalog = massive_provider_catalog(ExternalInstrumentCatalog {
        participant: ParticipantRef::new(ParticipantKind::DataProvider, "massive").unwrap(),
        instruments: vec![equity("XNAS"), equity("XNYS")],
    })
    .unwrap();

    assert_eq!(catalog.listings.len(), 2);
    let ids = catalog
        .listings
        .iter()
        .map(|listing| listing.listing_id.as_str())
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(ids.len(), 2);
    assert!(ids.contains("listing:exchange:nasdaq:equity:BCPC:USD"));
    assert!(ids.contains("listing:exchange:nyse:equity:BCPC:USD"));
    assert!(catalog.markets.is_empty());
}

#[test]
fn hyperliquid_provider_facts_receive_canonical_identity_only_in_reference() {
    let catalog = hyperliquid_provider_catalog(
        ExternalInstrumentCatalog {
            participant: ParticipantRef::new(ParticipantKind::Exchange, "hyperliquid").unwrap(),
            instruments: vec![ExternalInstrument {
                source_symbol: ProviderSymbol::new("BTC").unwrap(),
                source_venue: None,
                kind: ExternalInstrumentKind::Perpetual,
                base_currency: Some(Currency::new("BTC").unwrap()),
                quote_currency: Some(Currency::new("USDC").unwrap()),
                settlement_currency: Some(Currency::new("USDC").unwrap()),
                underlying: None,
                expiry_unix_nanos: None,
                strike: None,
                option_right: None,
                active: true,
                price_tick: None,
                quantity_tick: None,
                minimum_quantity: None,
                minimum_notional: None,
                contract_value: None,
                price_precision: None,
                quantity_precision: Some(5),
            }],
        },
        HyperliquidProduct::Perpetual,
    )
    .unwrap();
    assert_eq!(
        catalog.instruments[0].instrument_id,
        "instrument:perpetual:BTC-USDC"
    );
    assert_eq!(
        catalog.markets[0].market_id,
        "market:hyperliquid:perpetual:BTC"
    );
    assert_eq!(
        catalog.markets[0].quote_asset_id.as_deref(),
        Some("asset:crypto:USDC")
    );
}

#[test]
fn hyperliquid_spot_and_perpetual_have_distinct_provider_products() {
    let catalog = hyperliquid_provider_catalog(
        ExternalInstrumentCatalog {
            participant: ParticipantRef::new(ParticipantKind::Exchange, "hyperliquid").unwrap(),
            instruments: vec![ExternalInstrument {
                source_symbol: ProviderSymbol::new("PURR/USDC").unwrap(),
                source_venue: None,
                kind: ExternalInstrumentKind::Spot,
                base_currency: Some(Currency::new("PURR").unwrap()),
                quote_currency: Some(Currency::new("USDC").unwrap()),
                settlement_currency: None,
                underlying: None,
                expiry_unix_nanos: None,
                strike: None,
                option_right: None,
                active: true,
                price_tick: None,
                quantity_tick: None,
                minimum_quantity: None,
                minimum_notional: None,
                contract_value: None,
                price_precision: None,
                quantity_precision: Some(0),
            }],
        },
        HyperliquidProduct::Spot,
    )
    .unwrap();
    assert_eq!(catalog.instruments[0].instrument_id, "instrument:spot:PURR");
    assert_eq!(catalog.markets[0].instrument_kind, InstrumentKind::Spot);
    assert_eq!(
        catalog.markets[0].venue_symbol.as_deref(),
        Some("PURR/USDC")
    );
}

#[tokio::test]
async fn provider_failure_keeps_last_known_good_snapshot() {
    let calls = Arc::new(AtomicUsize::new(0));
    let mut source = CompositeSource::new(vec![TestProviderSource::from(FlakySource {
        calls: Arc::clone(&calls),
    })])
    .await
    .unwrap();
    let first = source.fetch_catalog().await.unwrap();
    let second = source.fetch_catalog().await.unwrap();
    assert_eq!(first.markets, second.markets);
    assert_eq!(calls.load(Ordering::SeqCst), 2);
    assert_eq!(source.provider_health()[0].status, "stale");
    assert!(source.provider_health()[0].stale);
}

#[tokio::test]
async fn targeted_refresh_does_not_poll_unrelated_provider() {
    let peer_calls = Arc::new(AtomicUsize::new(0));
    let mut source = CompositeSource::new(vec![
        TestProviderSource::from(PagedSource { calls: 0 }),
        TestProviderSource::from(CountingSource {
            id: "test-peer",
            catalog: ProviderCatalog {
                markets: vec![Market {
                    market_id: typed_market_id("market:peer"),
                    status: "active".into(),
                    ..Default::default()
                }],
                ..Default::default()
            },
            calls: Arc::clone(&peer_calls),
        }),
    ])
    .await
    .unwrap();

    assert!(source.advance_source("test-paged").await.unwrap().is_none());
    let catalog = source
        .advance_source("test-paged")
        .await
        .unwrap()
        .expect("completed provider has a last-known-good catalog");

    assert_eq!(peer_calls.load(Ordering::SeqCst), 0);
    assert_eq!(catalog.markets.len(), 2);
    assert!(catalog
        .markets
        .iter()
        .any(|market| market.market_id == "market:page-2"));
}

#[tokio::test]
async fn paused_provider_keeps_its_snapshot_without_polling_peer() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let paused_calls = Arc::new(AtomicUsize::new(0));
    let store = SqlxProviderSyncStore::open_legacy(&path).await.unwrap();
    let mut source = CompositeSource::new_with_sync_store(
        vec![
            TestProviderSource::from(FixedSource {
                id: "binance-spot",
                catalog: ProviderCatalog {
                    markets: vec![Market {
                        market_id: typed_market_id("market:binance"),
                        status: "active".into(),
                        ..Default::default()
                    }],
                    ..Default::default()
                },
            }),
            TestProviderSource::from(CountingSource {
                id: "massive-options",
                catalog: ProviderCatalog {
                    markets: vec![Market {
                        market_id: typed_market_id("market:test-venue:option"),
                        status: "active".into(),
                        ..Default::default()
                    }],
                    ..Default::default()
                },
                calls: Arc::clone(&paused_calls),
            }),
        ],
        Some(store),
    )
    .await
    .unwrap();

    assert_eq!(source.fetch_catalog().await.unwrap().markets.len(), 2);
    assert_eq!(paused_calls.load(Ordering::SeqCst), 1);
    source
        .set_source_paused("massive-options", true)
        .await
        .unwrap();
    let catalog = source.fetch_catalog().await.unwrap();

    assert_eq!(paused_calls.load(Ordering::SeqCst), 1);
    assert_eq!(catalog.markets.len(), 2);
    assert_eq!(
        source
            .provider_health()
            .into_iter()
            .find(|health| health.source_id == "massive-options")
            .unwrap()
            .status,
        "paused"
    );
}

#[tokio::test]
async fn provider_last_known_good_survives_composite_restart() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    {
        let store = SqlxProviderSyncStore::open_legacy(&path).await.unwrap();
        let calls = Arc::new(AtomicUsize::new(0));
        let mut source = CompositeSource::new_with_sync_store(
            vec![TestProviderSource::from(FlakySource {
                calls: Arc::clone(&calls),
            })],
            Some(store),
        )
        .await
        .unwrap();
        assert_eq!(source.fetch_catalog().await.unwrap().markets.len(), 1);
    }
    let store = SqlxProviderSyncStore::open_legacy(&path).await.unwrap();
    let mut restarted = CompositeSource::new_with_sync_store(
        vec![TestProviderSource::from(AlwaysFailSource)],
        Some(store),
    )
    .await
    .unwrap();
    let catalog = restarted.fetch_catalog().await.unwrap();
    assert_eq!(catalog.markets.len(), 1);
    assert_eq!(restarted.provider_health()[0].status, "stale");
}

#[tokio::test]
async fn provider_without_last_known_good_rejects_partial_refresh() {
    let mut source = CompositeSource::new(vec![TestProviderSource::from(AlwaysFailSource)])
        .await
        .unwrap();
    let error = source.fetch_catalog().await.unwrap_err().to_string();
    assert!(error.contains("without a last-known-good snapshot"));
}

#[tokio::test]
async fn provider_failure_opens_circuit_and_applies_backoff() {
    let mut source = CompositeSource::new(vec![TestProviderSource::from(AlwaysFailSource)])
        .await
        .unwrap();
    let _ = source.fetch_catalog().await;
    assert_eq!(source.provider_health()[0].consecutive_failures, 1);
    let error = source.fetch_catalog().await.unwrap_err().to_string();
    assert!(error.contains("provider circuit is open"));
    assert_eq!(source.provider_health()[0].consecutive_failures, 1);
}

#[tokio::test]
async fn irreconcilable_provider_record_collision_rejects_the_refresh() {
    let first = FixedSource {
        id: "provider-a",
        catalog: ProviderCatalog {
            markets: vec![Market {
                market_id: typed_market_id("market:shared"),
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        },
    };
    let second = FixedSource {
        id: "provider-b",
        catalog: ProviderCatalog {
            markets: vec![Market {
                market_id: typed_market_id("market:shared"),
                status: "inactive".into(),
                ..Default::default()
            }],
            ..Default::default()
        },
    };
    let mut source = CompositeSource::new(vec![
        TestProviderSource::from(first),
        TestProviderSource::from(second),
    ])
    .await
    .unwrap();
    let error = source.fetch_catalog().await.unwrap_err().to_string();
    assert!(error.contains("irreconcilable canonical record conflicts"));
    assert!(error.contains("market:market:shared"));
}

#[tokio::test]
async fn shared_canonical_instrument_aggregates_listing_availability() {
    let instrument = |status| Instrument {
        instrument_id: InstrumentId::new("instrument:spot:BTC").unwrap(),
        symbol: Symbol::new("BTC").unwrap(),
        instrument_type: InstrumentKind::Spot,
        primary_currency_asset_id: Some(
            kairos_primitives::AssetId::new("asset:crypto:BTC").unwrap(),
        ),
        status,
        ..Instrument::default()
    };
    let mut source = CompositeSource::new(vec![
        TestProviderSource::from(FixedSource {
            id: "provider-a",
            catalog: ProviderCatalog {
                instruments: vec![instrument("active".into())],
                ..ProviderCatalog::default()
            },
        }),
        TestProviderSource::from(FixedSource {
            id: "provider-b",
            catalog: ProviderCatalog {
                instruments: vec![instrument("inactive".into())],
                ..ProviderCatalog::default()
            },
        }),
    ])
    .await
    .unwrap();

    let catalog = source.fetch_catalog().await.unwrap();
    assert_eq!(catalog.instruments.len(), 1);
    assert_eq!(catalog.instruments[0].status, "active".into());
    assert_eq!(catalog.instruments[0].source_id, None);
}

#[tokio::test]
async fn shared_canonical_instrument_is_enriched_by_an_authoritative_optional_fact() {
    let instrument = || Instrument {
        instrument_id: InstrumentId::new("instrument:spot:BTC").unwrap(),
        symbol: Symbol::new("BTC").unwrap(),
        instrument_type: InstrumentKind::Spot,
        primary_currency_asset_id: Some(
            kairos_primitives::AssetId::new("asset:crypto:BTC").unwrap(),
        ),
        status: "active".into(),
        ..Instrument::default()
    };
    let mut without_currency = instrument();
    without_currency.primary_currency_asset_id = None;
    let with_currency = instrument();
    let mut source = CompositeSource::new(vec![
        TestProviderSource::from(FixedSource {
            id: "provider-a",
            catalog: ProviderCatalog {
                instruments: vec![without_currency],
                ..ProviderCatalog::default()
            },
        }),
        TestProviderSource::from(FixedSource {
            id: "provider-b",
            catalog: ProviderCatalog {
                instruments: vec![with_currency],
                ..ProviderCatalog::default()
            },
        }),
    ])
    .await
    .unwrap();

    let catalog = source.fetch_catalog().await.unwrap();
    assert_eq!(
        catalog.instruments[0].primary_currency_asset_id.as_deref(),
        Some("asset:crypto:BTC")
    );
}

#[tokio::test]
async fn shared_canonical_asset_is_active_when_any_provider_observes_it_active() {
    let asset = |status| Asset {
        asset_id: kairos_primitives::AssetId::new("asset:equity:AVB").unwrap(),
        code: "AVB".into(),
        asset_class: AssetClass::Equity,
        status,
        ..Asset::default()
    };
    let mut source = CompositeSource::new(vec![
        TestProviderSource::from(FixedSource {
            id: "provider-a",
            catalog: ProviderCatalog {
                assets: vec![asset("inactive".into())],
                ..ProviderCatalog::default()
            },
        }),
        TestProviderSource::from(FixedSource {
            id: "provider-b",
            catalog: ProviderCatalog {
                assets: vec![asset("active".into())],
                ..ProviderCatalog::default()
            },
        }),
    ])
    .await
    .unwrap();

    let catalog = source.fetch_catalog().await.unwrap();
    assert_eq!(catalog.assets[0].status, "active".into());
}

#[test]
fn obsolete_provider_snapshot_shape_is_not_eligible_for_fallback() {
    let canonical = Instrument {
        instrument_id: InstrumentId::new("instrument:spot:BTC").unwrap(),
        symbol: Symbol::new("BTC").unwrap(),
        instrument_type: InstrumentKind::Spot,
        primary_currency_asset_id: Some(
            kairos_primitives::AssetId::new("asset:crypto:BTC").unwrap(),
        ),
        status: "active".into(),
        ..Instrument::default()
    };
    assert!(provider_catalog_uses_current_canonical_shape(
        &ProviderCatalog {
            instruments: vec![canonical.clone()],
            ..ProviderCatalog::default()
        }
    ));

    let mut legacy_quote_owned = canonical.clone();
    legacy_quote_owned.primary_currency_asset_id =
        Some(kairos_primitives::AssetId::new("asset:crypto:USDT").unwrap());
    assert!(!provider_catalog_uses_current_canonical_shape(
        &ProviderCatalog {
            instruments: vec![legacy_quote_owned],
            ..ProviderCatalog::default()
        }
    ));

    let mut provider_owned = canonical;
    provider_owned.source_id = Some("binance-spot".into());
    assert!(!provider_catalog_uses_current_canonical_shape(
        &ProviderCatalog {
            instruments: vec![provider_owned],
            ..ProviderCatalog::default()
        }
    ));
}

#[tokio::test]
async fn provider_fan_in_polls_sources_concurrently_on_caller_runtime() {
    let barrier = Arc::new(tokio::sync::Barrier::new(2));
    let mut source = CompositeSource::new(vec![
        TestProviderSource::from(BarrierSource {
            id: "provider-a",
            barrier: Arc::clone(&barrier),
        }),
        TestProviderSource::from(BarrierSource {
            id: "provider-b",
            barrier,
        }),
    ])
    .await
    .unwrap();

    tokio::time::timeout(Duration::from_secs(1), source.fetch_catalog())
        .await
        .expect("provider futures must be polled concurrently")
        .unwrap();
}

#[tokio::test]
async fn hyperliquid_async_capability_maps_through_reference_end_to_end() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 4096];
        let length = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..length]).contains("metaAndAssetCtxs"));
        let body = r#"[{"universe":[{"name":"BTC","szDecimals":5}]},[{"markPx":"50000"}]]"#;
        write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
    });
    let mut source =
        HyperliquidSource::new(format!("http://{address}/info")).expect("build source");

    let catalog = source.fetch_catalog().await.unwrap();
    server.join().unwrap();

    assert_eq!(
        catalog.instruments[0].instrument_id,
        "instrument:perpetual:BTC-USDC"
    );
    assert_eq!(
        catalog.markets[0].market_id,
        "market:hyperliquid:perpetual:BTC"
    );
}

#[tokio::test]
async fn binance_async_capability_maps_through_reference_end_to_end() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 4096];
        let length = stream.read(&mut request).unwrap();
        assert!(
            String::from_utf8_lossy(&request[..length]).starts_with("GET /api/v3/exchangeInfo ")
        );
        let body = r#"{"symbols":[{"symbol":"BTCUSDT","baseAsset":"BTC","quoteAsset":"USDT","status":"TRADING","baseAssetPrecision":6,"quoteAssetPrecision":2,"filters":[{"filterType":"PRICE_FILTER","tickSize":"0.01"},{"filterType":"LOT_SIZE","stepSize":"0.00001","minQty":"0.00001"},{"filterType":"MIN_NOTIONAL","minNotional":"10"}]}]}"#;
        write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
    });
    let mut source =
        BinanceSpotSource::new(format!("http://{address}")).expect("build Binance source");

    let catalog = source.fetch_catalog().await.unwrap();
    server.join().unwrap();

    assert_eq!(catalog.markets.len(), 1);
    assert_eq!(catalog.markets[0].market_id, "market:binance:spot:BTCUSDT");
    assert_eq!(catalog.markets[0].price_tick.as_deref(), Some("0.01"));
    assert_eq!(catalog.markets[0].minimum_notional.as_deref(), Some("10"));
}

#[tokio::test]
async fn okx_async_capability_maps_through_reference_end_to_end() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 4096];
        let length = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..length])
            .starts_with("GET /api/v5/public/instruments?instType=SWAP "));
        let body = r#"{"code":"0","data":[{"instId":"BTC-USDT-SWAP","uly":"BTC-USDT","settleCcy":"USDT","state":"live","tickSz":"0.1","lotSz":"0.01","minSz":"0.01","ctVal":"0.01"}]}"#;
        write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
    });
    let mut source = OkxSource::new("okx-swap", OkxProduct::Swap, format!("http://{address}"))
        .expect("build OKX source");

    let catalog = source.fetch_catalog().await.unwrap();
    server.join().unwrap();

    assert_eq!(catalog.markets.len(), 1);
    assert_eq!(
        catalog.markets[0].market_id,
        "market:okx:perpetual:BTC-USDT-SWAP"
    );
    assert_eq!(catalog.markets[0].price_tick.as_deref(), Some("0.1"));
    assert_eq!(catalog.markets[0].contract_size.as_deref(), Some("0.01"));
}

#[tokio::test]
async fn okx_margin_is_spot_identity_with_explicit_margin_access() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 4096];
        let length = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..length])
            .starts_with("GET /api/v5/public/instruments?instType=MARGIN "));
        let body = r#"{"code":"0","data":[{"instId":"BTC-USDT","baseCcy":"BTC","quoteCcy":"USDT","state":"live","tickSz":"0.1","lotSz":"0.001","minSz":"0.001"}]}"#;
        write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
    });
    let mut source = OkxSource::new(
        "okx-margin",
        OkxProduct::Margin,
        format!("http://{address}"),
    )
    .unwrap();

    let catalog = source.fetch_catalog().await.unwrap();
    server.join().unwrap();

    assert_eq!(catalog.instruments[0].instrument_id, "instrument:spot:BTC");
    assert_eq!(catalog.instruments[0].instrument_type, InstrumentKind::Spot);
    assert_eq!(catalog.markets[0].instrument_kind, InstrumentKind::Spot);
}

#[tokio::test]
async fn massive_persists_each_successful_page_before_a_later_page_fails() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        for page in 0..2 {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 4096];
            let _ = stream.read(&mut request).unwrap();
            let body = if page == 0 {
                format!(
                    r#"{{"results":[{{"ticker":"AAPL","primary_exchange":"XNAS","active":true}}],"next_url":"http://{address}/v3/reference/tickers?cursor=page-2"}}"#
                )
            } else {
                r#"{"status":"OK"}"#.to_owned()
            };
            write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                )
                .unwrap();
        }
    });
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open_legacy(&path).await.unwrap();
    let mut source =
        MassiveEquitySource::new_with_sync_store("test-key", format!("http://{address}"), store)
            .await
            .unwrap();

    let first = source.fetch_catalog_step().await.unwrap();
    assert!(!first.complete);
    assert_eq!(first.page_count, 1);
    assert!(source.fetch_catalog_step().await.is_err());
    server.join().unwrap();

    let mut reopened = SqlxProviderSyncStore::open_legacy(&path).await.unwrap();
    let (cursor, accumulated) = reopened
        .load_state("massive-equity")
        .await
        .unwrap()
        .unwrap();
    assert_eq!(cursor.as_deref(), Some("page-2"));
    assert!(accumulated.is_none());
    assert!(reopened
        .staged_pages("massive-equity")
        .await
        .unwrap()
        .into_iter()
        .flat_map(|catalog| catalog.instruments)
        .any(|instrument| instrument.symbol == "AAPL"));
}

#[tokio::test]
async fn massive_options_coverage_is_explicit_and_scoped_to_one_underlying() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 4096];
        let length = stream.read(&mut request).unwrap();
        let request = String::from_utf8_lossy(&request[..length]);
        assert!(request.contains("underlying_ticker=SPY"));
        assert!(request.contains("expired=false"));
        let body = r#"{"results":[{"ticker":"O:SPY260821C00500000","underlying_ticker":"SPY","primary_exchange":"OPRA","expiration_date":"2026-08-21","strike_price":500,"contract_type":"call","active":true},{"ticker":"O:SPY260821P00400000","underlying_ticker":"SPY","primary_exchange":"OPRA","expiration_date":"2026-08-21","strike_price":400,"contract_type":"put","active":false}]}"#;
        write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
    });
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open_legacy(&path).await.unwrap();
    let mut source =
        MassiveOptionsCoverageSource::new("test-key", format!("http://{address}"), store)
            .await
            .unwrap();

    assert!(source.option_underlyings().is_empty());
    source.set_option_underlying("spy", true).await.unwrap();
    assert_eq!(source.option_underlyings(), vec!["SPY"]);
    let completed = source.fetch_catalog_step().await.unwrap();
    server.join().unwrap();
    assert!(completed.complete);
    assert!(completed
        .catalog
        .instruments
        .iter()
        .any(|instrument| instrument.instrument_id == "instrument:option:SPY:20260821:500:C"));
    assert!(!completed
        .catalog
        .instruments
        .iter()
        .any(|instrument| instrument.instrument_id == "instrument:option:SPY:20260821:400:P"));
    assert!(completed.catalog.listings.is_empty());
    assert!(completed.catalog.markets.is_empty());

    source.set_option_underlying("SPY", false).await.unwrap();
    let removed = source.fetch_catalog_step().await.unwrap();
    assert!(removed.complete);
    assert!(removed.catalog.instruments.is_empty());
    assert!(removed.catalog.markets.is_empty());
}

#[tokio::test]
async fn massive_full_catalog_resumes_from_persisted_incremental_cursor() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        for page in 0..9 {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 4096];
            let length = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..length]);
            if page > 0 {
                assert!(request.contains(&format!("cursor=page-{page}")));
            }
            let next = if page < 8 {
                format!(
                    r#", "next_url":"http://{address}/v3/reference/tickers?cursor=page-{}""#,
                    page + 1
                )
            } else {
                String::new()
            };
            let body = format!(
                r#"{{"results":[{{"ticker":"TEST{page}","primary_exchange":"XNAS","active":true}}]{next}}}"#
            );
            write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                )
                .unwrap();
        }
    });
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open_legacy(&path).await.unwrap();
    let mut first =
        MassiveEquitySource::new_with_sync_store("test-key", format!("http://{address}"), store)
            .await
            .unwrap();

    let partial = first.fetch_catalog_step().await.unwrap();
    assert!(!partial.complete);
    assert_eq!(partial.page_count, 1);
    assert!(partial.catalog.markets.is_empty());
    drop(first);

    let store = SqlxProviderSyncStore::open_legacy(&path).await.unwrap();
    let mut resumed =
        MassiveEquitySource::new_with_sync_store("test-key", format!("http://{address}"), store)
            .await
            .unwrap();
    for _ in 1..8 {
        let partial = resumed.fetch_catalog_step().await.unwrap();
        assert!(!partial.complete);
        assert_eq!(partial.page_count, 1);
        assert!(partial.catalog.markets.is_empty());
    }
    let complete = resumed.fetch_catalog_step().await.unwrap();
    server.join().unwrap();

    assert!(complete.complete);
    assert_eq!(complete.page_count, 1);
    assert_eq!(
        complete
            .catalog
            .instruments
            .iter()
            .filter(|instrument| instrument.instrument_type == InstrumentKind::Equity)
            .count(),
        9
    );
    assert!(complete
        .catalog
        .instruments
        .iter()
        .any(|instrument| instrument.symbol == "TEST0"));
    assert!(complete
        .catalog
        .instruments
        .iter()
        .any(|instrument| instrument.symbol == "TEST8"));
}

#[tokio::test]
async fn partial_provider_pages_do_not_drop_previous_page() {
    let mut source = CompositeSource::new(vec![TestProviderSource::from(PagedSource { calls: 0 })])
        .await
        .unwrap();
    let first_error = source.fetch_catalog().await.unwrap_err();
    assert!(matches!(
        &first_error,
        crate::domain::ReferenceError::SyncInProgress { .. }
    ));
    let first_error = first_error.to_string();
    assert!(first_error.contains("synchronization in progress"));
    assert!(first_error.contains("without last-known-good facts"));
    assert_eq!(source.provider_health()[0].status, "syncing");
    let second = source.fetch_catalog().await.unwrap();
    assert_eq!(second.markets.len(), 2);
    assert!(second
        .markets
        .iter()
        .any(|market| market.market_id == "market:page-1"));
}

#[tokio::test]
async fn incomplete_provider_sync_does_not_replace_last_good_snapshot() {
    let mut source = CompositeSource::new(vec![TestProviderSource::from(RefreshingPagedSource {
        calls: 0,
    })])
    .await
    .unwrap();
    let first = source.fetch_catalog().await.unwrap();
    assert_eq!(first.markets[0].market_id, "market:complete-old");
    let second = source.fetch_catalog().await.unwrap();
    assert_eq!(second.markets[0].market_id, "market:complete-old");
    assert_eq!(source.provider_health()[0].status, "ready");
    assert!(!source.provider_health()[0].stale);
    let third = source.fetch_catalog().await.unwrap();
    assert_eq!(third.markets[0].market_id, "market:complete-new");
    assert_eq!(source.provider_health()[0].status, "ready");
}
