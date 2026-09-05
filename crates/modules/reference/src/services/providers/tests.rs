use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use kairos_conflux::{
    BinanceCredential, ExternalInstrument, ExternalInstrumentCatalog, ExternalInstrumentKind,
    ExternalVenue, ExternalVenueKind, ParticipantKind, ParticipantRef,
};
use kairos_integration::composition::ProviderConnectionProfile;
use kairos_primitives::integration::ParticipantSymbol as ExternalSymbol;
use kairos_primitives::reference::{
    AssetClass, Currency, ExchangeId, InstrumentId, InstrumentKind, MarketId, Symbol,
};

use super::{
    BinanceProduct, BinanceSpotSource, HyperliquidProduct, HyperliquidSource, MassiveEquitySource,
    MassiveOptionsCoverageSource, OkxProduct, OkxSource, ProviderFanInSource,
    ReferenceCredentialResolver, ReferenceSource, binance_equity_provider_catalog,
    binance_provider_catalog, hyperliquid_provider_catalog, massive_provider_catalog,
    okx_provider_catalog, provider_catalog_uses_current_canonical_shape,
};
use crate::domain::{
    Asset, Exchange, Instrument, Market, ProviderCatalog, ReferenceResult,
    ReferenceSourceDefinition, SourceDesiredState, SourceRuntimePhase, SourceScope,
    SourceSyncPolicy, SourceTickBudget,
};
use crate::services::actor::ReferenceActor;
use crate::services::sources::ConfiguredProviderSource;
use crate::services::storage::catalog_store::SqlxCatalogStore;

fn reference_connection(
    connection_id: &str,
    provider: &str,
    credential_id: &str,
    products: &[&str],
) -> ProviderConnectionProfile {
    ProviderConnectionProfile {
        connection_id: connection_id.into(),
        provider: provider.into(),
        environment: "production".into(),
        endpoint: match provider {
            "binance" => super::default_endpoint("binance-spot"),
            "massive" => super::default_endpoint("massive"),
            "okx" => super::default_endpoint("okx-spot"),
            other => super::default_endpoint(other),
        }
        .into(),
        endpoints: BTreeMap::new(),
        credential_id: credential_id.into(),
        enabled: true,
        products: products.iter().map(|value| (*value).into()).collect(),
        purposes: vec!["reference-catalog".into()],
    }
}
use crate::services::storage::provider_sync_store::SqlxProviderSyncStore;

fn typed_market_id(value: &str) -> MarketId {
    MarketId::new(value).unwrap()
}

#[test]
fn reference_environment_uses_validated_integration_configuration() {
    let mut resolver = ReferenceCredentialResolver::default();
    assert_eq!(
        resolver.environment(None, "binance", "spot").unwrap(),
        "public"
    );
    assert!(
        resolver
            .environment(Some("missing"), "binance", "spot")
            .is_err()
    );
    let mut profile = reference_connection("configured", "binance", "fixture", &["spot"]);
    profile.environment = "testnet".into();
    resolver.insert_connection(profile.clone());
    assert_eq!(
        resolver
            .environment(Some("configured"), "binance", "spot")
            .unwrap(),
        "testnet"
    );
    assert!(
        resolver
            .environment(Some("configured"), "binance", "options")
            .is_err()
    );
    profile.enabled = false;
    resolver.insert_connection(profile);
    assert!(
        resolver
            .environment(Some("configured"), "binance", "spot")
            .is_err()
    );
}

#[tokio::test]
async fn massive_activation_and_new_option_scopes_preserve_private_environment() {
    for product in ["equity", "options"] {
        let directory = tempfile::tempdir().unwrap();
        let store = SqlxProviderSyncStore::open(directory.path().join("reference.sqlite"))
            .await
            .unwrap();
        let mut resolver = ReferenceCredentialResolver::default();
        resolver.insert_massive("fixture", "test-only-not-a-credential");
        let mut profile = reference_connection("private", "massive", "fixture", &[product]);
        profile.environment = "private".into();
        profile.endpoint = "https://private.example.test".into();
        resolver.insert_connection(profile);
        let options = product == "options";
        let definition = ReferenceSourceDefinition {
            source_id: kairos_primitives::reference::ReferenceSourceId::new(if options {
                "massive-options"
            } else {
                "massive-equity"
            })
            .unwrap(),
            provider_id: kairos_primitives::market::Provider::new("massive").unwrap(),
            scope: if options {
                SourceScope::underlying_instrument("instrument:equity:US:SPY:common")
            } else {
                SourceScope::global()
            },
            desired_state: SourceDesiredState::Enabled,
            connection_id: Some(crate::domain::SourceConnectionId::new("private").unwrap()),
            sync_policy: if options {
                SourceSyncPolicy::ScopedSnapshot
            } else {
                SourceSyncPolicy::PagedSnapshot
            },
        };
        let mut system = kairos_conflux::ConfluxSystem::new();
        let source = super::activate_runtime_source_definition(
            &definition,
            &mut system.connections(),
            &resolver,
            Some(store),
        )
        .await
        .unwrap()
        .unwrap();
        let mut connections = system.connections();
        let keys = connections.massive_rest.keys();
        assert_eq!(keys.len(), 1);
        assert_eq!(
            connections
                .massive_rest
                .get(&keys[0])
                .unwrap()
                .descriptor()
                .environment,
            "private"
        );
        if let ConfiguredProviderSource::MassiveOptions(source) = source {
            let (_, plan) = source.connection_plan("AAPL").unwrap();
            assert_eq!(plan.environment, "private");
            assert_eq!(plan.endpoint, "https://private.example.test");
        } else {
            assert!(!options);
        }
    }
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

struct NamedFailSource {
    id: &'static str,
}

struct FixedSource {
    id: &'static str,
    catalog: ProviderCatalog,
}

struct CountingSource {
    id: &'static str,
    catalog: ProviderCatalog,
    calls: Arc<AtomicUsize>,
}

struct DelayedCountingSource {
    id: &'static str,
    catalog: ProviderCatalog,
    calls: Arc<AtomicUsize>,
    delay: Duration,
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
    DelayedCounting(DelayedCountingSource),
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
test_source_from!(DelayedCountingSource, DelayedCounting);
test_source_from!(BarrierSource, Barrier);

#[async_trait::async_trait(?Send)]
impl ReferenceSource for TestProviderSource {
    fn source_id(&self) -> &str {
        match self {
            Self::Flaky(source) => source.source_id(),
            Self::RefreshingPaged(source) => source.source_id(),
            Self::Paged(source) => source.source_id(),
            Self::AlwaysFail(source) => source.source_id(),
            Self::Fixed(source) => source.source_id(),
            Self::Counting(source) => source.source_id(),
            Self::DelayedCounting(source) => source.source_id(),
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
            Self::DelayedCounting(source) => source.fetch_catalog().await,
            Self::Barrier(source) => source.fetch_catalog().await,
        }
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<super::SourceUpdate> {
        match self {
            Self::Flaky(source) => source.fetch_catalog_step().await,
            Self::RefreshingPaged(source) => source.fetch_catalog_step().await,
            Self::Paged(source) => source.fetch_catalog_step().await,
            Self::AlwaysFail(source) => source.fetch_catalog_step().await,
            Self::Fixed(source) => source.fetch_catalog_step().await,
            Self::Counting(source) => source.fetch_catalog_step().await,
            Self::DelayedCounting(source) => source.fetch_catalog_step().await,
            Self::Barrier(source) => source.fetch_catalog_step().await,
        }
    }
}

#[async_trait::async_trait(?Send)]
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
            exchanges: vec![Exchange {
                exchange_id: ExchangeId::new("exchange:a").unwrap(),
                name: "Provider A".into(),
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        },
    };
    let mut fan_in = ProviderFanInSource::new_with_sync_store(
        vec![TestProviderSource::from(source)],
        Some(store),
    )
    .await
    .unwrap();

    assert!(fan_in.normalized_facts_authoritative());
    assert_eq!(
        fan_in.fetch_catalog().await.unwrap(),
        ProviderCatalog::default()
    );
    let mut reopened = SqlxProviderSyncStore::open(&path).await.unwrap();
    assert!(!reopened.has_last_good("provider-a").await.unwrap());
    assert_eq!(
        fan_in
            .staged_source_changes()
            .completed_scans
            .iter()
            .map(|id| id.as_str())
            .collect::<Vec<_>>(),
        vec!["provider-a"]
    );
    assert!(
        reopened
            .load_last_good("provider-a")
            .await
            .unwrap()
            .is_none()
    );
}

#[tokio::test]
async fn normalized_commit_acknowledges_only_selected_sources() {
    let directory = tempfile::tempdir().unwrap();
    let store = SqlxProviderSyncStore::open(directory.path().join("reference.sqlite"))
        .await
        .unwrap();
    let mut fan_in = ProviderFanInSource::new_with_sync_store(
        vec![
            FixedSource {
                id: "provider-a",
                catalog: ProviderCatalog::default(),
            },
            FixedSource {
                id: "provider-b",
                catalog: ProviderCatalog::default(),
            },
        ],
        Some(store),
    )
    .await
    .unwrap();
    let mut system = kairos_conflux::ConfluxSystem::new();
    fan_in
        .advance_workflow_with_budget(
            &mut system.connections(),
            SourceTickBudget {
                max_sources_per_tick: 2,
                ..Default::default()
            },
        )
        .await
        .unwrap();
    let committed = crate::services::sources::SourceChanges {
        completed_scans: [
            kairos_primitives::reference::ReferenceSourceId::new("provider-a").unwrap(),
        ]
        .into_iter()
        .collect(),
        ..Default::default()
    };
    fan_in.mark_sources_committed(&committed);
    assert!(!fan_in.staged_source_changes().affects_source("provider-a"));
    assert!(fan_in.staged_source_changes().affects_source("provider-b"));
    let health = fan_in.source_health();
    assert_eq!(
        health
            .iter()
            .find(|source| source.source_id == "provider-a")
            .unwrap()
            .status,
        SourceRuntimePhase::Ready
    );
    assert_eq!(
        health
            .iter()
            .find(|source| source.source_id == "provider-b")
            .unwrap()
            .status,
        SourceRuntimePhase::Promoting
    );
    // Repeated acknowledgements cannot clear a peer's outstanding scan.
    fan_in.mark_sources_committed(&committed);
    assert!(fan_in.staged_source_changes().affects_source("provider-b"));
}

#[tokio::test]
async fn normalized_fan_in_round_robins_source_tick_budget() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let first_calls = Arc::new(AtomicUsize::new(0));
    let second_calls = Arc::new(AtomicUsize::new(0));
    let fan_in = ProviderFanInSource::new_with_sync_store(
        vec![
            TestProviderSource::from(CountingSource {
                id: "provider-a",
                catalog: ProviderCatalog {
                    exchanges: vec![Exchange {
                        exchange_id: ExchangeId::new("exchange:a").unwrap(),
                        name: "Provider A".into(),
                        status: "active".into(),
                        ..Default::default()
                    }],
                    ..Default::default()
                },
                calls: Arc::clone(&first_calls),
            }),
            TestProviderSource::from(DelayedCountingSource {
                id: "provider-b",
                catalog: ProviderCatalog {
                    exchanges: vec![Exchange {
                        exchange_id: ExchangeId::new("exchange:b").unwrap(),
                        name: "Provider B".into(),
                        status: "active".into(),
                        ..Default::default()
                    }],
                    ..Default::default()
                },
                calls: Arc::clone(&second_calls),
                delay: Duration::from_millis(10),
            }),
        ],
        Some(store),
    )
    .await
    .unwrap();
    let catalog_store = SqlxCatalogStore::open(&path).await.unwrap();
    let mut actor = ReferenceActor::new_test("reference-test", fan_in, catalog_store)
        .await
        .unwrap();
    let budget = SourceTickBudget {
        max_sources_per_tick: 1,
        ..SourceTickBudget::default()
    };
    let mut system = kairos_conflux::ConfluxSystem::new();

    let first = actor
        .advance_sources_with_connections(&mut system.connections(), budget)
        .await
        .unwrap_err();
    assert!(first.is_sync_in_progress());
    assert_eq!(first_calls.load(Ordering::SeqCst), 1);
    assert_eq!(second_calls.load(Ordering::SeqCst), 0);

    actor
        .advance_sources_with_connections(&mut system.connections(), budget)
        .await
        .unwrap();
    assert_eq!(first_calls.load(Ordering::SeqCst), 1);
    assert_eq!(second_calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn normalized_fan_in_keeps_concurrent_results_after_soft_wall_clock_budget() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let first_calls = Arc::new(AtomicUsize::new(0));
    let second_calls = Arc::new(AtomicUsize::new(0));
    let fan_in = ProviderFanInSource::new_with_sync_store(
        vec![
            TestProviderSource::from(DelayedCountingSource {
                id: "provider-a",
                catalog: ProviderCatalog {
                    exchanges: vec![Exchange {
                        exchange_id: ExchangeId::new("exchange:a").unwrap(),
                        name: "Provider A".into(),
                        status: "active".into(),
                        ..Default::default()
                    }],
                    ..Default::default()
                },
                calls: Arc::clone(&first_calls),
                delay: Duration::from_millis(10),
            }),
            TestProviderSource::from(DelayedCountingSource {
                id: "provider-b",
                catalog: ProviderCatalog {
                    exchanges: vec![Exchange {
                        exchange_id: ExchangeId::new("exchange:b").unwrap(),
                        name: "Provider B".into(),
                        status: "active".into(),
                        ..Default::default()
                    }],
                    ..Default::default()
                },
                calls: Arc::clone(&second_calls),
                delay: Duration::from_millis(10),
            }),
        ],
        Some(store),
    )
    .await
    .unwrap();
    let catalog_store = SqlxCatalogStore::open(&path).await.unwrap();
    let mut actor = ReferenceActor::new_test("reference-test", fan_in, catalog_store)
        .await
        .unwrap();
    let budget = SourceTickBudget {
        max_sources_per_tick: 2,
        max_wall_clock_millis: Some(1),
        ..SourceTickBudget::default()
    };
    let mut system = kairos_conflux::ConfluxSystem::new();

    actor
        .advance_sources_with_connections(&mut system.connections(), budget)
        .await
        .unwrap();
    assert_eq!(first_calls.load(Ordering::SeqCst), 1);
    assert_eq!(second_calls.load(Ordering::SeqCst), 1);
    let reader = kairos_reference_contract::ReferenceCatalog::open(&path).unwrap();
    assert!(reader.exchange("exchange:a").unwrap().is_some());
    assert!(reader.exchange("exchange:b").unwrap().is_some());
}

#[tokio::test]
async fn normalized_source_timeout_does_not_discard_or_skip_healthy_peer() {
    assert_normalized_timeout_isolated(true).await;
    assert_normalized_timeout_isolated(false).await;
}

async fn assert_normalized_timeout_isolated(slow_first: bool) {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let slow_calls = Arc::new(AtomicUsize::new(0));
    let healthy_calls = Arc::new(AtomicUsize::new(0));
    let mut sources = vec![
        TestProviderSource::from(DelayedCountingSource {
            id: "slow-source",
            catalog: ProviderCatalog::default(),
            calls: Arc::clone(&slow_calls),
            delay: super::fan_in::PROVIDER_FETCH_TIMEOUT + Duration::from_secs(1),
        }),
        TestProviderSource::from(CountingSource {
            id: "healthy-source",
            catalog: ProviderCatalog {
                exchanges: vec![Exchange {
                    exchange_id: ExchangeId::new("exchange:healthy").unwrap(),
                    name: "Healthy source".into(),
                    status: "active".into(),
                    ..Default::default()
                }],
                ..Default::default()
            },
            calls: Arc::clone(&healthy_calls),
        }),
    ];
    if !slow_first {
        sources.reverse();
    }
    let source = ProviderFanInSource::new_with_sync_store(sources, Some(store))
        .await
        .unwrap();
    let catalog_store = SqlxCatalogStore::open(&path).await.unwrap();
    let mut actor = ReferenceActor::new_test("reference-test", source, catalog_store)
        .await
        .unwrap();
    let mut system = kairos_conflux::ConfluxSystem::new();
    let mut connections = system.connections();
    {
        let refresh = actor.advance_sources_with_connections(
            &mut connections,
            SourceTickBudget {
                max_sources_per_tick: 2,
                max_wall_clock_millis: None,
                ..Default::default()
            },
        );
        tokio::pin!(refresh);
        tokio::time::pause();
        tokio::select! {
            biased;
            result = &mut refresh => panic!("slow request should be pending: {result:?}"),
            _ = std::future::ready(()) => {}
        }
        assert_eq!(slow_calls.load(Ordering::SeqCst), 1);
        assert_eq!(
            healthy_calls.load(Ordering::SeqCst),
            1,
            "healthy source must start before the slow source times out"
        );
        tokio::time::advance(super::fan_in::PROVIDER_FETCH_TIMEOUT).await;
        // Resume before SQLite I/O so virtual time cannot expire database pool timers.
        tokio::time::resume();
        assert!(refresh.await.is_err());
    }
    assert_eq!(healthy_calls.load(Ordering::SeqCst), 1);
    let health = actor.source_health();
    let failed = health
        .iter()
        .find(|entry| entry.source_id.as_str() == "slow-source")
        .unwrap();
    assert_eq!(failed.consecutive_failures, 1);
    assert!(failed.retry_after_unix_nanos.is_some());
    assert!(
        failed
            .last_error
            .as_ref()
            .unwrap()
            .message
            .contains("timed out")
    );
    let reader = kairos_reference_contract::ReferenceCatalog::open(&path).unwrap();
    assert!(reader.exchange("exchange:healthy").unwrap().is_some());
    let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
    assert!(store.has_last_good("healthy-source").await.unwrap());
    assert!(!store.has_last_good("slow-source").await.unwrap());
}

#[tokio::test]
async fn configured_public_sources_step_refresh_uses_managed_connections() {
    let key = || kairos_conflux::ConnectionKey::new("missing-reference-test").unwrap();
    let mut sources = vec![
        ConfiguredProviderSource::BinanceSpot(BinanceSpotSource::from_key(key())),
        ConfiguredProviderSource::BinanceDerivatives(
            super::BinanceDerivativesSource::from_usdm_key(key()),
        ),
        ConfiguredProviderSource::BinanceOptions(super::BinanceOptionsSource::from_key(key())),
        ConfiguredProviderSource::Okx(OkxSource::from_key("okx-spot", OkxProduct::Spot, key())),
        ConfiguredProviderSource::Hyperliquid(HyperliquidSource::from_key(
            HyperliquidProduct::Spot,
            key(),
        )),
    ];
    let mut system = kairos_conflux::ConfluxSystem::new();

    for source in &mut sources {
        let source_id = source.source_id().to_owned();
        let result = source
            .fetch_catalog_step_with_connections(&mut system.connections())
            .await;
        let error = match result {
            Ok(_) => panic!("{source_id} unexpectedly fetched from a missing test connection"),
            Err(error) => error.to_string(),
        };

        assert!(
            !error.contains("requires Conflux-managed connections"),
            "{source_id} step refresh fell back to the non-connection default: {error}"
        );
    }
}

#[tokio::test]
async fn actor_commits_normalized_composite_facts_without_catalog_materialization() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let mut provider_store = SqlxProviderSyncStore::open(&path).await.unwrap();
    provider_store
        .upsert_source_definition(ReferenceSourceDefinition {
            source_id: kairos_primitives::reference::ReferenceSourceId::new("massive-equity")
                .unwrap(),
            provider_id: kairos_primitives::market::Provider::new("massive").unwrap(),
            scope: SourceScope::provider_catalog(),
            desired_state: SourceDesiredState::Enabled,
            connection_id: None,
            sync_policy: SourceSyncPolicy::PagedSnapshot,
        })
        .await
        .unwrap();
    let source = FixedSource {
        id: "massive-equity",
        catalog: ProviderCatalog {
            exchanges: vec![Exchange {
                exchange_id: ExchangeId::new("exchange:a").unwrap(),
                name: "Provider A".into(),
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        },
    };
    let fan_in = ProviderFanInSource::new_with_sync_store(
        vec![TestProviderSource::from(source)],
        Some(provider_store),
    )
    .await
    .unwrap();
    let catalog_store = SqlxCatalogStore::open(&path).await.unwrap();
    let mut actor = ReferenceActor::new_test("reference-test", fan_in, catalog_store)
        .await
        .unwrap();

    let result = actor.refresh().await.unwrap();
    assert!(result.changed);
    assert_eq!(result.generation.get(), 1);
    assert_eq!(result.event_sequence.get(), 2);
    assert_eq!(result.event_count, 2);
    let reader = kairos_reference_contract::ReferenceCatalog::open(&path).unwrap();
    assert!(reader.exchange("exchange:a").unwrap().is_some());
    let publications = actor.pending_publications(10).await.unwrap();
    assert_eq!(publications.len(), 2);
    assert!(matches!(
        kairos_reference_contract::decode_event(&publications[1].payload).unwrap(),
        kairos_reference_contract::ReferenceEvent::CoverageStateChanged(_)
    ));
}

#[tokio::test]
async fn actor_retries_completed_candidate_without_refetching_staging() {
    assert_actor_conflict_isolation(false).await;
    assert_actor_conflict_isolation(true).await;
}

async fn assert_actor_conflict_isolation(reverse_sources: bool) {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let calls = Arc::new(AtomicUsize::new(0));
    let healthy_calls = Arc::new(AtomicUsize::new(0));
    let mut sources: Vec<_> = [("provider-a", "active"), ("provider-b", "inactive")]
        .into_iter()
        .map(|(id, status)| CountingSource {
            id,
            catalog: ProviderCatalog {
                exchanges: vec![Exchange {
                    exchange_id: ExchangeId::new("exchange:conflict").unwrap(),
                    name: "Shared exchange".into(),
                    status: status.into(),
                    ..Default::default()
                }],
                ..Default::default()
            },
            calls: Arc::clone(&calls),
        })
        .collect();
    if reverse_sources {
        sources.reverse();
    }
    sources.push(CountingSource {
        id: "provider-healthy",
        catalog: ProviderCatalog {
            exchanges: vec![Exchange {
                exchange_id: ExchangeId::new("exchange:healthy").unwrap(),
                name: "Healthy exchange".into(),
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        },
        calls: Arc::clone(&healthy_calls),
    });
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let fan_in = ProviderFanInSource::new_with_sync_store(sources, Some(store))
        .await
        .unwrap();
    let catalog_store = SqlxCatalogStore::open(&path).await.unwrap();
    let mut actor = ReferenceActor::new_test("reference-test", fan_in, catalog_store)
        .await
        .unwrap();
    let mut system = kairos_conflux::ConfluxSystem::new();
    let budget = SourceTickBudget {
        max_sources_per_tick: 2,
        ..Default::default()
    };
    let first_error = actor
        .advance_sources_with_connections(&mut system.connections(), budget)
        .await
        .unwrap_err()
        .to_string();
    assert!(
        first_error.contains("canonical exchange conflict"),
        "{first_error}"
    );
    assert_eq!(calls.load(Ordering::SeqCst), 2);
    assert_eq!(healthy_calls.load(Ordering::SeqCst), 0);
    let retry_error = actor
        .advance_sources_with_connections(&mut system.connections(), budget)
        .await
        .unwrap_err()
        .to_string();
    assert!(
        retry_error.contains("canonical exchange conflict"),
        "{retry_error}"
    );
    assert_eq!(calls.load(Ordering::SeqCst), 2);
    assert_eq!(healthy_calls.load(Ordering::SeqCst), 1);
    let health = actor.source_health();
    for id in ["provider-a", "provider-b"] {
        let source = health.iter().find(|source| source.source_id == id).unwrap();
        assert_eq!(source.status, SourceRuntimePhase::Unavailable);
        assert!(source.last_error.is_some());
    }
    assert_eq!(
        health
            .iter()
            .find(|source| source.source_id == "provider-healthy")
            .unwrap()
            .status,
        SourceRuntimePhase::Ready
    );
    let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
    assert!(!store.has_last_good("provider-a").await.unwrap());
    assert!(!store.has_last_good("provider-b").await.unwrap());
    assert!(store.has_last_good("provider-healthy").await.unwrap());
    let reader = kairos_reference_contract::ReferenceCatalog::open(&path).unwrap();
    assert!(reader.exchange("exchange:healthy").unwrap().is_some());
    assert!(reader.exchange("exchange:conflict").unwrap().is_none());
    assert_eq!(
        store.staged_pages("provider-a").await.unwrap()[0].exchanges[0]
            .status
            .as_str(),
        "active"
    );
    assert_eq!(
        store.staged_pages("provider-b").await.unwrap()[0].exchanges[0]
            .status
            .as_str(),
        "inactive"
    );
    actor
        .set_source_desired_state("provider-b", SourceDesiredState::Removed)
        .await
        .unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 2);
    assert!(store.has_last_good("provider-a").await.unwrap());
    assert!(!store.has_last_good("provider-b").await.unwrap());
    assert!(store.staged_pages("provider-b").await.unwrap().is_empty());
    assert!(reader.exchange("exchange:conflict").unwrap().is_some());
    assert_eq!(
        actor
            .source_health()
            .iter()
            .find(|source| source.source_id == "provider-a")
            .unwrap()
            .status,
        SourceRuntimePhase::Ready
    );
}

#[tokio::test]
async fn failed_peer_does_not_separate_committed_source_facts_from_catalog() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let provider_store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let source = FixedSource {
        id: "healthy-source",
        catalog: ProviderCatalog {
            exchanges: vec![Exchange {
                exchange_id: ExchangeId::new("exchange:healthy").unwrap(),
                name: "Healthy source".into(),
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        },
    };
    let fan_in = ProviderFanInSource::new_with_sync_store(
        vec![
            TestProviderSource::from(source),
            TestProviderSource::from(AlwaysFailSource),
        ],
        Some(provider_store),
    )
    .await
    .unwrap();
    let catalog_store = SqlxCatalogStore::open(&path).await.unwrap();
    let mut actor = ReferenceActor::new_test("reference-test", fan_in, catalog_store)
        .await
        .unwrap();

    assert!(actor.refresh().await.is_err());
    let reader = kairos_reference_contract::ReferenceCatalog::open(&path).unwrap();
    assert!(reader.exchange("exchange:healthy").unwrap().is_some());
    let mut provider_store = SqlxProviderSyncStore::open(&path).await.unwrap();
    assert!(
        provider_store
            .load_last_good("healthy-source")
            .await
            .unwrap()
            .is_some()
    );
}

#[tokio::test]
async fn actor_commits_last_known_good_failure_as_stale_coverage_event() {
    assert_last_known_good_transition(false, false).await;
}

#[tokio::test]
async fn actor_preserves_empty_completed_source_as_last_known_good() {
    assert_last_known_good_transition(true, false).await;
}

#[tokio::test]
async fn actor_removes_source_facts_and_coverage_proof_in_one_commit() {
    assert_last_known_good_transition(false, true).await;
    assert_last_known_good_transition(true, true).await;
}

struct RemovedSource;

#[async_trait::async_trait(?Send)]
impl ReferenceSource for RemovedSource {
    fn source_id(&self) -> &str {
        "massive-equity"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        panic!("a completed removal must commit without fetching again")
    }

    fn staged_source_changes(&self) -> crate::services::sources::SourceChanges {
        crate::services::sources::SourceChanges {
            removed_scans: [
                kairos_primitives::reference::ReferenceSourceId::new("massive-equity").unwrap(),
            ]
            .into_iter()
            .collect(),
            ..Default::default()
        }
    }
}

async fn assert_last_known_good_transition(empty: bool, removed: bool) {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let mut provider_store = SqlxProviderSyncStore::open(&path).await.unwrap();
    provider_store
        .upsert_source_definition(ReferenceSourceDefinition {
            source_id: kairos_primitives::reference::ReferenceSourceId::new("massive-equity")
                .unwrap(),
            provider_id: kairos_primitives::market::Provider::new("massive").unwrap(),
            scope: SourceScope::provider_catalog(),
            desired_state: SourceDesiredState::Enabled,
            connection_id: None,
            sync_policy: SourceSyncPolicy::PagedSnapshot,
        })
        .await
        .unwrap();
    let source = FixedSource {
        id: "massive-equity",
        catalog: if empty {
            ProviderCatalog::default()
        } else {
            ProviderCatalog {
                exchanges: vec![Exchange {
                    exchange_id: ExchangeId::new("exchange:a").unwrap(),
                    name: "Provider A".into(),
                    status: "active".into(),
                    ..Default::default()
                }],
                ..Default::default()
            }
        },
    };
    let fan_in = ProviderFanInSource::new_with_sync_store(vec![source], Some(provider_store))
        .await
        .unwrap();
    let catalog_store = SqlxCatalogStore::open(&path).await.unwrap();
    let mut actor = ReferenceActor::new_test("reference-test", fan_in, catalog_store)
        .await
        .unwrap();
    actor.refresh().await.unwrap();
    drop(actor);

    let mut provider_store = SqlxProviderSyncStore::open(&path).await.unwrap();
    assert!(
        provider_store
            .has_last_good("massive-equity")
            .await
            .unwrap()
    );
    let fan_in = ProviderFanInSource::new_with_sync_store(
        vec![NamedFailSource {
            id: "massive-equity",
        }],
        Some(provider_store),
    )
    .await
    .unwrap();
    let catalog_store = SqlxCatalogStore::open(&path).await.unwrap();
    let mut actor = if removed {
        ReferenceActor::new_test("reference-test", RemovedSource, catalog_store)
            .await
            .unwrap()
    } else {
        ReferenceActor::new_test("reference-test", fan_in, catalog_store)
            .await
            .unwrap()
    };

    let result = actor.refresh().await.unwrap();

    assert!(result.changed);
    assert_eq!(actor.metadata.generation.get(), 2);
    assert_eq!(
        actor.metadata.event_sequence.get(),
        if empty {
            2
        } else if removed {
            4
        } else {
            3
        }
    );
    assert_eq!(actor.metadata.stale_coverage_count, usize::from(!removed));
    let publications = actor.pending_publications(10).await.unwrap();
    let event =
        kairos_reference_contract::decode_event(&publications.last().unwrap().payload).unwrap();
    let kairos_reference_contract::ReferenceEvent::CoverageStateChanged(event) = event else {
        panic!("last publication must be a coverage transition");
    };
    assert_eq!(
        event.coverage().state(),
        if removed {
            kairos_protocol::generated::kairos::reference::v_3::CoverageState::NOT_CONFIGURED
        } else {
            kairos_protocol::generated::kairos::reference::v_3::CoverageState::STALE
        }
    );
    if removed {
        let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
        assert!(!store.has_last_good("massive-equity").await.unwrap());
        let payload: String = sqlx::query_scalar(
            "SELECT payload FROM reference_coverage_current WHERE source_id='massive-equity'",
        )
        .fetch_one(&store.pool)
        .await
        .unwrap();
        let coverage: kairos_reference_contract::ReferenceCoverage =
            serde_json::from_str(&payload).unwrap();
        assert_eq!(
            coverage.state,
            kairos_reference_contract::CoverageState::NotConfigured
        );
        assert_eq!(
            coverage.completeness,
            kairos_reference_contract::CoverageCompleteness::Unknown
        );
        assert!(!coverage.has_last_known_good);
        assert_eq!(coverage.generation, Some(actor.metadata.generation));
        assert_eq!(coverage.event_sequence, Some(actor.metadata.event_sequence));
        let remaining: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM reference_provider_records WHERE provider='massive-equity'",
        )
        .fetch_one(&store.pool)
        .await
        .unwrap();
        assert_eq!(remaining, 0);
        let reader = kairos_reference_contract::ReferenceCatalog::open(&path).unwrap();
        if empty {
            assert!(reader.exchange("exchange:a").unwrap().is_none());
        } else {
            // Retain historical identity without claiming an active source assertion.
            assert_eq!(
                reader.exchange("exchange:a").unwrap().unwrap().status,
                kairos_primitives::reference::ReferenceStatus::Inactive
            );
        }
    }
}

#[async_trait::async_trait(?Send)]
impl ReferenceSource for PagedSource {
    fn source_id(&self) -> &str {
        "test-paged"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        Ok(ProviderCatalog::default())
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<super::SourceUpdate> {
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
        Ok(super::SourceUpdate {
            catalog: ProviderCatalog {
                markets,
                ..Default::default()
            },
            complete: self.calls >= 2,
            page_count: 1,
            staged_changes: None,
            ..Default::default()
        })
    }
}

#[async_trait::async_trait(?Send)]
impl ReferenceSource for RefreshingPagedSource {
    fn source_id(&self) -> &str {
        "test-refreshing-paged"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        Ok(ProviderCatalog::default())
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<super::SourceUpdate> {
        self.calls += 1;
        let market_id = if self.calls == 1 {
            "market:complete-old"
        } else {
            "market:complete-new"
        };
        Ok(super::SourceUpdate {
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
            staged_changes: None,
            ..Default::default()
        })
    }
}

#[async_trait::async_trait(?Send)]
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

#[async_trait::async_trait(?Send)]
impl ReferenceSource for NamedFailSource {
    fn source_id(&self) -> &str {
        self.id
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        Err(crate::domain::ReferenceError::Provider(
            "named test provider unavailable".into(),
        ))
    }
}

#[async_trait::async_trait(?Send)]
impl ReferenceSource for FixedSource {
    fn source_id(&self) -> &str {
        self.id
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        Ok(self.catalog.clone())
    }
}

#[async_trait::async_trait(?Send)]
impl ReferenceSource for CountingSource {
    fn source_id(&self) -> &str {
        self.id
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        Ok(self.catalog.clone())
    }
}

#[async_trait::async_trait(?Send)]
impl ReferenceSource for DelayedCountingSource {
    fn source_id(&self) -> &str {
        self.id
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        tokio::time::sleep(self.delay).await;
        Ok(self.catalog.clone())
    }
}

#[async_trait::async_trait(?Send)]
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
        venues: Vec::new(),
        participant: ParticipantRef::new(ParticipantKind::Exchange, "okx").unwrap(),
        instruments: vec![
            ExternalInstrument {
                source_symbol: ExternalSymbol::new("BTC-USDT").unwrap(),
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
                source_symbol: ExternalSymbol::new("BTC-USDT-SWAP").unwrap(),
                source_venue: None,
                kind: ExternalInstrumentKind::Perpetual,
                base_currency: None,
                quote_currency: None,
                settlement_currency: Some(Currency::new("USDT").unwrap()),
                underlying: Some(ExternalSymbol::new("BTC-USDT").unwrap()),
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
    assert!(
        catalog
            .instruments
            .iter()
            .any(|value| value.instrument_id == "instrument:spot:BTC")
    );
    assert!(
        catalog
            .instruments
            .iter()
            .any(|value| value.instrument_id == "instrument:perpetual:BTC-USDT")
    );
    assert!(
        catalog
            .markets
            .iter()
            .any(|value| value.market_id == "market:okx:perpetual:BTC-USDT-SWAP")
    );
    assert!(
        catalog
            .markets
            .iter()
            .all(|value| value.exchange_id == "exchange:okx")
    );
}

#[test]
fn spot_listing_expiry_does_not_split_or_mutate_the_canonical_instrument() {
    let first_expiry = kairos_primitives::time::UnixNanos::new(1_786_694_400_000_000_000);
    let second_expiry = kairos_primitives::time::UnixNanos::new(1_786_953_600_000_000_000);
    let spot = |symbol: &str, quote: &str, expiry| ExternalInstrument {
        source_symbol: ExternalSymbol::new(symbol).unwrap(),
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
        venues: Vec::new(),
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
        venues: Vec::new(),
        participant: ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
        instruments: vec![
            ExternalInstrument {
                source_symbol: ExternalSymbol::new("BTCUSDT").unwrap(),
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
                source_symbol: ExternalSymbol::new("BTC-260821-50000-C").unwrap(),
                source_venue: None,
                kind: ExternalInstrumentKind::Option,
                base_currency: Some(Currency::new("BTC").unwrap()),
                quote_currency: Some(Currency::new("USDT").unwrap()),
                settlement_currency: Some(Currency::new("USDT").unwrap()),
                underlying: Some(ExternalSymbol::new("BTCUSDT").unwrap()),
                expiry_unix_nanos: Some(kairos_primitives::time::UnixNanos::new(
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
            venues: Vec::new(),
            participant: ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            instruments: vec![option],
        },
        BinanceProduct::Option,
    )
    .unwrap();
    assert!(
        spot_catalog
            .instruments
            .iter()
            .any(|value| value.instrument_id == "instrument:spot:BTC")
    );
    assert!(option_catalog.instruments.iter().any(|value| {
        value.instrument_id == "instrument:option:BTC-USDT:20260528:50000:C"
            && value.underlying_instrument_id.as_deref() == Some("instrument:spot:BTC")
    }));
    assert!(spot_catalog.markets.iter().any(|value| {
        value.market_id == "market:binance:spot:BTCUSDT"
            && value
                .minimum_notional
                .map(|value| value.to_string())
                .as_deref()
                == Some("10")
    }));
}

#[test]
fn binance_derivative_facts_keep_product_selection_but_not_canonical_identity() {
    let expiry = kairos_primitives::time::UnixNanos::new(1_782_432_000_000_000_000);
    let catalog = binance_provider_catalog(
        ExternalInstrumentCatalog {
            venues: Vec::new(),
            participant: ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            instruments: vec![ExternalInstrument {
                source_symbol: ExternalSymbol::new("BTCUSDT_260626").unwrap(),
                source_venue: None,
                kind: ExternalInstrumentKind::Future,
                base_currency: Some(Currency::new("BTC").unwrap()),
                quote_currency: Some(Currency::new("USDT").unwrap()),
                settlement_currency: Some(Currency::new("USDT").unwrap()),
                underlying: Some(ExternalSymbol::new("BTCUSDT").unwrap()),
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
            venues: Vec::new(),
            participant: ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            instruments: vec![ExternalInstrument {
                source_symbol: ExternalSymbol::new("AAPLUSDT").unwrap(),
                source_venue: None,
                kind: ExternalInstrumentKind::EquityPerpetual,
                base_currency: Some(Currency::new("AAPL").unwrap()),
                quote_currency: Some(Currency::new("USDT").unwrap()),
                settlement_currency: Some(Currency::new("USDT").unwrap()),
                underlying: Some(ExternalSymbol::new("AAPL").unwrap()),
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
fn binance_equity_service_catalog_does_not_invent_exchange_listing_or_market() {
    let catalog = binance_equity_provider_catalog(ExternalInstrumentCatalog {
        venues: Vec::new(),
        participant: ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
        instruments: vec![ExternalInstrument {
            source_symbol: ExternalSymbol::new("AAPL").unwrap(),
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
    assert!(catalog.exchanges.is_empty());
    assert!(catalog.listings.is_empty());
    assert!(catalog.markets.is_empty());
}

#[test]
fn massive_provider_facts_receive_canonical_identity_only_in_reference() {
    let expiry = kairos_primitives::time::UnixNanos::new(1_800_000_000_000_000_000);
    let catalog = massive_provider_catalog(ExternalInstrumentCatalog {
        venues: Vec::new(),
        participant: ParticipantRef::new(ParticipantKind::DataProvider, "massive").unwrap(),
        instruments: vec![ExternalInstrument {
            source_symbol: ExternalSymbol::new("O:SPY260821C00500000").unwrap(),
            source_venue: Some("BATO".into()),
            kind: ExternalInstrumentKind::Option,
            base_currency: None,
            quote_currency: Some(Currency::new("USD").unwrap()),
            settlement_currency: None,
            underlying: Some(ExternalSymbol::new("SPY").unwrap()),
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
    assert!(catalog.exchanges.iter().any(|value| {
        value.exchange_id == "exchange:cboe-bzx-options"
            && value.name == "Cboe BZX Options Exchange"
    }));
    assert!(
        catalog
            .instruments
            .iter()
            .any(|value| { value.instrument_id == "instrument:equity:US:SPY:common" })
    );
    assert!(catalog.instruments.iter().any(|value| {
        value.instrument_id == "instrument:option:SPY:20270115:500:C"
            && value.underlying_instrument_id.as_deref() == Some("instrument:equity:US:SPY:common")
    }));
    assert!(catalog.listings.iter().any(|value| {
        value.listing_id == "listing:cboe-bzx-options:option:SPY-20270115-500-C"
            && value.exchange_id == "exchange:cboe-bzx-options"
    }));
    assert!(catalog.markets.iter().any(|value| {
        value.market_id == "market:cboe-bzx-options:option:O:SPY260821C00500000"
            && value.listing_id.as_deref()
                == Some("listing:cboe-bzx-options:option:SPY-20270115-500-C")
            && value.instrument_kind == InstrumentKind::Option
            && value.venue_symbol.as_deref() == Some("O:SPY260821C00500000")
            && value.quote_asset_id.as_deref() == Some("asset:fiat:USD")
    }));
}

#[test]
fn massive_external_exchange_facts_create_reference_owned_venue_mappings() {
    let catalog = massive_provider_catalog(ExternalInstrumentCatalog {
        participant: ParticipantRef::new(ParticipantKind::DataProvider, "massive").unwrap(),
        instruments: Vec::new(),
        venues: vec![ExternalVenue {
            provider_identifier: "19".into(),
            participant_identifier: Some("Y".into()),
            mic: Some("BATY".into()),
            operating_mic: Some("XCBO".into()),
            name: "Cboe BYX".into(),
            kind: ExternalVenueKind::Exchange,
            active: true,
        }],
    })
    .unwrap();

    assert_eq!(catalog.venues[0].venue_id.as_str(), "venue:baty");
    assert_eq!(catalog.venues[0].mic.as_ref().unwrap().as_str(), "BATY");
    assert_eq!(catalog.venue_identifier_mappings.len(), 2);
    assert!(catalog.venue_identifier_mappings.iter().any(|mapping| {
        mapping.identifier == "19" && mapping.venue_id.as_str() == "venue:baty"
    }));
}

#[test]
fn massive_same_ticker_on_distinct_primary_venues_has_distinct_listings() {
    let equity = |venue: &str| ExternalInstrument {
        source_symbol: ExternalSymbol::new("BCPC").unwrap(),
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
        venues: Vec::new(),
        participant: ParticipantRef::new(ParticipantKind::DataProvider, "massive").unwrap(),
        instruments: vec![equity("XNAS"), equity("XNYS")],
    })
    .unwrap();

    assert_eq!(catalog.listings.len(), 2);
    assert_eq!(catalog.markets.len(), 2);
    let ids = catalog
        .listings
        .iter()
        .map(|listing| listing.listing_id.as_str())
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(ids.len(), 2);
    assert!(ids.contains("listing:nasdaq:equity:BCPC"));
    assert!(ids.contains("listing:nyse:equity:BCPC"));
    assert!(ids.iter().all(|id| !id.starts_with("listing:exchange:")));
    let market_ids = catalog
        .markets
        .iter()
        .map(|market| market.market_id.as_str())
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(market_ids.len(), 2);
    assert!(market_ids.contains("market:nasdaq:equity:BCPC:USD"));
    assert!(market_ids.contains("market:nyse:equity:BCPC:USD"));
    assert!(
        market_ids
            .iter()
            .all(|id| !id.starts_with("market:exchange:"))
    );
    assert!(catalog.markets.iter().all(|market| {
        market.instrument_kind == InstrumentKind::Equity
            && market.listing_id.is_some()
            && market.asset_type == Some(AssetClass::Equity)
    }));
}

#[test]
fn massive_equity_venues_have_specific_exchange_names() {
    let equity = |symbol: &str, venue: &str| ExternalInstrument {
        source_symbol: ExternalSymbol::new(symbol).unwrap(),
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
        venues: Vec::new(),
        participant: ParticipantRef::new(ParticipantKind::DataProvider, "massive").unwrap(),
        instruments: vec![equity("AAA", "ARCX"), equity("BBB", "BATS")],
    })
    .unwrap();

    assert!(catalog.exchanges.iter().any(|exchange| {
        exchange.exchange_id == "exchange:arcx" && exchange.name == "NYSE Arca"
    }));
    assert!(catalog.exchanges.iter().any(|exchange| {
        exchange.exchange_id == "exchange:bats" && exchange.name == "Cboe BZX Exchange"
    }));
    assert!(
        catalog
            .exchanges
            .iter()
            .all(|exchange| exchange.name != "Exchange")
    );
}

#[test]
fn hyperliquid_provider_facts_receive_canonical_identity_only_in_reference() {
    let catalog = hyperliquid_provider_catalog(
        ExternalInstrumentCatalog {
            venues: Vec::new(),
            participant: ParticipantRef::new(ParticipantKind::Exchange, "hyperliquid").unwrap(),
            instruments: vec![ExternalInstrument {
                source_symbol: ExternalSymbol::new("BTC").unwrap(),
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
fn hyperliquid_spot_and_perpetual_have_distinct_reference_markets() {
    let catalog = hyperliquid_provider_catalog(
        ExternalInstrumentCatalog {
            venues: Vec::new(),
            participant: ParticipantRef::new(ParticipantKind::Exchange, "hyperliquid").unwrap(),
            instruments: vec![ExternalInstrument {
                source_symbol: ExternalSymbol::new("PURR/USDC").unwrap(),
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
    let mut source = ProviderFanInSource::new(vec![TestProviderSource::from(FlakySource {
        calls: Arc::clone(&calls),
    })])
    .await
    .unwrap();
    let first = source.fetch_catalog().await.unwrap();
    let second = source.fetch_catalog().await.unwrap();
    assert_eq!(first.markets, second.markets);
    assert_eq!(calls.load(Ordering::SeqCst), 2);
    assert_eq!(
        source.source_health()[0].status,
        SourceRuntimePhase::Degraded
    );
    assert!(source.source_health()[0].stale);
}

#[tokio::test]
async fn targeted_refresh_does_not_poll_unrelated_provider() {
    let peer_calls = Arc::new(AtomicUsize::new(0));
    let mut source = ProviderFanInSource::new(vec![
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
    assert!(
        catalog
            .markets
            .iter()
            .any(|market| market.market_id == "market:page-2")
    );
}

#[tokio::test]
async fn paused_provider_keeps_its_snapshot_without_polling_peer() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let paused_calls = Arc::new(AtomicUsize::new(0));
    let store = SqlxProviderSyncStore::open_legacy(&path).await.unwrap();
    let mut source = ProviderFanInSource::new_with_sync_store(
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
        .set_source_desired_state("massive-options", SourceDesiredState::Paused)
        .await
        .unwrap();
    let catalog = source.fetch_catalog().await.unwrap();

    assert_eq!(paused_calls.load(Ordering::SeqCst), 1);
    assert_eq!(catalog.markets.len(), 2);
    assert_eq!(
        source
            .source_health()
            .into_iter()
            .find(|health| health.source_id == "massive-options")
            .unwrap()
            .status,
        SourceRuntimePhase::Paused
    );
}

#[tokio::test]
async fn provider_last_known_good_survives_composite_restart() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    {
        let store = SqlxProviderSyncStore::open_legacy(&path).await.unwrap();
        let calls = Arc::new(AtomicUsize::new(0));
        let mut source = ProviderFanInSource::new_with_sync_store(
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
    let mut restarted = ProviderFanInSource::new_with_sync_store(
        vec![TestProviderSource::from(AlwaysFailSource)],
        Some(store),
    )
    .await
    .unwrap();
    let catalog = restarted.fetch_catalog().await.unwrap();
    assert_eq!(catalog.markets.len(), 1);
    assert_eq!(
        restarted.source_health()[0].status,
        SourceRuntimePhase::Degraded
    );
}

#[tokio::test]
async fn dynamic_source_definition_is_visible_in_source_health() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let mut source = ProviderFanInSource::new_with_sync_store(
        vec![TestProviderSource::from(FixedSource {
            id: "provider-a",
            catalog: ProviderCatalog::default(),
        })],
        Some(store),
    )
    .await
    .unwrap();

    source
        .upsert_source_definition(ReferenceSourceDefinition {
            source_id: kairos_primitives::reference::ReferenceSourceId::new("massive-options")
                .unwrap(),
            provider_id: kairos_primitives::market::Provider::new("massive").unwrap(),
            scope: SourceScope::underlying_instrument("instrument:equity:US:SPY:common"),
            desired_state: SourceDesiredState::Paused,
            connection_id: Some(crate::domain::SourceConnectionId::new("massive-main").unwrap()),
            sync_policy: SourceSyncPolicy::ScopedSnapshot,
        })
        .await
        .unwrap();

    let health = source.source_health();
    let dynamic = health
        .iter()
        .find(|value| value.source_id == "massive-options")
        .expect("dynamic source definition appears in provider health");
    assert_eq!(dynamic.status, SourceRuntimePhase::Paused);
    let definition = dynamic
        .definition
        .as_ref()
        .expect("dynamic source keeps its resolved definition");
    assert_eq!(definition.source_id.as_str(), "massive-options");
    assert_eq!(definition.provider_id.as_str(), "massive");
    let mut reopened = SqlxProviderSyncStore::open(&path).await.unwrap();
    let definitions = reopened.source_definitions().await.unwrap();
    assert!(
        definitions
            .iter()
            .any(|value| value.source_id == "provider-a")
    );
    assert!(
        definitions
            .iter()
            .any(|value| value.source_id == "massive-options")
    );
}

#[tokio::test]
async fn built_in_source_is_seeded_once_and_persisted_definition_wins_on_restart() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let mut source = ProviderFanInSource::new_with_sync_store(
        vec![TestProviderSource::from(FixedSource {
            id: "provider-a",
            catalog: ProviderCatalog::default(),
        })],
        Some(store),
    )
    .await
    .unwrap();
    source
        .upsert_source_definition(ReferenceSourceDefinition {
            source_id: kairos_primitives::reference::ReferenceSourceId::new("provider-a").unwrap(),
            provider_id: kairos_primitives::market::Provider::new("provider-a").unwrap(),
            scope: SourceScope::provider_catalog(),
            desired_state: SourceDesiredState::Paused,
            connection_id: Some(crate::domain::SourceConnectionId::new("provider-a-main").unwrap()),
            sync_policy: SourceSyncPolicy::ManualCurated,
        })
        .await
        .unwrap();
    drop(source);

    let restarted = ProviderFanInSource::new_with_sync_store(
        vec![TestProviderSource::from(FixedSource {
            id: "provider-a",
            catalog: ProviderCatalog::default(),
        })],
        Some(SqlxProviderSyncStore::open(&path).await.unwrap()),
    )
    .await
    .unwrap();
    let definition = restarted.source_health()[0].definition.clone().unwrap();
    assert_eq!(definition.scope, SourceScope::provider_catalog());
    assert_eq!(definition.desired_state, SourceDesiredState::Paused);
    assert_eq!(definition.sync_policy, SourceSyncPolicy::ManualCurated);
    assert_eq!(
        definition.connection_id.unwrap().as_str(),
        "provider-a-main"
    );
}

#[tokio::test]
async fn enabled_registry_only_source_is_reported_as_registered() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let mut source = ProviderFanInSource::new_with_sync_store(
        vec![TestProviderSource::from(FixedSource {
            id: "provider-a",
            catalog: ProviderCatalog::default(),
        })],
        Some(store),
    )
    .await
    .unwrap();

    source
        .upsert_source_definition(ReferenceSourceDefinition {
            source_id: kairos_primitives::reference::ReferenceSourceId::new("massive-options")
                .unwrap(),
            provider_id: kairos_primitives::market::Provider::new("massive").unwrap(),
            scope: SourceScope::underlying_instrument("instrument:equity:US:SPY:common"),
            desired_state: SourceDesiredState::Enabled,
            connection_id: Some(crate::domain::SourceConnectionId::new("massive-main").unwrap()),
            sync_policy: SourceSyncPolicy::ScopedSnapshot,
        })
        .await
        .unwrap();

    let health = source.source_health();
    let dynamic = health
        .iter()
        .find(|value| value.source_id == "massive-options")
        .expect("dynamic source definition appears in provider health");
    assert_eq!(dynamic.status, SourceRuntimePhase::Registered);
    assert!(dynamic.last_error.is_none());
    assert!(dynamic.definition.as_ref().unwrap().sync_policy == SourceSyncPolicy::ScopedSnapshot);
}

#[tokio::test]
async fn dynamic_scoped_massive_options_definition_activates_runtime_adapter() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let key = kairos_conflux::ConnectionKey::new("missing-reference-test").unwrap();
    let mut resolver = ReferenceCredentialResolver::default();
    resolver.insert_massive("massive-main", "test-massive-api-key");
    resolver.insert_connection(reference_connection(
        "massive-main",
        "massive",
        "massive-main",
        &["options"],
    ));
    let mut source = ProviderFanInSource::new_with_sync_store_and_credentials(
        vec![ConfiguredProviderSource::BinanceSpot(
            BinanceSpotSource::from_key(key),
        )],
        Some(store),
        resolver,
    )
    .await
    .unwrap();
    let mut system = kairos_conflux::ConfluxSystem::new();

    source
        .upsert_source_definition_with_connections(
            ReferenceSourceDefinition {
                source_id: kairos_primitives::reference::ReferenceSourceId::new("massive-options")
                    .unwrap(),
                provider_id: kairos_primitives::market::Provider::new("massive").unwrap(),
                scope: SourceScope::underlying_instrument("instrument:equity:US:SPY:common"),
                desired_state: SourceDesiredState::Enabled,
                connection_id: Some(
                    crate::domain::SourceConnectionId::new("massive-main").unwrap(),
                ),
                sync_policy: SourceSyncPolicy::ScopedSnapshot,
            },
            &mut system.connections(),
        )
        .await
        .unwrap();

    assert_eq!(source.option_underlyings(), vec!["SPY"]);
    assert!(
        system
            .connections()
            .massive_rest
            .keys()
            .iter()
            .any(|key| key.as_str() == "reference-massive-options-spy")
    );
    let health = source.source_health();
    let dynamic = health
        .iter()
        .find(|value| value.source_id == "massive-options")
        .expect("dynamic scoped source appears in source health");
    assert_eq!(dynamic.status, SourceRuntimePhase::Idle);

    source
        .upsert_source_definition_with_connections(
            ReferenceSourceDefinition {
                source_id: kairos_primitives::reference::ReferenceSourceId::new("massive-options")
                    .unwrap(),
                provider_id: kairos_primitives::market::Provider::new("massive").unwrap(),
                scope: SourceScope::underlying_instrument("instrument:equity:US:QQQ:common"),
                desired_state: SourceDesiredState::Enabled,
                connection_id: Some(
                    crate::domain::SourceConnectionId::new("massive-main").unwrap(),
                ),
                sync_policy: SourceSyncPolicy::ScopedSnapshot,
            },
            &mut system.connections(),
        )
        .await
        .unwrap();

    assert_eq!(source.option_underlyings(), vec!["QQQ", "SPY"]);
    assert!(
        system
            .connections()
            .massive_rest
            .keys()
            .iter()
            .any(|key| key.as_str() == "reference-massive-options-qqq")
    );

    source
        .set_source_desired_state_with_connections(
            "massive-options",
            SourceDesiredState::Removed,
            &mut system.connections(),
        )
        .await
        .unwrap();
    assert!(
        !system
            .connections()
            .massive_rest
            .keys()
            .iter()
            .any(|key| key.as_str() == "reference-massive-options-spy")
    );
    assert!(
        !system
            .connections()
            .massive_rest
            .keys()
            .iter()
            .any(|key| key.as_str() == "reference-massive-options-qqq")
    );
}

#[tokio::test]
async fn dynamic_runtime_source_definition_activates_public_adapter() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let key = kairos_conflux::ConnectionKey::new("missing-reference-test").unwrap();
    let mut source = ProviderFanInSource::new_with_sync_store(
        vec![ConfiguredProviderSource::BinanceSpot(
            BinanceSpotSource::from_key(key),
        )],
        Some(store),
    )
    .await
    .unwrap();
    let mut system = kairos_conflux::ConfluxSystem::new();

    source
        .upsert_source_definition_with_connections(
            ReferenceSourceDefinition {
                source_id: kairos_primitives::reference::ReferenceSourceId::new("hyperliquid-spot")
                    .unwrap(),
                provider_id: kairos_primitives::market::Provider::new("hyperliquid").unwrap(),
                scope: SourceScope::global(),
                desired_state: SourceDesiredState::Enabled,
                connection_id: None,
                sync_policy: SourceSyncPolicy::FullSnapshot,
            },
            &mut system.connections(),
        )
        .await
        .unwrap();

    let health = source.source_health();
    let dynamic = health
        .iter()
        .find(|value| value.source_id == "hyperliquid-spot")
        .expect("dynamic runtime source appears in provider health");
    assert_eq!(dynamic.status, SourceRuntimePhase::Idle);
    assert!(
        system
            .connections()
            .hyperliquid_info_rest
            .keys()
            .iter()
            .any(|key| key.as_str() == "reference-hyperliquid-spot")
    );

    source
        .set_source_desired_state_with_connections(
            "hyperliquid-spot",
            SourceDesiredState::Removed,
            &mut system.connections(),
        )
        .await
        .unwrap();

    assert!(
        !system
            .connections()
            .hyperliquid_info_rest
            .keys()
            .iter()
            .any(|key| key.as_str() == "reference-hyperliquid-spot")
    );
    let health = source.source_health();
    let dynamic = health
        .iter()
        .find(|value| value.source_id == "hyperliquid-spot")
        .expect("removed source definition remains observable");
    assert_eq!(dynamic.status, SourceRuntimePhase::Disabled);
}

#[tokio::test]
async fn dynamic_credentialed_source_definition_activates_runtime_adapter() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let key = kairos_conflux::ConnectionKey::new("missing-reference-test").unwrap();
    let mut resolver = ReferenceCredentialResolver::default();
    resolver.insert_binance(
        "binance-stocks",
        BinanceCredential {
            principal_id: "reference-stocks".to_owned(),
            api_key: secrecy::SecretString::new("test-api-key".to_owned().into()),
            secret: secrecy::SecretString::new("test-secret".to_owned().into()),
        },
    );
    resolver.insert_connection(reference_connection(
        "binance-stocks",
        "binance",
        "binance-stocks",
        &["equity"],
    ));
    let mut source = ProviderFanInSource::new_with_sync_store_and_credentials(
        vec![ConfiguredProviderSource::BinanceSpot(
            BinanceSpotSource::from_key(key),
        )],
        Some(store),
        resolver,
    )
    .await
    .unwrap();
    let mut system = kairos_conflux::ConfluxSystem::new();

    source
        .upsert_source_definition_with_connections(
            ReferenceSourceDefinition {
                source_id: kairos_primitives::reference::ReferenceSourceId::new("binance-equity")
                    .unwrap(),
                provider_id: kairos_primitives::market::Provider::new("binance").unwrap(),
                scope: SourceScope::global(),
                desired_state: SourceDesiredState::Enabled,
                connection_id: Some(
                    crate::domain::SourceConnectionId::new("binance-stocks").unwrap(),
                ),
                sync_policy: SourceSyncPolicy::FullSnapshot,
            },
            &mut system.connections(),
        )
        .await
        .unwrap();

    assert!(
        system
            .connections()
            .binance_stocks_rest
            .keys()
            .iter()
            .any(|key| key.as_str() == "reference-binance-stocks")
    );
    let health = source.source_health();
    let dynamic = health
        .iter()
        .find(|value| value.source_id == "binance-equity")
        .expect("dynamic credentialed source appears in source health");
    assert_eq!(dynamic.status, SourceRuntimePhase::Idle);
}

#[tokio::test]
async fn dynamic_massive_equity_source_definition_activates_runtime_adapter() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let key = kairos_conflux::ConnectionKey::new("missing-reference-test").unwrap();
    let mut resolver = ReferenceCredentialResolver::default();
    resolver.insert_massive("massive-main", "test-massive-api-key");
    resolver.insert_connection(reference_connection(
        "massive-main",
        "massive",
        "massive-main",
        &["equity"],
    ));
    let mut source = ProviderFanInSource::new_with_sync_store_and_credentials(
        vec![ConfiguredProviderSource::BinanceSpot(
            BinanceSpotSource::from_key(key),
        )],
        Some(store),
        resolver,
    )
    .await
    .unwrap();
    let mut system = kairos_conflux::ConfluxSystem::new();

    source
        .upsert_source_definition_with_connections(
            ReferenceSourceDefinition {
                source_id: kairos_primitives::reference::ReferenceSourceId::new("massive-equity")
                    .unwrap(),
                provider_id: kairos_primitives::market::Provider::new("massive").unwrap(),
                scope: SourceScope::global(),
                desired_state: SourceDesiredState::Enabled,
                connection_id: Some(
                    crate::domain::SourceConnectionId::new("massive-main").unwrap(),
                ),
                sync_policy: SourceSyncPolicy::PagedSnapshot,
            },
            &mut system.connections(),
        )
        .await
        .unwrap();

    assert!(
        system
            .connections()
            .massive_rest
            .keys()
            .iter()
            .any(|key| key.as_str() == "reference-massive-equity")
    );
    let health = source.source_health();
    let dynamic = health
        .iter()
        .find(|value| value.source_id == "massive-equity")
        .expect("dynamic Massive equity source appears in source health");
    assert_eq!(dynamic.status, SourceRuntimePhase::Idle);

    source
        .set_source_desired_state_with_connections(
            "massive-equity",
            SourceDesiredState::Removed,
            &mut system.connections(),
        )
        .await
        .unwrap();
    assert!(
        !system
            .connections()
            .massive_rest
            .keys()
            .iter()
            .any(|key| key.as_str() == "reference-massive-equity")
    );
}

#[tokio::test]
async fn dynamic_activation_error_is_visible_in_registered_source_health() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let key = kairos_conflux::ConnectionKey::new("missing-reference-test").unwrap();
    let mut source = ProviderFanInSource::new_with_sync_store_and_credentials(
        vec![ConfiguredProviderSource::BinanceSpot(
            BinanceSpotSource::from_key(key),
        )],
        Some(store),
        ReferenceCredentialResolver::default(),
    )
    .await
    .unwrap();
    let mut system = kairos_conflux::ConfluxSystem::new();

    let error = source
        .upsert_source_definition_with_connections(
            ReferenceSourceDefinition {
                source_id: kairos_primitives::reference::ReferenceSourceId::new("binance-equity")
                    .unwrap(),
                provider_id: kairos_primitives::market::Provider::new("binance").unwrap(),
                scope: SourceScope::global(),
                desired_state: SourceDesiredState::Enabled,
                connection_id: Some(
                    crate::domain::SourceConnectionId::new("binance-missing").unwrap(),
                ),
                sync_policy: SourceSyncPolicy::FullSnapshot,
            },
            &mut system.connections(),
        )
        .await
        .unwrap_err();

    assert!(
        error
            .to_string()
            .contains("Integration connection binance-missing is not configured")
    );
    let health = source.source_health();
    let dynamic = health
        .iter()
        .find(|value| value.source_id == "binance-equity")
        .expect("failed dynamic source remains observable");
    assert_eq!(dynamic.status, SourceRuntimePhase::Registered);
    assert_eq!(
        dynamic.last_error.as_ref().map(|error| error.code.as_str()),
        Some("reference.provider_failed")
    );
    assert!(
        dynamic
            .last_error
            .as_ref()
            .unwrap()
            .message
            .contains("Integration connection binance-missing is not configured")
    );
}

#[tokio::test]
async fn registered_source_targeted_refresh_reports_missing_adapter() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let mut source = ProviderFanInSource::new_with_sync_store(
        vec![TestProviderSource::from(FixedSource {
            id: "provider-a",
            catalog: ProviderCatalog::default(),
        })],
        Some(store),
    )
    .await
    .unwrap();

    source
        .upsert_source_definition(ReferenceSourceDefinition {
            source_id: kairos_primitives::reference::ReferenceSourceId::new("massive-options")
                .unwrap(),
            provider_id: kairos_primitives::market::Provider::new("massive").unwrap(),
            scope: SourceScope::underlying_instrument("instrument:equity:US:SPY:common"),
            desired_state: SourceDesiredState::Enabled,
            connection_id: Some(crate::domain::SourceConnectionId::new("massive-main").unwrap()),
            sync_policy: SourceSyncPolicy::ScopedSnapshot,
        })
        .await
        .unwrap();

    let error = source
        .advance_one_source("massive-options")
        .await
        .unwrap_err()
        .to_string();
    assert!(
        error.contains("registered but has no active runtime adapter"),
        "{error}"
    );
}

#[tokio::test]
async fn paused_registry_only_source_targeted_refresh_is_skipped() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let mut source = ProviderFanInSource::new_with_sync_store(
        vec![TestProviderSource::from(FixedSource {
            id: "provider-a",
            catalog: ProviderCatalog::default(),
        })],
        Some(store),
    )
    .await
    .unwrap();

    source
        .upsert_source_definition(ReferenceSourceDefinition {
            source_id: kairos_primitives::reference::ReferenceSourceId::new("massive-options")
                .unwrap(),
            provider_id: kairos_primitives::market::Provider::new("massive").unwrap(),
            scope: SourceScope::underlying_instrument("instrument:equity:US:SPY:common"),
            desired_state: SourceDesiredState::Paused,
            connection_id: Some(crate::domain::SourceConnectionId::new("massive-main").unwrap()),
            sync_policy: SourceSyncPolicy::ScopedSnapshot,
        })
        .await
        .unwrap();

    assert!(
        source
            .advance_one_source("massive-options")
            .await
            .unwrap()
            .is_none()
    );
}

#[tokio::test]
async fn disabled_registry_only_source_targeted_refresh_preserves_disabled_state() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let mut source = ProviderFanInSource::new_with_sync_store(
        vec![TestProviderSource::from(FixedSource {
            id: "provider-a",
            catalog: ProviderCatalog::default(),
        })],
        Some(store),
    )
    .await
    .unwrap();

    source
        .upsert_source_definition(ReferenceSourceDefinition {
            source_id: kairos_primitives::reference::ReferenceSourceId::new("massive-options")
                .unwrap(),
            provider_id: kairos_primitives::market::Provider::new("massive").unwrap(),
            scope: SourceScope::underlying_instrument("instrument:equity:US:SPY:common"),
            desired_state: SourceDesiredState::Disabled,
            connection_id: Some(crate::domain::SourceConnectionId::new("massive-main").unwrap()),
            sync_policy: SourceSyncPolicy::ScopedSnapshot,
        })
        .await
        .unwrap();

    assert!(
        source
            .advance_one_source("massive-options")
            .await
            .unwrap()
            .is_none()
    );
    let health = source.source_health();
    let dynamic = health
        .iter()
        .find(|value| value.source_id == "massive-options")
        .expect("dynamic source definition appears in provider health");
    assert_eq!(dynamic.status, SourceRuntimePhase::Disabled);
    assert_eq!(
        dynamic
            .definition
            .as_ref()
            .map(|definition| definition.desired_state),
        Some(SourceDesiredState::Disabled)
    );
}

#[tokio::test]
async fn configured_source_seed_preserves_durable_desired_state() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    {
        let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
        let mut definition = crate::services::providers::ReferenceSourceBinding::Massive(
            super::MassiveReferenceSource::Options,
        )
        .builtin_definition()
        .unwrap();
        definition.desired_state = SourceDesiredState::Paused;
        store.upsert_source_definition(definition).await.unwrap();
    }
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let source = ProviderFanInSource::new_with_sync_store(
        vec![TestProviderSource::from(FixedSource {
            id: "massive-options",
            catalog: ProviderCatalog::default(),
        })],
        Some(store),
    )
    .await
    .unwrap();

    let health = source.source_health();
    assert_eq!(health[0].source_id, "massive-options");
    assert_eq!(health[0].status, SourceRuntimePhase::Paused);
    assert_eq!(
        health[0]
            .definition
            .as_ref()
            .map(|definition| definition.desired_state),
        Some(SourceDesiredState::Paused)
    );

    let mut reopened = SqlxProviderSyncStore::open(&path).await.unwrap();
    let definitions = reopened.source_definitions().await.unwrap();
    assert_eq!(definitions.len(), 1);
    assert_eq!(definitions[0].source_id, "massive-options");
    assert_eq!(definitions[0].desired_state, SourceDesiredState::Paused);
}

#[tokio::test]
async fn provider_without_last_known_good_rejects_partial_refresh() {
    let mut source = ProviderFanInSource::new(vec![TestProviderSource::from(AlwaysFailSource)])
        .await
        .unwrap();
    let error = source.fetch_catalog().await.unwrap_err().to_string();
    assert!(error.contains("without a last-known-good snapshot"));
}

#[tokio::test]
async fn provider_failure_opens_circuit_and_applies_backoff() {
    let mut source = ProviderFanInSource::new(vec![TestProviderSource::from(AlwaysFailSource)])
        .await
        .unwrap();
    let _ = source.fetch_catalog().await;
    assert_eq!(source.source_health()[0].consecutive_failures, 1);
    let error = source.fetch_catalog().await.unwrap_err().to_string();
    assert!(error.contains("provider retry window is waiting"));
    assert_eq!(source.source_health()[0].consecutive_failures, 1);
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
    let mut source = ProviderFanInSource::new(vec![
        TestProviderSource::from(first),
        TestProviderSource::from(second),
    ])
    .await
    .unwrap();
    let error = source.fetch_catalog().await.unwrap_err().to_string();
    assert!(error.contains("canonical market conflict"));
    assert!(error.contains("market:shared"));
}

#[tokio::test]
async fn shared_canonical_instrument_aggregates_listing_availability() {
    let instrument = |status| Instrument {
        instrument_id: InstrumentId::new("instrument:spot:BTC").unwrap(),
        symbol: Symbol::new("BTC").unwrap(),
        instrument_type: InstrumentKind::Spot,
        primary_currency_asset_id: Some(
            kairos_primitives::reference::AssetId::new("asset:crypto:BTC").unwrap(),
        ),
        status,
        ..Instrument::default()
    };
    let mut source = ProviderFanInSource::new(vec![
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
            kairos_primitives::reference::AssetId::new("asset:crypto:BTC").unwrap(),
        ),
        status: "active".into(),
        ..Instrument::default()
    };
    let mut without_currency = instrument();
    without_currency.primary_currency_asset_id = None;
    let with_currency = instrument();
    let mut source = ProviderFanInSource::new(vec![
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
        asset_id: kairos_primitives::reference::AssetId::new("asset:equity:AVB").unwrap(),
        code: kairos_primitives::reference::Symbol::new("AVB").unwrap(),
        asset_class: AssetClass::Equity,
        status,
        ..Asset::default()
    };
    let mut source = ProviderFanInSource::new(vec![
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
            kairos_primitives::reference::AssetId::new("asset:crypto:BTC").unwrap(),
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
        Some(kairos_primitives::reference::AssetId::new("asset:crypto:USDT").unwrap());
    assert!(!provider_catalog_uses_current_canonical_shape(
        &ProviderCatalog {
            instruments: vec![legacy_quote_owned],
            ..ProviderCatalog::default()
        }
    ));

    let mut provider_owned = canonical;
    provider_owned.source_id =
        Some(kairos_primitives::reference::ReferenceSourceId::new("binance-spot").unwrap());
    assert!(!provider_catalog_uses_current_canonical_shape(
        &ProviderCatalog {
            instruments: vec![provider_owned],
            ..ProviderCatalog::default()
        }
    ));
}

#[tokio::test]
async fn normalized_fan_in_polls_independent_sources_concurrently() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("reference.sqlite");
    let store = SqlxProviderSyncStore::open(&path).await.unwrap();
    let barrier = Arc::new(tokio::sync::Barrier::new(2));
    let mut source = ProviderFanInSource::new_with_sync_store(
        vec![
            TestProviderSource::from(BarrierSource {
                id: "provider-a",
                barrier: Arc::clone(&barrier),
            }),
            TestProviderSource::from(BarrierSource {
                id: "provider-b",
                barrier,
            }),
        ],
        Some(store),
    )
    .await
    .unwrap();
    assert!(source.normalized_facts_authoritative());
    let mut system = kairos_conflux::ConfluxSystem::new();
    tokio::time::timeout(
        Duration::from_secs(5),
        source.advance_workflow_with_budget(
            &system.connections(),
            SourceTickBudget {
                max_sources_per_tick: 2,
                max_wall_clock_millis: None,
                ..Default::default()
            },
        ),
    )
    .await
    .expect("normalized source futures must reach the barrier together")
    .unwrap();
    let changes = source.staged_source_changes();
    assert_eq!(changes.completed_scans.len(), 2);
}

#[tokio::test]
async fn provider_fan_in_polls_sources_concurrently_on_caller_runtime() {
    let barrier = Arc::new(tokio::sync::Barrier::new(2));
    let mut source = ProviderFanInSource::new(vec![
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
    let key = kairos_conflux::ConnectionKey::new("reference-hyperliquid-perpetual").unwrap();
    let mut system = kairos_conflux::ConfluxSystem::new();
    system
        .connections()
        .hyperliquid_info_rest
        .create(
            key.clone(),
            kairos_conflux::HyperliquidRestConfig {
                environment: "public".into(),
                endpoint: format!("http://{address}/info"),
            },
        )
        .unwrap();
    let mut source = HyperliquidSource::from_key(HyperliquidProduct::Perpetual, key);

    let catalog = source
        .fetch_catalog_with_connections(&mut system.connections())
        .await
        .unwrap();
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
async fn shared_managed_catalog_connection_allows_overlapping_http_queries() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let key = kairos_conflux::ConnectionKey::new("shared-reference-binance").unwrap();
    let mut system = kairos_conflux::ConfluxSystem::new();
    system
        .connections()
        .binance_spot_rest
        .create(
            key.clone(),
            kairos_conflux::BinanceRestConfig {
                environment: "public".into(),
                endpoint: format!("http://{address}"),
                credential: None,
            },
        )
        .unwrap();
    let mut first = BinanceSpotSource::from_key(key.clone());
    let mut second = BinanceSpotSource::from_key(key);
    let connections = system.connections();
    let server = async {
        // No response is sent until both requests have reached the same endpoint.
        let mut streams = Vec::new();
        for _ in 0..2 {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = Vec::new();
            while !request.ends_with(b"\r\n\r\n") {
                assert!(request.len() < 8192);
                request.push(stream.read_u8().await.unwrap());
            }
            assert!(request.starts_with(b"GET /api/v3/exchangeInfo "));
            streams.push(stream);
        }
        let body = r#"{"symbols":[]}"#;
        let response = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        for mut stream in streams {
            stream.write_all(response.as_bytes()).await.unwrap();
            stream.shutdown().await.unwrap();
        }
    };
    tokio::time::timeout(Duration::from_secs(5), async {
        let ((), first, second) = tokio::join!(
            server,
            first.fetch_catalog_with_connections(&connections),
            second.fetch_catalog_with_connections(&connections),
        );
        assert!(first.unwrap().instruments.is_empty());
        assert!(second.unwrap().instruments.is_empty());
    })
    .await
    .expect("shared managed queries must overlap without cloning the connection");
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
    let key = kairos_conflux::ConnectionKey::new("reference-binance-spot").unwrap();
    let mut system = kairos_conflux::ConfluxSystem::new();
    system
        .connections()
        .binance_spot_rest
        .create(
            key.clone(),
            kairos_conflux::BinanceRestConfig {
                environment: "public".into(),
                endpoint: format!("http://{address}"),
                credential: None,
            },
        )
        .unwrap();
    let mut source = BinanceSpotSource::from_key(key);

    let catalog = source
        .fetch_catalog_with_connections(&mut system.connections())
        .await
        .unwrap();
    server.join().unwrap();

    assert_eq!(catalog.markets.len(), 1);
    assert_eq!(catalog.markets[0].market_id, "market:binance:spot:BTCUSDT");
    assert_eq!(
        catalog.markets[0].price_tick.map(|value| value.to_string()),
        Some("0.01".into())
    );
    assert_eq!(
        catalog.markets[0]
            .minimum_notional
            .map(|value| value.to_string()),
        Some("10".into())
    );
}

#[tokio::test]
async fn okx_async_capability_maps_through_reference_end_to_end() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 4096];
        let length = stream.read(&mut request).unwrap();
        assert!(
            String::from_utf8_lossy(&request[..length])
                .starts_with("GET /api/v5/public/instruments?instType=SWAP ")
        );
        let body = r#"{"code":"0","data":[{"instId":"BTC-USDT-SWAP","uly":"BTC-USDT","settleCcy":"USDT","state":"live","tickSz":"0.1","lotSz":"0.01","minSz":"0.01","ctVal":"0.01"}]}"#;
        write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
    });
    let key = kairos_conflux::ConnectionKey::new("reference-okx-swap").unwrap();
    let mut system = kairos_conflux::ConfluxSystem::new();
    system
        .connections()
        .okx_public_rest
        .create(
            key.clone(),
            kairos_conflux::OkxRestConfig {
                environment: "public".into(),
                endpoint: format!("http://{address}"),
            },
        )
        .unwrap();
    let mut source = OkxSource::from_key("okx-swap", OkxProduct::Swap, key);

    let catalog = source
        .fetch_catalog_with_connections(&mut system.connections())
        .await
        .unwrap();
    server.join().unwrap();

    assert_eq!(catalog.markets.len(), 1);
    assert_eq!(
        catalog.markets[0].market_id,
        "market:okx:perpetual:BTC-USDT-SWAP"
    );
    assert_eq!(
        catalog.markets[0].price_tick.map(|value| value.to_string()),
        Some("0.1".into())
    );
    assert_eq!(
        catalog.markets[0]
            .contract_size
            .map(|value| value.to_string()),
        Some("0.01".into())
    );
}

#[tokio::test]
async fn okx_margin_is_spot_identity_with_explicit_margin_access() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 4096];
        let length = stream.read(&mut request).unwrap();
        assert!(
            String::from_utf8_lossy(&request[..length])
                .starts_with("GET /api/v5/public/instruments?instType=MARGIN ")
        );
        let body = r#"{"code":"0","data":[{"instId":"BTC-USDT","baseCcy":"BTC","quoteCcy":"USDT","state":"live","tickSz":"0.1","lotSz":"0.001","minSz":"0.001"}]}"#;
        write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
    });
    let key = kairos_conflux::ConnectionKey::new("reference-okx-margin").unwrap();
    let mut system = kairos_conflux::ConfluxSystem::new();
    system
        .connections()
        .okx_public_rest
        .create(
            key.clone(),
            kairos_conflux::OkxRestConfig {
                environment: "public".into(),
                endpoint: format!("http://{address}"),
            },
        )
        .unwrap();
    let mut source = OkxSource::from_key("okx-margin", OkxProduct::Margin, key);

    let catalog = source
        .fetch_catalog_with_connections(&mut system.connections())
        .await
        .unwrap();
    server.join().unwrap();

    assert_eq!(catalog.instruments[0].instrument_id, "instrument:spot:BTC");
    assert_eq!(catalog.instruments[0].instrument_type, InstrumentKind::Spot);
    assert_eq!(catalog.markets[0].instrument_kind, InstrumentKind::Spot);
}

fn next_massive_ticker_request(listener: &TcpListener) -> (std::net::TcpStream, String) {
    loop {
        let (mut stream, _) = listener.accept().unwrap();
        let mut bytes = [0_u8; 4096];
        let length = stream.read(&mut bytes).unwrap();
        let request = String::from_utf8_lossy(&bytes[..length]).into_owned();
        if request.starts_with("GET /v3/reference/exchanges?") {
            let body = r#"{"results":[{"id":12,"name":"Nasdaq","mic":"XNAS","type":"exchange","asset_class":"stocks","locale":"us"}]}"#;
            write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
            continue;
        }
        assert!(request.starts_with("GET /v3/reference/tickers?"));
        return (stream, request);
    }
}

#[tokio::test]
async fn massive_persists_each_successful_page_before_a_later_page_fails() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        for page in 0..2 {
            let (mut stream, _) = next_massive_ticker_request(&listener);
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
    let (mut source, mut system) =
        MassiveEquitySource::new_with_sync_store("test-key", format!("http://{address}"), store)
            .await
            .unwrap();

    let first = source
        .fetch_catalog_step_with_connections(&mut system.connections())
        .await
        .unwrap();
    assert!(!first.complete);
    assert_eq!(first.page_count, 1);
    assert!(
        source
            .fetch_catalog_step_with_connections(&mut system.connections())
            .await
            .is_err()
    );
    server.join().unwrap();

    let mut reopened = SqlxProviderSyncStore::open_legacy(&path).await.unwrap();
    let (cursor, accumulated) = reopened
        .load_state("massive-equity")
        .await
        .unwrap()
        .unwrap();
    assert_eq!(cursor.as_deref(), Some("page-2"));
    assert!(accumulated.is_none());
    assert!(
        reopened
            .staged_pages("massive-equity")
            .await
            .unwrap()
            .into_iter()
            .flat_map(|catalog| catalog.instruments)
            .any(|instrument| instrument.symbol == "AAPL")
    );
}

#[tokio::test]
async fn massive_options_coverage_is_explicit_and_scoped_to_one_underlying() {
    assert_massive_option_scope(false).await;
}

#[tokio::test]
async fn massive_options_staged_results_identify_completed_and_removed_scope() {
    assert_massive_option_scope(true).await;
}

async fn assert_massive_option_scope(normalized: bool) {
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
    let store = if normalized {
        SqlxProviderSyncStore::open(&path).await.unwrap()
    } else {
        SqlxProviderSyncStore::open_legacy(&path).await.unwrap()
    };
    let (mut source, mut system) =
        MassiveOptionsCoverageSource::new("test-key", format!("http://{address}"), store)
            .await
            .unwrap();

    assert!(source.option_underlyings().is_empty());
    source
        .set_scope_with_connections(
            SourceScope::underlying_instrument("spy"),
            true,
            &mut system.connections(),
        )
        .await
        .unwrap();
    assert_eq!(source.option_underlyings(), vec!["SPY"]);
    let completed = source
        .fetch_catalog_step_with_connections(&mut system.connections())
        .await
        .unwrap();
    server.join().unwrap();
    assert!(completed.complete);
    let catalog = if normalized {
        let changes = completed.staged_changes.as_ref().unwrap();
        assert_eq!(
            changes
                .completed_scans
                .iter()
                .map(|id| id.as_str())
                .collect::<Vec<_>>(),
            vec!["massive-options:SPY"]
        );
        assert!(changes.removed_scans.is_empty());
        assert!(completed.catalog.instruments.is_empty());
        let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
        store
            .load_provider_candidate(&ProviderCatalog::default(), changes)
            .await
            .unwrap()
    } else {
        assert!(completed.staged_changes.is_none());
        completed.catalog.clone()
    };
    assert_eq!(completed.records_seen, Some(catalog.record_count() as u64));
    assert!(
        catalog
            .instruments
            .iter()
            .any(|instrument| instrument.instrument_id == "instrument:option:SPY:20260821:500:C")
    );
    assert!(
        !catalog
            .instruments
            .iter()
            .any(|instrument| instrument.instrument_id == "instrument:option:SPY:20260821:400:P")
    );
    assert!(catalog.listings.is_empty());
    assert!(catalog.markets.is_empty());

    source
        .set_scope_with_connections(
            SourceScope::underlying_instrument("SPY"),
            false,
            &mut system.connections(),
        )
        .await
        .unwrap();
    let removed = source
        .fetch_catalog_step_with_connections(&mut system.connections())
        .await
        .unwrap();
    assert!(removed.complete);
    if normalized {
        let changes = removed.staged_changes.as_ref().unwrap();
        assert!(changes.completed_scans.is_empty());
        assert_eq!(
            changes
                .removed_scans
                .iter()
                .map(|id| id.as_str())
                .collect::<Vec<_>>(),
            vec!["massive-options:SPY"]
        );
    }
    assert!(removed.catalog.instruments.is_empty());
    assert!(removed.catalog.markets.is_empty());
}

#[tokio::test]
async fn massive_options_scope_replacement_is_syncing_until_scope_complete() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        for page in 0..2 {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 4096];
            let length = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..length]);
            assert!(request.contains("underlying_ticker=SPY"));
            let next = if page == 0 {
                String::new()
            } else {
                format!(r#", "next_url":"http://{address}/v3/reference/tickers?cursor=page-2""#)
            };
            let strike = if page == 0 { 500 } else { 510 };
            let body = format!(
                r#"{{"results":[{{"ticker":"O:SPY260821C{strike:08}","underlying_ticker":"SPY","primary_exchange":"OPRA","expiration_date":"2026-08-21","strike_price":{strike},"contract_type":"call","active":true}}]{next}}}"#
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
    let (mut source, mut system) =
        MassiveOptionsCoverageSource::new("test-key", format!("http://{address}"), store)
            .await
            .unwrap();
    source
        .set_scope_with_connections(
            SourceScope::underlying_instrument("SPY"),
            true,
            &mut system.connections(),
        )
        .await
        .unwrap();
    let first = source
        .fetch_catalog_step_with_connections(&mut system.connections())
        .await
        .unwrap();
    assert!(first.complete);
    assert!(
        first
            .catalog
            .instruments
            .iter()
            .any(|instrument| instrument.instrument_id == "instrument:option:SPY:20260821:500:C")
    );
    drop(source);

    let store = SqlxProviderSyncStore::open_legacy(&path).await.unwrap();
    let (mut resumed, mut resumed_system) =
        MassiveOptionsCoverageSource::new("test-key", format!("http://{address}"), store)
            .await
            .unwrap();
    let replacement = resumed
        .fetch_catalog_step_with_connections(&mut resumed_system.connections())
        .await
        .unwrap();
    server.join().unwrap();

    assert!(!replacement.complete);
    assert!(replacement.records_seen.is_some_and(|count| count > 0));
    assert_eq!(
        replacement.work_item_id.as_deref(),
        Some("massive-options:SPY")
    );
    assert_eq!(
        replacement.scope_id.as_deref(),
        Some("instrument:equity:US:SPY:common")
    );
    assert_eq!(
        replacement.scope_kind.as_deref(),
        Some("underlying_instrument")
    );
    assert_eq!(replacement.cursor_present, Some(true));
    let mut store = SqlxProviderSyncStore::open_legacy(&path).await.unwrap();
    let last_good = store
        .load_last_good("massive-options:SPY")
        .await
        .unwrap()
        .expect("completed scope remains authoritative while replacement pages");
    assert!(
        last_good
            .instruments
            .iter()
            .any(|instrument| instrument.instrument_id == "instrument:option:SPY:20260821:500:C")
    );
}

#[tokio::test]
async fn massive_full_catalog_resumes_from_persisted_incremental_cursor() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        for page in 0..9 {
            let (mut stream, request) = next_massive_ticker_request(&listener);
            assert!(request.contains("limit=1000"));
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
    let (mut first, mut first_system) =
        MassiveEquitySource::new_with_sync_store("test-key", format!("http://{address}"), store)
            .await
            .unwrap();

    let partial = first
        .fetch_catalog_step_with_connections(&mut first_system.connections())
        .await
        .unwrap();
    assert!(!partial.complete);
    assert_eq!(partial.page_count, 1);
    assert_eq!(partial.pages_done, Some(1));
    assert!(partial.catalog.markets.is_empty());
    drop(first);

    let store = SqlxProviderSyncStore::open_legacy(&path).await.unwrap();
    let (mut resumed, mut resumed_system) =
        MassiveEquitySource::new_with_sync_store("test-key", format!("http://{address}"), store)
            .await
            .unwrap();
    for _ in 1..8 {
        let partial = resumed
            .fetch_catalog_step_with_connections(&mut resumed_system.connections())
            .await
            .unwrap();
        assert!(!partial.complete);
        assert_eq!(partial.page_count, 1);
        assert!(partial.pages_done.is_some_and(|pages_done| pages_done >= 1));
        assert!(partial.catalog.markets.is_empty());
    }
    let complete = resumed
        .fetch_catalog_step_with_connections(&mut resumed_system.connections())
        .await
        .unwrap();
    server.join().unwrap();

    assert!(complete.complete);
    assert_eq!(complete.page_count, 1);
    assert_eq!(complete.pages_done, Some(8));
    assert_eq!(complete.pages_total, Some(8));
    assert_eq!(
        complete
            .catalog
            .instruments
            .iter()
            .filter(|instrument| instrument.instrument_type == InstrumentKind::Equity)
            .count(),
        9
    );
    assert!(
        complete
            .catalog
            .instruments
            .iter()
            .any(|instrument| instrument.symbol == "TEST0")
    );
    assert!(
        complete
            .catalog
            .instruments
            .iter()
            .any(|instrument| instrument.symbol == "TEST8")
    );
}

#[tokio::test]
async fn massive_full_catalog_uses_tick_budget_for_page_batches() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        for page in 0..3 {
            let (mut stream, request) = next_massive_ticker_request(&listener);
            assert!(request.contains("limit=25"));
            if page > 0 {
                assert!(request.contains(&format!("cursor=page-{page}")));
            }
            let next = if page < 2 {
                format!(
                    r#", "next_url":"http://{address}/v3/reference/tickers?cursor=page-{}""#,
                    page + 1
                )
            } else {
                String::new()
            };
            let body = format!(
                r#"{{"results":[{{"ticker":"BUDGET{page}","primary_exchange":"XNAS","active":true}}]{next}}}"#
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
    let (mut source, mut system) =
        MassiveEquitySource::new_with_sync_store("test-key", format!("http://{address}"), store)
            .await
            .unwrap();
    let budget = SourceTickBudget {
        max_batches_per_source: 2,
        max_records_per_batch: Some(25),
        ..SourceTickBudget::default()
    };

    let partial = source
        .fetch_catalog_step_with_budget(&mut system.connections(), budget)
        .await
        .unwrap();
    assert!(!partial.complete);
    assert_eq!(partial.page_count, 2);
    assert_eq!(partial.pages_done, Some(2));
    assert_eq!(partial.pages_total, None);
    assert!(partial.catalog.markets.is_empty());

    let complete = source
        .fetch_catalog_step_with_budget(&mut system.connections(), budget)
        .await
        .unwrap();
    server.join().unwrap();

    assert!(complete.complete);
    assert_eq!(complete.page_count, 1);
    assert_eq!(complete.pages_done, Some(3));
    assert_eq!(complete.pages_total, Some(3));
    assert_eq!(
        complete
            .catalog
            .instruments
            .iter()
            .filter(|instrument| instrument.instrument_type == InstrumentKind::Equity)
            .count(),
        3
    );
}

#[tokio::test]
async fn partial_provider_pages_do_not_drop_previous_page() {
    let mut source =
        ProviderFanInSource::new(vec![TestProviderSource::from(PagedSource { calls: 0 })])
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
    assert_eq!(
        source.source_health()[0].status,
        SourceRuntimePhase::Scanning
    );
    let second = source.fetch_catalog().await.unwrap();
    assert_eq!(second.markets.len(), 2);
    assert!(
        second
            .markets
            .iter()
            .any(|market| market.market_id == "market:page-1")
    );
}

#[tokio::test]
async fn incomplete_provider_sync_does_not_replace_last_good_snapshot() {
    let mut source =
        ProviderFanInSource::new(vec![TestProviderSource::from(RefreshingPagedSource {
            calls: 0,
        })])
        .await
        .unwrap();
    let first = source.fetch_catalog().await.unwrap();
    assert_eq!(first.markets[0].market_id, "market:complete-old");
    let second = source.fetch_catalog().await.unwrap();
    assert_eq!(second.markets[0].market_id, "market:complete-old");
    assert_eq!(
        source.source_health()[0].status,
        SourceRuntimePhase::Scanning
    );
    assert!(!source.source_health()[0].stale);
    let third = source.fetch_catalog().await.unwrap();
    assert_eq!(third.markets[0].market_id, "market:complete-new");
    assert_eq!(source.source_health()[0].status, SourceRuntimePhase::Ready);
}
