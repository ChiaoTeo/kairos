//! Provider sources and in-memory implementations for the Reference actor.

use std::collections::BTreeMap;
use std::sync::mpsc::{self, Receiver, SyncSender, TrySendError};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use kairos_integration::application::capabilities::reference::{
    ExternalInstrument, ExternalInstrumentCatalog, ExternalInstrumentKind,
};
use kairos_integration::blocking::InstrumentCatalogConnection;
use kairos_integration::participants::binance::{
    BinanceConnection, BinanceConnectionConfig, BinancePrincipalConfig, BinanceQuotaAllocation,
    InstrumentType as BinanceInstrumentType,
};
use kairos_integration::participants::hyperliquid::{
    HyperliquidConnection, HyperliquidConnectionConfig,
};
use kairos_integration::participants::massive::{
    InstrumentQuery as MassiveInstrumentQuery, MassiveConnection, MassiveConnectionConfig,
};
use kairos_integration::participants::okx::{
    InstrumentType as OkxInstrumentType, OkxConnection, OkxConnectionConfig,
};

use super::store::ProviderSyncStore;
use crate::domain::{
    Asset, Entity, ExecutionAccess, Instrument, Listing, Market, ProviderCatalog, ProviderHealth,
    ReferenceError, ReferenceResult,
};

/// Internal provider seam. Provider selection belongs to composition and is
/// not part of the public application contract.
pub(crate) trait ReferenceSource: Send {
    fn source_id(&self) -> &str;
    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog>;

    fn fetch_catalog_step(&mut self) -> ReferenceResult<ProviderUpdate> {
        Ok(ProviderUpdate {
            catalog: self.fetch_catalog()?,
            complete: true,
            page_count: 1,
        })
    }

    fn provider_health(&self) -> Vec<ProviderHealth> {
        Vec::new()
    }
}

/// Adds workspace-declared participants to the provider fan-in without
/// creating another mutable catalog owner or another provider health entry.
pub(crate) struct ParticipantAugmentedSource {
    inner: Box<dyn ReferenceSource>,
    participants: Vec<Entity>,
}

impl ParticipantAugmentedSource {
    pub(crate) fn wrap(
        inner: Box<dyn ReferenceSource>,
        participants: Vec<Entity>,
    ) -> Box<dyn ReferenceSource> {
        Box::new(Self {
            inner,
            participants,
        })
    }
}

impl ReferenceSource for ParticipantAugmentedSource {
    fn source_id(&self) -> &str {
        self.inner.source_id()
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let mut catalog = self.inner.fetch_catalog()?;
        catalog.entities.extend(self.participants.iter().cloned());
        Ok(catalog)
    }

    fn fetch_catalog_step(&mut self) -> ReferenceResult<ProviderUpdate> {
        let mut update = self.inner.fetch_catalog_step()?;
        update
            .catalog
            .entities
            .extend(self.participants.iter().cloned());
        Ok(update)
    }

    fn provider_health(&self) -> Vec<ProviderHealth> {
        self.inner.provider_health()
    }
}

pub(crate) struct ProviderUpdate {
    pub catalog: ProviderCatalog,
    pub complete: bool,
    pub page_count: usize,
}

/// Binance Spot public reference source.
///
/// The provider connection and vendor normalization live in integration. This
/// source only maps the neutral integration payload into Reference-owned
/// domain records.
pub struct BinanceSpotSource {
    connection: kairos_integration::blocking::BinanceInstrumentCatalog,
}

pub struct BinanceOptionsSource {
    connection: kairos_integration::blocking::BinanceInstrumentCatalog,
}

pub struct BinanceDerivativesSource {
    id: &'static str,
    instrument_type: BinanceInstrumentType,
    connection: kairos_integration::blocking::BinanceInstrumentCatalog,
}

pub struct BinanceEquitySource {
    connection: kairos_integration::blocking::BinanceEquityInstrumentCatalog,
}

pub struct MassiveSource {
    connection: kairos_integration::blocking::MassiveInstrumentCatalog,
    cursor: Option<String>,
    accumulated: Option<ProviderCatalog>,
    sync_store: Option<Box<dyn ProviderSyncStore>>,
}

pub struct MassiveEquitySource {
    connection: kairos_integration::blocking::MassiveInstrumentCatalog,
    cursor: Option<String>,
    accumulated: Option<ProviderCatalog>,
    sync_store: Option<Box<dyn ProviderSyncStore>>,
}

pub struct HyperliquidSource {
    connection: kairos_integration::blocking::HyperliquidInstrumentCatalog,
}

pub struct OkxSource {
    id: String,
    connection: kairos_integration::blocking::OkxInstrumentCatalog,
}

/// Reference-owned fan-in for the global catalog. Each provider remains an
/// independent integration connection; this source only merges normalized
/// records before they enter the Reference actor.
pub struct CompositeSource {
    workers: Vec<ProviderWorker>,
    last_good: BTreeMap<String, ProviderCatalog>,
    health: BTreeMap<String, ProviderHealth>,
    sync_store: Option<Box<dyn ProviderSyncStore>>,
}

struct ProviderWorker {
    source_id: String,
    requests: SyncSender<ProviderRequest>,
    retry_after: Option<Instant>,
}

struct ProviderRequest {
    response: SyncSender<ReferenceResult<ProviderUpdate>>,
}

// Each provider refresh advances a bounded page batch. Large providers persist
// their cursor and candidate catalog between refreshes, while only completed
// candidates are promoted to last-known-good.
const PROVIDER_FETCH_TIMEOUT: Duration = Duration::from_secs(150);
const MASSIVE_PAGES_PER_REFRESH: usize = 8;

impl CompositeSource {
    #[cfg(test)]
    pub fn new(sources: Vec<Box<dyn ReferenceSource>>) -> ReferenceResult<Self> {
        Self::new_with_sync_store(sources, None)
    }

    pub fn new_with_sync_store(
        sources: Vec<Box<dyn ReferenceSource>>,
        mut sync_store: Option<Box<dyn ProviderSyncStore>>,
    ) -> ReferenceResult<Self> {
        if sources.is_empty() {
            return Err(ReferenceError::Provider(
                "reference catalog has no sources".into(),
            ));
        }
        let mut last_good = BTreeMap::new();
        for source in &sources {
            if let Some(store) = sync_store.as_mut() {
                if let Some(catalog) = store.load_last_good(source.source_id())? {
                    last_good.insert(source.source_id().to_owned(), catalog);
                }
            }
        }
        let workers = sources
            .into_iter()
            .map(|mut source| {
                let source_id = source.source_id().to_owned();
                let (requests, receiver): (SyncSender<ProviderRequest>, Receiver<ProviderRequest>) =
                    mpsc::sync_channel(1);
                let worker_id = source_id.clone();
                std::thread::Builder::new()
                    .name(format!("reference-provider-{worker_id}"))
                    .spawn(move || {
                        while let Ok(request) = receiver.recv() {
                            // A provider step is deliberately bounded. Sources with a large
                            // catalog persist their cursor and accumulated candidate, then make
                            // progress on the next refresh instead of holding the coordinator
                            // until the entire universe has been downloaded.
                            let result = source.fetch_catalog_step();
                            let _ = request.response.send(result);
                        }
                    })
                    .map_err(|error| {
                        ReferenceError::Provider(format!(
                            "start provider worker {source_id}: {error}"
                        ))
                    })?;
                Ok(ProviderWorker {
                    source_id,
                    requests,
                    retry_after: None,
                })
            })
            .collect::<ReferenceResult<Vec<_>>>()?;
        Ok(Self {
            workers,
            last_good,
            health: BTreeMap::new(),
            sync_store,
        })
    }
}

impl ReferenceSource for CompositeSource {
    fn source_id(&self) -> &str {
        if self.workers.len() == 1 {
            &self.workers[0].source_id
        } else {
            "reference-default"
        }
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        // Provider REST calls are independent persistent workers. Send all
        // requests first, then collect them against one common deadline. A
        // slow provider cannot hold the coordinator forever.
        let started = Instant::now();
        let mut failures = Vec::new();
        let mut failed_workers = Vec::new();
        let mut requests = Vec::new();
        for worker in &self.workers {
            if worker
                .retry_after
                .is_some_and(|until| until > Instant::now())
            {
                failures.push(format!("{}: provider circuit is open", worker.source_id));
                continue;
            }
            let health = self
                .health
                .entry(worker.source_id.clone())
                .or_insert_with(|| ProviderHealth {
                    source_id: worker.source_id.clone(),
                    status: "unknown".into(),
                    last_attempt_unix_nanos: None,
                    last_success_unix_nanos: None,
                    consecutive_failures: 0,
                    stale: false,
                });
            health.last_attempt_unix_nanos = Some(unix_nanos().into());
            let (response, receiver) = mpsc::sync_channel(1);
            match worker.requests.try_send(ProviderRequest { response }) {
                Ok(()) => requests.push((worker.source_id.clone(), receiver, Instant::now())),
                Err(TrySendError::Full(_)) => {
                    failures.push(format!("{}: provider worker is busy", worker.source_id));
                    failed_workers.push((worker.source_id.clone(), "busy"));
                }
                Err(TrySendError::Disconnected(_)) => {
                    failures.push(format!(
                        "{}: provider worker is unavailable",
                        worker.source_id
                    ));
                    failed_workers.push((worker.source_id.clone(), "failed"));
                }
            }
        }
        for (source_id, status) in failed_workers {
            self.mark_failure(&source_id, status);
        }
        let mut entities = BTreeMap::new();
        let mut assets = BTreeMap::new();
        let mut instruments = BTreeMap::new();
        let mut listings = BTreeMap::new();
        let mut markets = BTreeMap::new();
        let mut financial_products = BTreeMap::new();
        let mut execution_accesses = BTreeMap::new();
        let mut conflicts = 0usize;
        let mut successful_sources = 0usize;
        let mut unavailable_without_last_good = Vec::new();
        for (source_id, receiver, provider_started) in requests {
            let remaining = PROVIDER_FETCH_TIMEOUT.saturating_sub(started.elapsed());
            let result = match receiver.recv_timeout(remaining) {
                Ok(result) => result,
                Err(error) => Err(ReferenceError::Provider(format!(
                    "provider fetch timed out: {error}"
                ))),
            };
            tracing::info!(
                event = "reference_provider_fetch_completed",
                component = "reference",
                provider = %source_id,
                duration_ms = provider_started.elapsed().as_millis() as u64,
                success = result.is_ok(),
                complete = result.as_ref().map(|update| update.complete).unwrap_or(false),
                page_count = result.as_ref().map(|update| update.page_count).unwrap_or(0),
                "reference provider fetch completed"
            );
            let catalog = match result {
                Ok(update) => {
                    if !update.complete {
                        self.mark_syncing(&source_id);
                        tracing::info!(
                            event = "reference_provider_sync_in_progress",
                            component = "reference",
                            provider = %source_id,
                            page_count = update.page_count,
                            fallback = self.last_good.contains_key(&source_id),
                            "reference provider sync is continuing from its persisted cursor"
                        );
                        let Some(catalog) = self.last_good.get(&source_id) else {
                            unavailable_without_last_good.push(source_id.clone());
                            continue;
                        };
                        catalog
                    } else {
                        successful_sources += 1;
                        self.mark_success(&source_id);
                        let mut catalog = update.catalog;
                        tag_catalog_source(&mut catalog, &source_id);
                        self.last_good.insert(source_id.clone(), catalog);
                        if let Some(store) = self.sync_store.as_mut() {
                            store.save_last_good(
                                &source_id,
                                self.last_good
                                    .get(&source_id)
                                    .expect("inserted provider snapshot"),
                            )?;
                        }
                        self.last_good
                            .get(&source_id)
                            .expect("inserted provider snapshot")
                    }
                }
                Err(error) => {
                    failures.push(format!("{source_id}: {error}"));
                    self.mark_failure(&source_id, "stale");
                    let Some(catalog) = self.last_good.get(&source_id) else {
                        unavailable_without_last_good.push(source_id.clone());
                        continue;
                    };
                    catalog
                }
            };
            for value in &catalog.entities {
                if entities
                    .insert(value.entity_id.clone(), value.clone())
                    .is_some_and(|previous| previous != *value)
                {
                    conflicts += 1;
                }
            }
            for value in &catalog.assets {
                if assets
                    .insert(value.asset_id.clone(), value.clone())
                    .is_some_and(|previous| previous != *value)
                {
                    conflicts += 1;
                }
            }
            for value in &catalog.instruments {
                if instruments
                    .insert(value.instrument_id.clone(), value.clone())
                    .is_some_and(|previous| previous != *value)
                {
                    conflicts += 1;
                }
            }
            for value in &catalog.listings {
                if listings
                    .insert(value.listing_id.clone(), value.clone())
                    .is_some_and(|previous| previous != *value)
                {
                    conflicts += 1;
                }
            }
            for value in &catalog.markets {
                if markets
                    .insert(value.market_id.clone(), value.clone())
                    .is_some_and(|previous| previous != *value)
                {
                    conflicts += 1;
                }
            }
            for value in &catalog.financial_products {
                if financial_products
                    .insert(value.product_id.clone(), value.clone())
                    .is_some_and(|previous| previous != *value)
                {
                    conflicts += 1;
                }
            }
            for value in &catalog.execution_accesses {
                if execution_accesses
                    .insert(value.access_id.clone(), value.clone())
                    .is_some_and(|previous| previous != *value)
                {
                    conflicts += 1;
                }
            }
        }
        if !unavailable_without_last_good.is_empty() {
            return Err(ReferenceError::Provider(format!(
                "reference providers unavailable without a last-known-good snapshot: {}",
                unavailable_without_last_good.join(", ")
            )));
        }
        if successful_sources == 0 && self.last_good.is_empty() {
            return Err(ReferenceError::Provider(format!(
                "all reference providers failed: {}",
                failures.join("; ")
            )));
        }
        if !failures.is_empty() {
            tracing::warn!(
                event = "reference_provider_degraded",
                component = "reference",
                failures = ?failures,
                "reference refresh used last-known-good provider snapshots"
            );
        }
        if conflicts > 0 {
            tracing::warn!(
                event = "reference_provider_conflicts",
                component = "reference",
                conflict_count = conflicts,
                "reference providers returned conflicting records; deterministic source order was used"
            );
        }
        tracing::info!(
            event = "reference_provider_fan_in_completed",
            component = "reference",
            provider_count = self.workers.len(),
            successful_sources,
            duration_ms = started.elapsed().as_millis() as u64,
            "reference provider fan-in completed"
        );
        Ok(ProviderCatalog {
            entities: entities.into_values().collect(),
            assets: assets.into_values().collect(),
            instruments: instruments.into_values().collect(),
            listings: listings.into_values().collect(),
            markets: markets.into_values().collect(),
            financial_products: financial_products.into_values().collect(),
            execution_accesses: execution_accesses.into_values().collect(),
        })
    }

    fn provider_health(&self) -> Vec<ProviderHealth> {
        self.workers
            .iter()
            .map(|worker| {
                self.health
                    .get(&worker.source_id)
                    .cloned()
                    .unwrap_or_else(|| ProviderHealth {
                        source_id: worker.source_id.clone(),
                        status: "unknown".into(),
                        last_attempt_unix_nanos: None,
                        last_success_unix_nanos: None,
                        consecutive_failures: 0,
                        stale: false,
                    })
            })
            .collect()
    }
}

fn tag_catalog_source(catalog: &mut ProviderCatalog, source_id: &str) {
    let source_id = source_id.to_owned();
    for value in &mut catalog.entities {
        value.source_id = Some(source_id.clone());
    }
    for value in &mut catalog.assets {
        value.source_id = Some(source_id.clone());
    }
    for value in &mut catalog.instruments {
        value.source_id = Some(source_id.clone());
    }
    for value in &mut catalog.listings {
        value.source_id = Some(source_id.clone());
    }
    for value in &mut catalog.markets {
        value.source_id = Some(source_id.clone());
    }
    for value in &mut catalog.financial_products {
        value.source_id = Some(source_id.clone());
    }
    for value in &mut catalog.execution_accesses {
        value.source_id = Some(source_id.clone());
    }
}

impl CompositeSource {
    fn mark_success(&mut self, source_id: &str) {
        let health = self
            .health
            .entry(source_id.to_owned())
            .or_insert_with(|| ProviderHealth {
                source_id: source_id.to_owned(),
                status: "unknown".into(),
                last_attempt_unix_nanos: None,
                last_success_unix_nanos: None,
                consecutive_failures: 0,
                stale: false,
            });
        health.status = "ready".into();
        health.last_success_unix_nanos = Some(unix_nanos().into());
        health.consecutive_failures = 0;
        health.stale = false;
        if let Some(worker) = self
            .workers
            .iter_mut()
            .find(|worker| worker.source_id == source_id)
        {
            worker.retry_after = None;
        }
    }

    fn mark_failure(&mut self, source_id: &str, status: &str) {
        let health = self
            .health
            .entry(source_id.to_owned())
            .or_insert_with(|| ProviderHealth {
                source_id: source_id.to_owned(),
                status: "unknown".into(),
                last_attempt_unix_nanos: None,
                last_success_unix_nanos: None,
                consecutive_failures: 0,
                stale: false,
            });
        health.status = if self.last_good.contains_key(source_id) {
            "stale"
        } else {
            status
        }
        .into();
        health.consecutive_failures = health.consecutive_failures.saturating_add(1);
        health.stale = self.last_good.contains_key(source_id);
        let backoff_seconds = 5u64.saturating_mul(1u64 << health.consecutive_failures.min(6));
        if let Some(worker) = self
            .workers
            .iter_mut()
            .find(|worker| worker.source_id == source_id)
        {
            worker.retry_after =
                Some(Instant::now() + Duration::from_secs(backoff_seconds.min(300)));
        }
    }

    fn mark_syncing(&mut self, source_id: &str) {
        let has_last_good = self.last_good.contains_key(source_id);
        let health = self
            .health
            .entry(source_id.to_owned())
            .or_insert_with(|| ProviderHealth {
                source_id: source_id.to_owned(),
                status: "unknown".into(),
                last_attempt_unix_nanos: None,
                last_success_unix_nanos: None,
                consecutive_failures: 0,
                stale: false,
            });
        health.status = "syncing".into();
        health.stale = has_last_good;
        if let Some(worker) = self
            .workers
            .iter_mut()
            .find(|worker| worker.source_id == source_id)
        {
            worker.retry_after = None;
        }
    }
}

fn unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

impl OkxSource {
    pub fn new(
        id: impl Into<String>,
        instrument_type: OkxInstrumentType,
        endpoint: impl Into<String>,
    ) -> ReferenceResult<Self> {
        let provider = OkxConnection::connect(OkxConnectionConfig {
            environment: "public".into(),
            rest_base_url: endpoint.into(),
            shared_quota: None,
        })
        .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        Ok(Self {
            id: id.into(),
            connection: provider.blocking_instrument_catalog(instrument_type),
        })
    }
}

impl ReferenceSource for OkxSource {
    fn source_id(&self) -> &str {
        &self.id
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        okx_provider_catalog(facts)
    }
}

impl BinanceSpotSource {
    pub fn new(endpoint: impl Into<String>) -> ReferenceResult<Self> {
        Ok(Self {
            connection: binance_public_connection(endpoint)?
                .blocking_instrument_catalog(BinanceInstrumentType::Spot),
        })
    }
}

impl BinanceOptionsSource {
    pub fn new(endpoint: impl Into<String>) -> ReferenceResult<Self> {
        Ok(Self {
            connection: binance_public_connection(endpoint)?
                .blocking_instrument_catalog(BinanceInstrumentType::Option),
        })
    }
}

impl BinanceDerivativesSource {
    pub fn new(
        instrument_type: BinanceInstrumentType,
        endpoint: impl Into<String>,
    ) -> ReferenceResult<Self> {
        let id = match instrument_type {
            BinanceInstrumentType::UsdMFutures => "binance-usdm-futures",
            BinanceInstrumentType::CoinMFutures => "binance-coinm-futures",
            _ => {
                return Err(ReferenceError::Provider(
                    "Binance derivatives source requires a futures instrument type".into(),
                ))
            }
        };
        Ok(Self {
            id,
            instrument_type,
            connection: binance_public_connection(endpoint)?
                .blocking_instrument_catalog(instrument_type),
        })
    }
}

fn binance_public_connection(endpoint: impl Into<String>) -> ReferenceResult<BinanceConnection> {
    let endpoint = endpoint.into();
    let trimmed = endpoint.trim_end_matches('/');
    let base_url = trimmed
        .strip_suffix("/api/v3/exchangeInfo")
        .or_else(|| trimmed.strip_suffix("/fapi/v1/exchangeInfo"))
        .or_else(|| trimmed.strip_suffix("/dapi/v1/exchangeInfo"))
        .or_else(|| trimmed.strip_suffix("/eapi/v1/exchangeInfo"))
        .unwrap_or(trimmed)
        .to_owned();
    BinanceConnection::connect(BinanceConnectionConfig {
        environment: "public".into(),
        rest_base_url: base_url,
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_200,
            cancel_reserve_weight: 0,
        },
        shared_quota: None,
    })
    .map_err(|error| ReferenceError::Provider(error.to_string()))
}

impl BinanceEquitySource {
    pub fn new(api_key: impl Into<String>, secret: impl Into<String>) -> ReferenceResult<Self> {
        let provider = BinanceConnection::connect(BinanceConnectionConfig {
            environment: "live".into(),
            rest_base_url: "https://api.binance.com".into(),
            quota: BinanceQuotaAllocation {
                request_weight_per_minute: 1_200,
                cancel_reserve_weight: 0,
            },
            shared_quota: None,
        })
        .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        let principal = provider
            .principal_connection(BinancePrincipalConfig {
                binding_id: "reference.binance.equity".into(),
                principal_id: None,
                api_key: api_key.into().into(),
                secret: secret.into().into(),
                principal_quota: None,
            })
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        let connection = principal.blocking_equity_instrument_catalog();
        Ok(Self { connection })
    }
}

impl MassiveSource {
    pub fn new(api_key: impl Into<String>, base_url: impl Into<String>) -> ReferenceResult<Self> {
        Self::new_with_underlying(api_key, base_url, None)
    }

    pub fn new_with_underlying(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
        underlying: Option<String>,
    ) -> ReferenceResult<Self> {
        let connection = massive_public_connection(api_key, base_url)?
            .blocking_instrument_catalog(MassiveInstrumentQuery::options(underlying))
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        Ok(Self {
            connection,
            cursor: None,
            accumulated: None,
            sync_store: None,
        })
    }

    pub fn new_with_sync_store(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
        mut sync_store: Box<dyn ProviderSyncStore>,
    ) -> ReferenceResult<Self> {
        let mut source = Self::new(api_key, base_url)?;
        let (cursor, accumulated) = sync_store
            .load_state("massive-options")?
            .unwrap_or((None, None));
        source.cursor = cursor;
        source.accumulated = accumulated;
        source.sync_store = Some(sync_store);
        Ok(source)
    }
}

impl MassiveEquitySource {
    pub fn new(api_key: impl Into<String>, base_url: impl Into<String>) -> ReferenceResult<Self> {
        let connection = massive_public_connection(api_key, base_url)?
            .blocking_instrument_catalog(MassiveInstrumentQuery::equities())
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        Ok(Self {
            connection,
            cursor: None,
            accumulated: None,
            sync_store: None,
        })
    }

    pub fn new_with_sync_store(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
        mut sync_store: Box<dyn ProviderSyncStore>,
    ) -> ReferenceResult<Self> {
        let mut source = Self::new(api_key, base_url)?;
        let (cursor, accumulated) = sync_store
            .load_state("massive-equity")?
            .unwrap_or((None, None));
        source.cursor = cursor;
        source.accumulated = accumulated;
        source.sync_store = Some(sync_store);
        Ok(source)
    }
}

fn massive_public_connection(
    api_key: impl Into<String>,
    base_url: impl Into<String>,
) -> ReferenceResult<MassiveConnection> {
    MassiveConnection::connect(MassiveConnectionConfig {
        environment: "public".into(),
        rest_base_url: base_url.into(),
        api_key: secrecy::SecretString::new(api_key.into().into()),
    })
    .map_err(|error| ReferenceError::Provider(error.to_string()))
}

impl HyperliquidSource {
    pub fn new(endpoint: impl Into<String>) -> ReferenceResult<Self> {
        let provider = HyperliquidConnection::connect(HyperliquidConnectionConfig {
            environment: "public".into(),
            info_endpoint: endpoint.into(),
        })
        .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        let connection = provider
            .blocking_instrument_catalog()
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        Ok(Self { connection })
    }
}

impl ReferenceSource for BinanceSpotSource {
    fn source_id(&self) -> &str {
        "binance-spot"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        binance_provider_catalog(facts, BinanceInstrumentType::Spot)
    }
}

impl ReferenceSource for BinanceOptionsSource {
    fn source_id(&self) -> &str {
        "binance-options"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        binance_provider_catalog(facts, BinanceInstrumentType::Option)
    }
}

impl ReferenceSource for BinanceDerivativesSource {
    fn source_id(&self) -> &str {
        self.id
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        binance_provider_catalog(facts, self.instrument_type)
    }
}

impl ReferenceSource for BinanceEquitySource {
    fn source_id(&self) -> &str {
        "binance-equity"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        binance_equity_provider_catalog(facts)
    }
}

impl ReferenceSource for MassiveSource {
    fn source_id(&self) -> &str {
        "massive-options"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        massive_provider_catalog(facts)
    }

    fn fetch_catalog_step(&mut self) -> ReferenceResult<ProviderUpdate> {
        let mut cursor = self.cursor.take();
        let mut catalog = self.accumulated.take();
        let mut complete = false;
        let mut page_count = 0;
        for _ in 0..MASSIVE_PAGES_PER_REFRESH {
            page_count += 1;
            let page = self
                .connection
                .fetch_instruments_page(cursor.as_deref(), 1000)
                .map_err(|error| ReferenceError::Provider(error.to_string()))?;
            cursor = page.next_cursor;
            let page_catalog = massive_provider_catalog(page.catalog)?;
            catalog = Some(merge_provider_catalog(catalog.as_ref(), page_catalog));
            complete = page.complete;
            if complete {
                break;
            }
        }
        let catalog = catalog.unwrap_or_default();
        self.cursor = if complete { None } else { cursor };
        self.accumulated = if complete {
            None
        } else {
            Some(catalog.clone())
        };
        if let Some(store) = self.sync_store.as_mut() {
            store.save_state(
                "massive-options",
                self.cursor.as_deref(),
                self.accumulated.as_ref(),
            )?;
        }
        Ok(ProviderUpdate {
            catalog,
            complete,
            page_count,
        })
    }
}

impl ReferenceSource for MassiveEquitySource {
    fn source_id(&self) -> &str {
        "massive-equity"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        massive_provider_catalog(facts)
    }

    fn fetch_catalog_step(&mut self) -> ReferenceResult<ProviderUpdate> {
        let mut cursor = self.cursor.take();
        let mut catalog = self.accumulated.take();
        let mut complete = false;
        let mut page_count = 0;
        for _ in 0..MASSIVE_PAGES_PER_REFRESH {
            page_count += 1;
            let page = self
                .connection
                .fetch_instruments_page(cursor.as_deref(), 1000)
                .map_err(|error| ReferenceError::Provider(error.to_string()))?;
            cursor = page.next_cursor;
            let page_catalog = massive_provider_catalog(page.catalog)?;
            catalog = Some(merge_provider_catalog(catalog.as_ref(), page_catalog));
            complete = page.complete;
            if complete {
                break;
            }
        }
        let catalog = catalog.unwrap_or_default();
        self.cursor = if complete { None } else { cursor };
        self.accumulated = if complete {
            None
        } else {
            Some(catalog.clone())
        };
        if let Some(store) = self.sync_store.as_mut() {
            store.save_state(
                "massive-equity",
                self.cursor.as_deref(),
                self.accumulated.as_ref(),
            )?;
        }
        Ok(ProviderUpdate {
            catalog,
            complete,
            page_count,
        })
    }
}

impl ReferenceSource for HyperliquidSource {
    fn source_id(&self) -> &str {
        "hyperliquid"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        hyperliquid_provider_catalog(facts)
    }
}

fn hyperliquid_provider_catalog(
    facts: ExternalInstrumentCatalog,
) -> ReferenceResult<ProviderCatalog> {
    if facts.participant.id.as_str() != "hyperliquid" {
        return Err(ReferenceError::Provider(format!(
            "Hyperliquid source received catalog for {}",
            facts.participant.id
        )));
    }
    let mut catalog = ProviderCatalog {
        entities: vec![Entity {
            entity_id: "exchange:hyperliquid".into(),
            entity_type: "exchange".into(),
            name: "Hyperliquid".into(),
            status: "active".into(),
            source_id: None,
        }],
        ..Default::default()
    };
    for value in facts.instruments {
        if value.kind != ExternalInstrumentKind::Perpetual {
            return Err(ReferenceError::Provider(format!(
                "unsupported Hyperliquid instrument kind: {:?}",
                value.kind
            )));
        }
        let source_symbol = value.source_symbol.as_str().to_ascii_uppercase();
        let base = value
            .base_currency
            .as_ref()
            .map(|value| value.as_str().to_ascii_uppercase())
            .ok_or_else(|| {
                ReferenceError::Provider("Hyperliquid base currency is missing".into())
            })?;
        let quote = value
            .quote_currency
            .as_ref()
            .map(|value| value.as_str().to_ascii_uppercase())
            .unwrap_or_else(|| "USDC".into());
        for code in [&base, &quote] {
            catalog.assets.push(Asset {
                asset_id: kairos_domain_types::AssetId::new(format!("asset:crypto:{code}"))?,
                code: code.clone(),
                asset_class: "crypto".into(),
                status: "active".into(),
                ..Asset::default()
            });
        }
        let instrument_id =
            kairos_domain_types::InstrumentId::new(format!("instrument:perpetual:{base}-{quote}"))?;
        let listing_id = kairos_domain_types::ListingId::new(format!(
            "listing:hyperliquid:perpetual:{base}:{quote}"
        ))?;
        let exchange_id = kairos_domain_types::Exchange::new("exchange:hyperliquid")?;
        let status: kairos_domain_types::ReferenceStatus =
            if value.active { "active" } else { "inactive" }.into();
        catalog.instruments.push(Instrument {
            instrument_id: instrument_id.clone(),
            symbol: kairos_domain_types::Symbol::new(source_symbol.clone())?,
            instrument_type: "perpetual".into(),
            product_family: Some("perpetual".into()),
            primary_currency_asset_id: Some(kairos_domain_types::AssetId::new(format!(
                "asset:crypto:{quote}"
            ))?),
            status,
            ..Instrument::default()
        });
        catalog.listings.push(Listing {
            listing_id: listing_id.clone(),
            instrument_id: instrument_id.clone(),
            exchange_id: exchange_id.clone(),
            exchange_symbol: kairos_domain_types::Symbol::new(source_symbol.clone())?,
            status,
            effective_from_unix_nanos: 0.into(),
            ..Listing::default()
        });
        catalog.markets.push(Market {
            market_id: kairos_domain_types::MarketId::new(format!(
                "market:hyperliquid:perpetual:{source_symbol}"
            ))?,
            market_key: format!("hyperliquid.perpetual.{source_symbol}"),
            instrument_id,
            listing_id,
            exchange_id,
            market_type: "perpetual".into(),
            asset_type: Some("crypto".into()),
            source_symbol: kairos_domain_types::Symbol::new(source_symbol)?,
            base_asset_id: Some(kairos_domain_types::AssetId::new(format!(
                "asset:crypto:{base}"
            ))?),
            quote_asset_id: Some(kairos_domain_types::AssetId::new(format!(
                "asset:crypto:{quote}"
            ))?),
            status,
            price_tick: value.price_tick,
            quantity_tick: value.quantity_tick,
            price_precision: value.price_precision.unwrap_or_default() as i32,
            quantity_precision: value.quantity_precision.unwrap_or_default() as i32,
            minimum_quantity: value.minimum_quantity,
            minimum_notional: value.minimum_notional,
            contract_size: value.contract_value,
            effective_from_unix_nanos: 0.into(),
            ..Market::default()
        });
    }
    catalog
        .assets
        .sort_by(|left, right| left.asset_id.cmp(&right.asset_id));
    catalog
        .assets
        .dedup_by(|left, right| left.asset_id == right.asset_id);
    catalog.validate()?;
    Ok(catalog)
}

fn massive_provider_catalog(facts: ExternalInstrumentCatalog) -> ReferenceResult<ProviderCatalog> {
    if facts.participant.id.as_str() != "massive" {
        return Err(ReferenceError::Provider(format!(
            "Massive source received catalog for {}",
            facts.participant.id
        )));
    }
    let mut catalog = ProviderCatalog {
        entities: vec![Entity {
            entity_id: "data_provider:massive".into(),
            entity_type: "data_provider".into(),
            name: "Massive".into(),
            status: "active".into(),
            source_id: None,
        }],
        ..Default::default()
    };
    for value in facts.instruments {
        append_massive_instrument(&mut catalog, value)?;
    }
    catalog
        .entities
        .sort_by(|left, right| left.entity_id.cmp(&right.entity_id));
    catalog
        .entities
        .dedup_by(|left, right| left.entity_id == right.entity_id);
    catalog
        .assets
        .sort_by(|left, right| left.asset_id.cmp(&right.asset_id));
    catalog
        .assets
        .dedup_by(|left, right| left.asset_id == right.asset_id);
    catalog
        .instruments
        .sort_by(|left, right| left.instrument_id.cmp(&right.instrument_id));
    catalog
        .instruments
        .dedup_by(|left, right| left.instrument_id == right.instrument_id);
    catalog
        .listings
        .sort_by(|left, right| left.listing_id.cmp(&right.listing_id));
    catalog
        .listings
        .dedup_by(|left, right| left.listing_id == right.listing_id);
    catalog
        .markets
        .sort_by(|left, right| left.market_id.cmp(&right.market_id));
    catalog
        .markets
        .dedup_by(|left, right| left.market_id == right.market_id);
    catalog.validate()?;
    Ok(catalog)
}

fn append_massive_instrument(
    catalog: &mut ProviderCatalog,
    value: ExternalInstrument,
) -> ReferenceResult<()> {
    let source_symbol = value.source_symbol.as_str().to_ascii_uppercase();
    let exchange_id = massive_exchange_id(value.source_venue.as_deref());
    catalog.entities.push(Entity {
        entity_id: exchange_id.clone(),
        entity_type: "exchange".into(),
        name: massive_exchange_name(&exchange_id).into(),
        status: "active".into(),
        source_id: None,
    });
    let quote = value
        .quote_currency
        .as_ref()
        .map(|value| value.as_str().to_ascii_uppercase())
        .unwrap_or_else(|| "USD".into());
    let status: kairos_domain_types::ReferenceStatus =
        if value.active { "active" } else { "inactive" }.into();
    let (family, instrument_id, symbol, underlying_id) = match value.kind {
        ExternalInstrumentKind::Equity => {
            let ticker = source_symbol.clone();
            ensure_massive_underlying(catalog, &ticker, &quote, &exchange_id, status)?;
            (
                "equity",
                format!("instrument:equity:US:{ticker}:common"),
                ticker,
                None,
            )
        }
        ExternalInstrumentKind::Option => {
            let underlying = value
                .underlying
                .as_ref()
                .map(|value| value.as_str().to_ascii_uppercase())
                .ok_or_else(|| {
                    ReferenceError::Provider("Massive option underlying is missing".into())
                })?;
            ensure_massive_underlying(catalog, &underlying, &quote, &exchange_id, status)?;
            let expiry = canonical_expiry(value.expiry_unix_nanos)?;
            let strike = value.strike.as_deref().ok_or_else(|| {
                ReferenceError::Provider("Massive option strike is missing".into())
            })?;
            let right = match value
                .option_right
                .as_deref()
                .unwrap_or_default()
                .to_ascii_lowercase()
                .as_str()
            {
                "call" | "c" => "C",
                "put" | "p" => "P",
                other => {
                    return Err(ReferenceError::Provider(format!(
                        "unsupported Massive option right: {other}"
                    )))
                }
            };
            (
                "options",
                format!("instrument:option:{underlying}:{expiry}:{strike}:{right}"),
                source_symbol.clone(),
                Some(kairos_domain_types::InstrumentId::new(format!(
                    "instrument:equity:US:{underlying}:common"
                ))?),
            )
        }
        other => {
            return Err(ReferenceError::Provider(format!(
                "unsupported Massive instrument kind: {other:?}"
            )))
        }
    };
    let instrument_id = kairos_domain_types::InstrumentId::new(instrument_id)?;
    let listing_id = kairos_domain_types::ListingId::new(if family == "equity" {
        format!("listing:{exchange_id}:equity:{source_symbol}:{quote}")
    } else {
        format!("listing:massive:options:{source_symbol}")
    })?;
    let market_id = kairos_domain_types::MarketId::new(if family == "equity" {
        format!("market:{exchange_id}:equity:{source_symbol}")
    } else {
        format!("market:massive:options:{source_symbol}")
    })?;
    let exchange = kairos_domain_types::Exchange::new(exchange_id.clone())?;
    catalog.instruments.push(Instrument {
        instrument_id: instrument_id.clone(),
        symbol: kairos_domain_types::Symbol::new(symbol)?,
        instrument_type: if family == "options" {
            "option"
        } else {
            "equity"
        }
        .into(),
        product_family: Some(family.into()),
        issuer_id: (family == "equity").then(|| {
            kairos_domain_types::IssuerId::new(format!("issuer:US:{source_symbol}"))
                .expect("validated Massive issuer")
        }),
        share_class: (family == "equity").then(|| "common".into()),
        primary_currency_asset_id: Some(kairos_domain_types::AssetId::new(format!(
            "asset:fiat:{quote}"
        ))?),
        underlying_instrument_id: underlying_id,
        expiry_unix_nanos: value.expiry_unix_nanos,
        strike: value.strike.clone(),
        option_right: value.option_right.clone(),
        status,
        ..Instrument::default()
    });
    catalog.listings.push(Listing {
        listing_id: listing_id.clone(),
        instrument_id: instrument_id.clone(),
        exchange_id: exchange.clone(),
        exchange_symbol: kairos_domain_types::Symbol::new(source_symbol.clone())?,
        status,
        effective_from_unix_nanos: 0.into(),
        ..Listing::default()
    });
    catalog.markets.push(Market {
        market_id,
        market_key: format!("massive.{family}.{source_symbol}"),
        instrument_id,
        listing_id,
        exchange_id: exchange,
        market_type: family.into(),
        asset_type: Some("equity".into()),
        source_symbol: kairos_domain_types::Symbol::new(source_symbol)?,
        base_asset_id: value.underlying.as_ref().map(|underlying| {
            kairos_domain_types::AssetId::new(format!(
                "asset:equity:{}",
                underlying.as_str().to_ascii_uppercase()
            ))
            .expect("validated Massive underlying asset")
        }),
        quote_asset_id: Some(kairos_domain_types::AssetId::new(format!(
            "asset:fiat:{quote}"
        ))?),
        status,
        price_tick: value.price_tick,
        quantity_tick: value.quantity_tick,
        price_precision: value.price_precision.unwrap_or_default() as i32,
        quantity_precision: value.quantity_precision.unwrap_or_default() as i32,
        minimum_quantity: value.minimum_quantity,
        minimum_notional: value.minimum_notional,
        contract_size: value.contract_value,
        effective_from_unix_nanos: 0.into(),
        effective_to_unix_nanos: value.expiry_unix_nanos,
        ..Market::default()
    });
    Ok(())
}

fn ensure_massive_underlying(
    catalog: &mut ProviderCatalog,
    ticker: &str,
    quote: &str,
    exchange_id: &str,
    status: kairos_domain_types::ReferenceStatus,
) -> ReferenceResult<()> {
    let equity_asset = kairos_domain_types::AssetId::new(format!("asset:equity:{ticker}"))?;
    let fiat_asset = kairos_domain_types::AssetId::new(format!("asset:fiat:{quote}"))?;
    catalog.assets.push(Asset {
        asset_id: equity_asset.clone(),
        code: ticker.into(),
        asset_class: "equity".into(),
        status: "active".into(),
        ..Asset::default()
    });
    catalog.assets.push(Asset {
        asset_id: fiat_asset.clone(),
        code: quote.into(),
        asset_class: "fiat".into(),
        status: "active".into(),
        ..Asset::default()
    });
    let instrument_id =
        kairos_domain_types::InstrumentId::new(format!("instrument:equity:US:{ticker}:common"))?;
    if catalog
        .instruments
        .iter()
        .any(|value| value.instrument_id == instrument_id)
    {
        return Ok(());
    }
    let listing_id = kairos_domain_types::ListingId::new(format!(
        "listing:{exchange_id}:equity:{ticker}:{quote}"
    ))?;
    let exchange = kairos_domain_types::Exchange::new(exchange_id.to_owned())?;
    catalog.instruments.push(Instrument {
        instrument_id: instrument_id.clone(),
        symbol: kairos_domain_types::Symbol::new(ticker.to_owned())?,
        instrument_type: "equity".into(),
        product_family: Some("equity".into()),
        issuer_id: Some(kairos_domain_types::IssuerId::new(format!(
            "issuer:US:{ticker}"
        ))?),
        share_class: Some("common".into()),
        primary_currency_asset_id: Some(fiat_asset.clone()),
        status,
        ..Instrument::default()
    });
    catalog.listings.push(Listing {
        listing_id: listing_id.clone(),
        instrument_id: instrument_id.clone(),
        exchange_id: exchange.clone(),
        exchange_symbol: kairos_domain_types::Symbol::new(ticker.to_owned())?,
        status,
        effective_from_unix_nanos: 0.into(),
        ..Listing::default()
    });
    catalog.markets.push(Market {
        market_id: kairos_domain_types::MarketId::new(format!(
            "market:{exchange_id}:equity:{ticker}"
        ))?,
        market_key: format!("massive.equity.{ticker}"),
        instrument_id,
        listing_id,
        exchange_id: exchange,
        market_type: "equity".into(),
        asset_type: Some("equity".into()),
        source_symbol: kairos_domain_types::Symbol::new(ticker.to_owned())?,
        base_asset_id: Some(equity_asset),
        quote_asset_id: Some(fiat_asset),
        status,
        price_tick: Some("0.01".into()),
        quantity_tick: Some("1".into()),
        price_precision: 2,
        quantity_precision: 0,
        contract_size: Some("1".into()),
        effective_from_unix_nanos: 0.into(),
        ..Market::default()
    });
    Ok(())
}

fn massive_exchange_id(source_venue: Option<&str>) -> String {
    match source_venue
        .unwrap_or("unknown")
        .trim()
        .to_ascii_uppercase()
        .as_str()
    {
        "XNAS" | "NASDAQ" => "exchange:nasdaq".into(),
        "XNYS" | "NYSE" => "exchange:nyse".into(),
        "XASE" | "AMEX" => "exchange:amex".into(),
        "" | "UNKNOWN" => "exchange:unknown".into(),
        value => format!("exchange:{}", value.to_ascii_lowercase()),
    }
}

fn massive_exchange_name(exchange_id: &str) -> &str {
    match exchange_id {
        "exchange:nasdaq" => "Nasdaq",
        "exchange:nyse" => "NYSE",
        "exchange:amex" => "NYSE American",
        "exchange:unknown" => "Unknown exchange",
        _ => "Exchange",
    }
}

fn binance_provider_catalog(
    facts: ExternalInstrumentCatalog,
    instrument_type: BinanceInstrumentType,
) -> ReferenceResult<ProviderCatalog> {
    if facts.participant.id.as_str() != "binance" {
        return Err(ReferenceError::Provider(format!(
            "Binance source received catalog for {}",
            facts.participant.id
        )));
    }
    let mut catalog = ProviderCatalog {
        entities: vec![Entity {
            entity_id: "exchange:binance".into(),
            entity_type: "exchange".into(),
            name: "Binance".into(),
            status: "active".into(),
            source_id: None,
        }],
        ..Default::default()
    };
    for value in facts.instruments {
        append_binance_instrument(&mut catalog, value, instrument_type)?;
    }
    catalog
        .assets
        .sort_by(|left, right| left.asset_id.cmp(&right.asset_id));
    catalog
        .assets
        .dedup_by(|left, right| left.asset_id == right.asset_id);
    catalog
        .instruments
        .sort_by(|left, right| left.instrument_id.cmp(&right.instrument_id));
    catalog
        .instruments
        .dedup_by(|left, right| left.instrument_id == right.instrument_id);
    catalog.validate()?;
    Ok(catalog)
}

fn append_binance_instrument(
    catalog: &mut ProviderCatalog,
    value: ExternalInstrument,
    instrument_type: BinanceInstrumentType,
) -> ReferenceResult<()> {
    let source_symbol = value.source_symbol.as_str().to_ascii_uppercase();
    let base = value
        .base_currency
        .as_ref()
        .map(|value| value.as_str().to_ascii_uppercase())
        .ok_or_else(|| ReferenceError::Provider("Binance base currency is missing".into()))?;
    let quote = value
        .quote_currency
        .as_ref()
        .map(|value| value.as_str().to_ascii_uppercase())
        .ok_or_else(|| ReferenceError::Provider("Binance quote currency is missing".into()))?;
    for code in [&base, &quote] {
        catalog.assets.push(Asset {
            asset_id: kairos_domain_types::AssetId::new(format!("asset:crypto:{code}"))?,
            code: code.clone(),
            asset_class: "crypto".into(),
            status: "active".into(),
            ..Asset::default()
        });
    }
    let (family, instrument_id, underlying_instrument_id) = match value.kind {
        ExternalInstrumentKind::Spot if instrument_type == BinanceInstrumentType::Spot => {
            ("spot", format!("instrument:spot:{base}"), None)
        }
        ExternalInstrumentKind::Perpetual
            if matches!(
                instrument_type,
                BinanceInstrumentType::UsdMFutures | BinanceInstrumentType::CoinMFutures
            ) =>
        {
            (
                match instrument_type {
                    BinanceInstrumentType::UsdMFutures => "usd-m-futures",
                    BinanceInstrumentType::CoinMFutures => "coin-m-futures",
                    _ => unreachable!("guarded futures type"),
                },
                format!("instrument:perpetual:{base}-{quote}"),
                None,
            )
        }
        ExternalInstrumentKind::Future
            if matches!(
                instrument_type,
                BinanceInstrumentType::UsdMFutures | BinanceInstrumentType::CoinMFutures
            ) =>
        {
            let expiry = canonical_expiry(value.expiry_unix_nanos)?;
            (
                match instrument_type {
                    BinanceInstrumentType::UsdMFutures => "usd-m-futures",
                    BinanceInstrumentType::CoinMFutures => "coin-m-futures",
                    _ => unreachable!("guarded futures type"),
                },
                format!("instrument:future:{base}-{quote}:{expiry}"),
                None,
            )
        }
        ExternalInstrumentKind::Option if instrument_type == BinanceInstrumentType::Option => {
            let expiry = canonical_expiry(value.expiry_unix_nanos)?;
            let strike = value.strike.as_deref().ok_or_else(|| {
                ReferenceError::Provider("Binance option strike is missing".into())
            })?;
            let right = value.option_right.as_deref().ok_or_else(|| {
                ReferenceError::Provider("Binance option right is missing".into())
            })?;
            let underlying =
                kairos_domain_types::InstrumentId::new(format!("instrument:spot:{base}"))?;
            if !catalog
                .instruments
                .iter()
                .any(|value| value.instrument_id == underlying)
            {
                catalog.instruments.push(Instrument {
                    instrument_id: underlying.clone(),
                    symbol: kairos_domain_types::Symbol::new(base.clone())?,
                    instrument_type: "spot".into(),
                    product_family: Some("spot".into()),
                    primary_currency_asset_id: Some(kairos_domain_types::AssetId::new(format!(
                        "asset:crypto:{quote}"
                    ))?),
                    status: "active".into(),
                    ..Instrument::default()
                });
            }
            (
                "options",
                format!(
                    "instrument:option:{base}-{quote}:{expiry}:{strike}:{}",
                    match right.to_ascii_lowercase().as_str() {
                        "call" | "c" => "C",
                        "put" | "p" => "P",
                        _ => {
                            return Err(ReferenceError::Provider(format!(
                                "unsupported Binance option right: {right}"
                            )));
                        }
                    }
                ),
                Some(underlying),
            )
        }
        other => {
            return Err(ReferenceError::Provider(format!(
            "Binance {instrument_type:?} catalog contained incompatible instrument kind: {other:?}"
        )))
        }
    };
    let instrument_id = kairos_domain_types::InstrumentId::new(instrument_id)?;
    let listing_id = kairos_domain_types::ListingId::new(if family == "spot" {
        format!("listing:binance:spot:{base}:{quote}")
    } else {
        format!("listing:binance:{family}:{source_symbol}")
    })?;
    let exchange_id = kairos_domain_types::Exchange::new("exchange:binance")?;
    let market_id =
        kairos_domain_types::MarketId::new(format!("market:binance:{family}:{source_symbol}"))?;
    let status: kairos_domain_types::ReferenceStatus =
        if value.active { "active" } else { "inactive" }.into();
    catalog.instruments.push(Instrument {
        instrument_id: instrument_id.clone(),
        symbol: kairos_domain_types::Symbol::new(if family == "spot" {
            base.clone()
        } else {
            source_symbol.clone()
        })?,
        instrument_type: match value.kind {
            ExternalInstrumentKind::Perpetual => "perpetual",
            ExternalInstrumentKind::Future => "future",
            ExternalInstrumentKind::Option => "option",
            _ => family,
        }
        .into(),
        product_family: Some(family.into()),
        primary_currency_asset_id: Some(kairos_domain_types::AssetId::new(format!(
            "asset:crypto:{quote}"
        ))?),
        underlying_instrument_id,
        expiry_unix_nanos: value.expiry_unix_nanos,
        strike: value.strike.clone(),
        option_right: value.option_right.clone(),
        status,
        ..Instrument::default()
    });
    catalog.listings.push(Listing {
        listing_id: listing_id.clone(),
        instrument_id: instrument_id.clone(),
        exchange_id: exchange_id.clone(),
        exchange_symbol: kairos_domain_types::Symbol::new(source_symbol.clone())?,
        status,
        effective_from_unix_nanos: 0.into(),
        ..Listing::default()
    });
    catalog.markets.push(Market {
        market_id,
        market_key: format!("binance.{family}.{source_symbol}"),
        instrument_id,
        listing_id,
        exchange_id,
        market_type: family.into(),
        asset_type: Some("crypto".into()),
        source_symbol: kairos_domain_types::Symbol::new(source_symbol)?,
        base_asset_id: Some(kairos_domain_types::AssetId::new(format!(
            "asset:crypto:{base}"
        ))?),
        quote_asset_id: Some(kairos_domain_types::AssetId::new(format!(
            "asset:crypto:{quote}"
        ))?),
        status,
        price_tick: value.price_tick,
        quantity_tick: value.quantity_tick,
        price_precision: value.price_precision.unwrap_or_default() as i32,
        quantity_precision: value.quantity_precision.unwrap_or_default() as i32,
        minimum_quantity: value.minimum_quantity,
        minimum_notional: value.minimum_notional,
        contract_size: value.contract_value,
        effective_from_unix_nanos: 0.into(),
        effective_to_unix_nanos: value.expiry_unix_nanos,
        ..Market::default()
    });
    Ok(())
}

fn okx_provider_catalog(facts: ExternalInstrumentCatalog) -> ReferenceResult<ProviderCatalog> {
    if facts.participant.id.as_str() != "okx" {
        return Err(ReferenceError::Provider(format!(
            "OKX source received catalog for {}",
            facts.participant.id
        )));
    }
    let mut catalog = ProviderCatalog {
        entities: vec![Entity {
            entity_id: "exchange:okx".into(),
            entity_type: "exchange".into(),
            name: "OKX".into(),
            status: "active".into(),
            source_id: None,
        }],
        ..Default::default()
    };
    for value in facts.instruments {
        append_okx_instrument(&mut catalog, value)?;
    }
    catalog
        .assets
        .sort_by(|left, right| left.asset_id.cmp(&right.asset_id));
    catalog
        .assets
        .dedup_by(|left, right| left.asset_id == right.asset_id);
    catalog
        .instruments
        .sort_by(|left, right| left.instrument_id.cmp(&right.instrument_id));
    catalog
        .instruments
        .dedup_by(|left, right| left.instrument_id == right.instrument_id);
    catalog.validate()?;
    Ok(catalog)
}

fn append_okx_instrument(
    catalog: &mut ProviderCatalog,
    value: ExternalInstrument,
) -> ReferenceResult<()> {
    let source_symbol = value.source_symbol.as_str().to_ascii_uppercase();
    let (base, quote) = okx_base_quote(&value)?;
    let (family, instrument_id) = match value.kind {
        ExternalInstrumentKind::Equity => {
            return Err(ReferenceError::Provider(
                "OKX catalog cannot contain equity instruments".into(),
            ))
        }
        ExternalInstrumentKind::Spot => ("spot", format!("instrument:spot:{base}")),
        ExternalInstrumentKind::Margin => ("margin", format!("instrument:margin:{base}-{quote}")),
        ExternalInstrumentKind::Perpetual => {
            ("swap", format!("instrument:perpetual:{base}-{quote}"))
        }
        ExternalInstrumentKind::Future => {
            let expiry = canonical_expiry(value.expiry_unix_nanos)?;
            (
                "futures",
                format!("instrument:future:{base}-{quote}:{expiry}"),
            )
        }
        ExternalInstrumentKind::Option => {
            let expiry = canonical_expiry(value.expiry_unix_nanos)?;
            let strike = value
                .strike
                .as_deref()
                .ok_or_else(|| ReferenceError::Provider("OKX option strike is missing".into()))?;
            let right = value
                .option_right
                .as_deref()
                .ok_or_else(|| ReferenceError::Provider("OKX option right is missing".into()))?;
            (
                "options",
                format!(
                    "instrument:option:{base}-{quote}:{expiry}:{strike}:{}",
                    right.to_ascii_uppercase()
                ),
            )
        }
    };
    for code in [&base, &quote] {
        catalog.assets.push(Asset {
            asset_id: kairos_domain_types::AssetId::new(format!("asset:crypto:{code}"))?,
            code: code.clone(),
            asset_class: "crypto".into(),
            status: "active".into(),
            ..Asset::default()
        });
    }
    let instrument_id = kairos_domain_types::InstrumentId::new(instrument_id)?;
    let listing_id = kairos_domain_types::ListingId::new(if family == "spot" {
        format!("listing:okx:spot:{base}:{quote}")
    } else {
        format!("listing:okx:{family}:{source_symbol}")
    })?;
    let exchange_id = kairos_domain_types::Exchange::new("exchange:okx")?;
    let market_id =
        kairos_domain_types::MarketId::new(format!("market:okx:{family}:{source_symbol}"))?;
    let status: kairos_domain_types::ReferenceStatus =
        if value.active { "active" } else { "inactive" }.into();
    catalog.instruments.push(Instrument {
        instrument_id: instrument_id.clone(),
        symbol: kairos_domain_types::Symbol::new(source_symbol.clone())?,
        instrument_type: family.into(),
        product_family: Some(family.into()),
        primary_currency_asset_id: Some(kairos_domain_types::AssetId::new(format!(
            "asset:crypto:{quote}"
        ))?),
        expiry_unix_nanos: value.expiry_unix_nanos,
        strike: value.strike.clone(),
        option_right: value.option_right.clone(),
        status,
        ..Instrument::default()
    });
    catalog.listings.push(Listing {
        listing_id: listing_id.clone(),
        instrument_id: instrument_id.clone(),
        exchange_id: exchange_id.clone(),
        exchange_symbol: kairos_domain_types::Symbol::new(source_symbol.clone())?,
        status,
        effective_from_unix_nanos: 0.into(),
        ..Listing::default()
    });
    catalog.markets.push(Market {
        market_id,
        market_key: format!("okx.{family}.{source_symbol}"),
        instrument_id,
        listing_id,
        exchange_id,
        market_type: family.into(),
        asset_type: Some("crypto".into()),
        source_symbol: kairos_domain_types::Symbol::new(source_symbol)?,
        base_asset_id: Some(kairos_domain_types::AssetId::new(format!(
            "asset:crypto:{base}"
        ))?),
        quote_asset_id: Some(kairos_domain_types::AssetId::new(format!(
            "asset:crypto:{quote}"
        ))?),
        status,
        price_tick: value.price_tick,
        quantity_tick: value.quantity_tick,
        minimum_quantity: value.minimum_quantity,
        minimum_notional: value.minimum_notional,
        contract_size: value.contract_value,
        price_precision: value.price_precision.unwrap_or_default() as i32,
        quantity_precision: value.quantity_precision.unwrap_or_default() as i32,
        effective_from_unix_nanos: 0.into(),
        effective_to_unix_nanos: None,
        ..Market::default()
    });
    Ok(())
}

fn okx_base_quote(value: &ExternalInstrument) -> ReferenceResult<(String, String)> {
    let from_pair = |pair: &str| {
        let mut values = pair.split('-').filter(|value| !value.is_empty());
        values
            .next()
            .zip(values.next())
            .map(|(base, quote)| (base.to_ascii_uppercase(), quote.to_ascii_uppercase()))
    };
    let fallback = value
        .underlying
        .as_ref()
        .and_then(|value| from_pair(value.as_str()))
        .or_else(|| from_pair(value.source_symbol.as_str()));
    let base = value
        .base_currency
        .as_ref()
        .map(|value| value.as_str().to_ascii_uppercase())
        .or_else(|| fallback.as_ref().map(|value| value.0.clone()))
        .ok_or_else(|| {
            ReferenceError::Provider("OKX instrument base currency is missing".into())
        })?;
    let quote = value
        .quote_currency
        .as_ref()
        .map(|value| value.as_str().to_ascii_uppercase())
        .or_else(|| fallback.as_ref().map(|value| value.1.clone()))
        .or_else(|| {
            value
                .settlement_currency
                .as_ref()
                .map(|value| value.as_str().to_ascii_uppercase())
        })
        .ok_or_else(|| {
            ReferenceError::Provider("OKX instrument quote currency is missing".into())
        })?;
    Ok((base, quote))
}

fn canonical_expiry(value: Option<kairos_domain_types::UnixNanos>) -> ReferenceResult<String> {
    let value = value
        .ok_or_else(|| ReferenceError::Provider("expiring instrument expiry is missing".into()))?;
    let seconds = i64::try_from(value.get() / 1_000_000_000)
        .map_err(|_| ReferenceError::Provider("instrument expiry is out of range".into()))?;
    let date = chrono::DateTime::from_timestamp(seconds, 0)
        .ok_or_else(|| ReferenceError::Provider("instrument expiry is invalid".into()))?;
    Ok(date.format("%Y-%m-%d").to_string())
}

fn binance_equity_provider_catalog(
    facts: ExternalInstrumentCatalog,
) -> ReferenceResult<ProviderCatalog> {
    if facts.participant.id.as_str() != "binance" {
        return Err(ReferenceError::Provider(format!(
            "Binance Equity source received catalog for {}",
            facts.participant.id
        )));
    }
    let mut catalog = ProviderCatalog {
        entities: vec![Entity {
            entity_id: "exchange:binance".into(),
            entity_type: "exchange".into(),
            name: "Binance".into(),
            status: "active".into(),
            source_id: None,
        }],
        ..Default::default()
    };

    for value in facts.instruments {
        if value.kind != ExternalInstrumentKind::Equity {
            return Err(ReferenceError::Provider(format!(
                "Binance Equity catalog contained incompatible instrument kind: {:?}",
                value.kind
            )));
        }
        let symbol = value.source_symbol.as_str().to_ascii_uppercase();
        let quote = value
            .quote_currency
            .as_ref()
            .map(|value| value.as_str().to_ascii_uppercase())
            .unwrap_or_else(|| "USD".into());
        let settlement = value
            .settlement_currency
            .as_ref()
            .map(|value| value.as_str().to_ascii_uppercase())
            .unwrap_or_else(|| "USDC".into());
        let status: kairos_domain_types::ReferenceStatus =
            if value.active { "active" } else { "inactive" }.into();
        let equity_asset = kairos_domain_types::AssetId::new(format!("asset:equity:{symbol}"))?;
        let quote_asset = kairos_domain_types::AssetId::new(format!("asset:fiat:{quote}"))?;
        let settlement_asset =
            kairos_domain_types::AssetId::new(format!("asset:crypto:{settlement}"))?;
        for asset in [
            Asset {
                asset_id: equity_asset.clone(),
                code: symbol.clone(),
                asset_class: "equity".into(),
                status,
                ..Asset::default()
            },
            Asset {
                asset_id: quote_asset.clone(),
                code: quote.clone(),
                asset_class: "fiat".into(),
                status: "active".into(),
                ..Asset::default()
            },
            Asset {
                asset_id: settlement_asset.clone(),
                code: settlement,
                asset_class: "crypto".into(),
                status: "active".into(),
                ..Asset::default()
            },
        ] {
            if !catalog
                .assets
                .iter()
                .any(|existing| existing.asset_id == asset.asset_id)
            {
                catalog.assets.push(asset);
            }
        }

        let instrument_id = kairos_domain_types::InstrumentId::new(format!(
            "instrument:equity:US:{symbol}:common"
        ))?;
        let listing_id = kairos_domain_types::ListingId::new(format!(
            "listing:binance:equity:{symbol}:{quote}"
        ))?;
        let market_id =
            kairos_domain_types::MarketId::new(format!("market:binance:equity:{symbol}"))?;
        let exchange_id = kairos_domain_types::Exchange::new("exchange:binance")?;
        catalog.instruments.push(Instrument {
            instrument_id: instrument_id.clone(),
            symbol: kairos_domain_types::Symbol::new(symbol.clone())?,
            instrument_type: "equity".into(),
            product_family: Some("equity".into()),
            issuer_id: Some(kairos_domain_types::IssuerId::new(format!(
                "issuer:US:{symbol}"
            ))?),
            share_class: Some("common".into()),
            primary_currency_asset_id: Some(quote_asset.clone()),
            status,
            ..Instrument::default()
        });
        catalog.listings.push(Listing {
            listing_id: listing_id.clone(),
            instrument_id: instrument_id.clone(),
            exchange_id: exchange_id.clone(),
            exchange_symbol: kairos_domain_types::Symbol::new(symbol.clone())?,
            status,
            effective_from_unix_nanos: 0.into(),
            ..Listing::default()
        });
        catalog.markets.push(Market {
            market_id: market_id.clone(),
            market_key: format!("binance.equity.{symbol}"),
            instrument_id,
            listing_id,
            exchange_id,
            market_type: "equity".into(),
            asset_type: Some("equity".into()),
            source_symbol: kairos_domain_types::Symbol::new(symbol.clone())?,
            base_asset_id: Some(equity_asset),
            quote_asset_id: Some(quote_asset),
            status,
            price_tick: value.price_tick,
            quantity_tick: value.quantity_tick,
            price_precision: value.price_precision.unwrap_or_default() as i32,
            quantity_precision: value.quantity_precision.unwrap_or_default() as i32,
            minimum_quantity: value.minimum_quantity,
            minimum_notional: value.minimum_notional,
            effective_from_unix_nanos: 0.into(),
            ..Market::default()
        });
        catalog.execution_accesses.push(ExecutionAccess {
            access_id: kairos_domain_types::ExecutionAccessId::new(format!(
                "access:binance:equity:{symbol}"
            ))?,
            market_id,
            provider_id: "binance".into(),
            product_family: "equity".into(),
            provider_symbol: value.source_symbol,
            settlement_asset_id: Some(settlement_asset),
            status,
            effective_from_unix_nanos: 0.into(),
            ..ExecutionAccess::default()
        });
    }

    catalog.validate()?;
    Ok(catalog)
}

fn merge_provider_catalog(
    previous: Option<&ProviderCatalog>,
    incoming: ProviderCatalog,
) -> ProviderCatalog {
    let mut merged = previous.cloned().unwrap_or_default();
    for value in incoming.entities {
        upsert_vec(&mut merged.entities, value.entity_id.clone(), value, |v| {
            v.entity_id.clone()
        });
    }
    for value in incoming.assets {
        upsert_vec(&mut merged.assets, value.asset_id.to_string(), value, |v| {
            v.asset_id.to_string()
        });
    }
    for value in incoming.instruments {
        upsert_vec(
            &mut merged.instruments,
            value.instrument_id.to_string(),
            value,
            |v| v.instrument_id.to_string(),
        );
    }
    for value in incoming.listings {
        upsert_vec(
            &mut merged.listings,
            value.listing_id.to_string(),
            value,
            |v| v.listing_id.to_string(),
        );
    }
    for value in incoming.markets {
        upsert_vec(
            &mut merged.markets,
            value.market_id.to_string(),
            value,
            |v| v.market_id.to_string(),
        );
    }
    for value in incoming.financial_products {
        upsert_vec(
            &mut merged.financial_products,
            value.product_id.clone(),
            value,
            |v| v.product_id.clone(),
        );
    }
    for value in incoming.execution_accesses {
        upsert_vec(
            &mut merged.execution_accesses,
            value.access_id.to_string(),
            value,
            |v| v.access_id.to_string(),
        );
    }
    merged
}

fn upsert_vec<T>(values: &mut Vec<T>, id: String, value: T, key: impl Fn(&T) -> String) {
    if let Some(existing) = values.iter_mut().find(|existing| key(existing) == id) {
        *existing = value;
    } else {
        values.push(value);
    }
}

#[cfg(test)]
mod tests {
    use super::{
        binance_equity_provider_catalog, binance_provider_catalog, hyperliquid_provider_catalog,
        massive_provider_catalog, okx_provider_catalog, CompositeSource, ReferenceSource,
    };
    use crate::domain::{Market, ProviderCatalog, ReferenceResult};
    use crate::services::sqlx_storage::SqlxProviderSyncStore;
    use kairos_domain_types::{Currency, MarketId, ProviderSymbol};
    use kairos_integration::application::capabilities::reference::{
        ExternalInstrument, ExternalInstrumentCatalog, ExternalInstrumentKind,
    };
    use kairos_integration::application::{ParticipantKind, ParticipantRef};
    use kairos_integration::participants::binance::InstrumentType as BinanceInstrumentType;
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    };

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

    impl ReferenceSource for FlakySource {
        fn source_id(&self) -> &str {
            "test-flaky"
        }

        fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
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

    impl ReferenceSource for PagedSource {
        fn source_id(&self) -> &str {
            "test-paged"
        }

        fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
            Ok(ProviderCatalog::default())
        }

        fn fetch_catalog_step(&mut self) -> ReferenceResult<super::ProviderUpdate> {
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
            })
        }
    }

    impl ReferenceSource for RefreshingPagedSource {
        fn source_id(&self) -> &str {
            "test-refreshing-paged"
        }

        fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
            Ok(ProviderCatalog::default())
        }

        fn fetch_catalog_step(&mut self) -> ReferenceResult<super::ProviderUpdate> {
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
            })
        }
    }

    impl ReferenceSource for AlwaysFailSource {
        fn source_id(&self) -> &str {
            "test-flaky"
        }

        fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
            Err(crate::domain::ReferenceError::Provider(
                "test provider unavailable after restart".into(),
            ))
        }
    }

    impl ReferenceSource for FixedSource {
        fn source_id(&self) -> &str {
            self.id
        }

        fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
            Ok(self.catalog.clone())
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
            .any(|value| value.market_id == "market:okx:swap:BTC-USDT-SWAP"));
        assert!(catalog
            .markets
            .iter()
            .all(|value| value.exchange_id == "exchange:okx"));
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
                    expiry_unix_nanos: Some(kairos_domain_types::UnixNanos::new(
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
        let spot_catalog = binance_provider_catalog(facts, BinanceInstrumentType::Spot).unwrap();
        let option_catalog = binance_provider_catalog(
            ExternalInstrumentCatalog {
                participant: ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
                instruments: vec![option],
            },
            BinanceInstrumentType::Option,
        )
        .unwrap();
        assert!(spot_catalog
            .instruments
            .iter()
            .any(|value| value.instrument_id == "instrument:spot:BTC"));
        assert!(option_catalog.instruments.iter().any(|value| {
            value.instrument_id == "instrument:option:BTC-USDT:2026-05-28:50000:C"
                && value.underlying_instrument_id.as_deref() == Some("instrument:spot:BTC")
        }));
        assert!(spot_catalog.markets.iter().any(|value| {
            value.market_id == "market:binance:spot:BTCUSDT"
                && value.minimum_notional.as_deref() == Some("10")
        }));
    }

    #[test]
    fn binance_derivative_facts_keep_product_selection_but_not_canonical_identity() {
        let expiry = kairos_domain_types::UnixNanos::new(1_782_432_000_000_000_000);
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
            BinanceInstrumentType::UsdMFutures,
        )
        .unwrap();
        assert_eq!(
            catalog.instruments[0].instrument_id,
            "instrument:future:BTC-USDT:2026-06-26"
        );
        assert_eq!(
            catalog.markets[0].market_id,
            "market:binance:usd-m-futures:BTCUSDT_260626"
        );
        assert_eq!(catalog.markets[0].effective_to_unix_nanos, Some(expiry));
    }

    #[test]
    fn binance_equity_provider_facts_receive_canonical_identity_only_in_reference() {
        let catalog = binance_equity_provider_catalog(ExternalInstrumentCatalog {
            participant: ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            instruments: vec![ExternalInstrument {
                source_symbol: ProviderSymbol::new("AAPL").unwrap(),
                source_venue: Some("XNAS".into()),
                kind: ExternalInstrumentKind::Equity,
                base_currency: None,
                quote_currency: Some(Currency::new("USD").unwrap()),
                settlement_currency: Some(Currency::new("USDC").unwrap()),
                underlying: None,
                expiry_unix_nanos: None,
                strike: None,
                option_right: None,
                active: true,
                price_tick: Some("0.01".into()),
                quantity_tick: Some("1".into()),
                minimum_quantity: Some("1".into()),
                minimum_notional: None,
                contract_value: None,
                price_precision: Some(2),
                quantity_precision: Some(0),
            }],
        })
        .unwrap();

        assert_eq!(
            catalog.instruments[0].instrument_id,
            "instrument:equity:US:AAPL:common"
        );
        assert_eq!(catalog.markets[0].market_id, "market:binance:equity:AAPL");
        assert_eq!(
            catalog.execution_accesses[0].settlement_asset_id.as_deref(),
            Some("asset:crypto:USDC")
        );
    }

    #[test]
    fn massive_provider_facts_receive_canonical_identity_only_in_reference() {
        let expiry = kairos_domain_types::UnixNanos::new(1_800_000_000_000_000_000);
        let catalog = massive_provider_catalog(ExternalInstrumentCatalog {
            participant: ParticipantRef::new(ParticipantKind::DataProvider, "massive").unwrap(),
            instruments: vec![ExternalInstrument {
                source_symbol: ProviderSymbol::new("O:SPY260821C00500000").unwrap(),
                source_venue: Some("XNAS".into()),
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
        assert!(catalog
            .entities
            .iter()
            .any(|value| { value.entity_id == "exchange:nasdaq" && value.name == "Nasdaq" }));
        assert!(catalog
            .instruments
            .iter()
            .any(|value| { value.instrument_id == "instrument:equity:US:SPY:common" }));
        assert!(catalog.instruments.iter().any(|value| {
            value.instrument_id == "instrument:option:SPY:2027-01-15:500:C"
                && value.underlying_instrument_id.as_deref()
                    == Some("instrument:equity:US:SPY:common")
        }));
        assert!(catalog.markets.iter().any(|value| {
            value.market_id == "market:massive:options:O:SPY260821C00500000"
                && value
                    .contract_size
                    .as_ref()
                    .map(ToString::to_string)
                    .as_deref()
                    == Some("100")
        }));
    }

    #[test]
    fn hyperliquid_provider_facts_receive_canonical_identity_only_in_reference() {
        let catalog = hyperliquid_provider_catalog(ExternalInstrumentCatalog {
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
        })
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
    fn provider_failure_keeps_last_known_good_snapshot() {
        let calls = Arc::new(AtomicUsize::new(0));
        let mut source = CompositeSource::new(vec![Box::new(FlakySource {
            calls: Arc::clone(&calls),
        })])
        .unwrap();
        let first = source.fetch_catalog().unwrap();
        let second = source.fetch_catalog().unwrap();
        assert_eq!(first.markets, second.markets);
        assert_eq!(first.markets[0].source_id.as_deref(), Some("test-flaky"));
        assert_eq!(calls.load(Ordering::SeqCst), 2);
        assert_eq!(source.provider_health()[0].status, "stale");
        assert!(source.provider_health()[0].stale);
    }

    #[test]
    fn provider_last_known_good_survives_composite_restart() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        {
            let store = SqlxProviderSyncStore::open(&path).unwrap();
            let calls = Arc::new(AtomicUsize::new(0));
            let mut source = CompositeSource::new_with_sync_store(
                vec![Box::new(FlakySource {
                    calls: Arc::clone(&calls),
                })],
                Some(Box::new(store)),
            )
            .unwrap();
            assert_eq!(source.fetch_catalog().unwrap().markets.len(), 1);
        }
        let store = SqlxProviderSyncStore::open(&path).unwrap();
        let mut restarted = CompositeSource::new_with_sync_store(
            vec![Box::new(AlwaysFailSource)],
            Some(Box::new(store)),
        )
        .unwrap();
        let catalog = restarted.fetch_catalog().unwrap();
        assert_eq!(catalog.markets.len(), 1);
        assert_eq!(restarted.provider_health()[0].status, "stale");
    }

    #[test]
    fn provider_without_last_known_good_rejects_partial_refresh() {
        let mut source = CompositeSource::new(vec![Box::new(AlwaysFailSource)]).unwrap();
        let error = source.fetch_catalog().unwrap_err().to_string();
        assert!(error.contains("without a last-known-good snapshot"));
    }

    #[test]
    fn provider_failure_opens_circuit_and_applies_backoff() {
        let mut source = CompositeSource::new(vec![Box::new(AlwaysFailSource)]).unwrap();
        let _ = source.fetch_catalog();
        assert_eq!(source.provider_health()[0].consecutive_failures, 1);
        let error = source.fetch_catalog().unwrap_err().to_string();
        assert!(error.contains("provider circuit is open"));
        assert_eq!(source.provider_health()[0].consecutive_failures, 1);
    }

    #[test]
    fn conflicting_sources_use_deterministic_order_and_retain_source_identity() {
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
        let mut source = CompositeSource::new(vec![Box::new(first), Box::new(second)]).unwrap();
        let catalog = source.fetch_catalog().unwrap();
        assert_eq!(catalog.markets[0].status, "inactive".into());
        assert_eq!(catalog.markets[0].source_id.as_deref(), Some("provider-b"));
    }

    #[test]
    fn partial_provider_pages_do_not_drop_previous_page() {
        let mut source = CompositeSource::new(vec![Box::new(PagedSource { calls: 0 })]).unwrap();
        let first_error = source.fetch_catalog().unwrap_err().to_string();
        assert!(first_error.contains("without a last-known-good snapshot"));
        assert_eq!(source.provider_health()[0].status, "syncing");
        let second = source.fetch_catalog().unwrap();
        assert_eq!(second.markets.len(), 2);
        assert!(second
            .markets
            .iter()
            .any(|market| market.market_id == "market:page-1"));
    }

    #[test]
    fn incomplete_provider_sync_does_not_replace_last_good_snapshot() {
        let mut source =
            CompositeSource::new(vec![Box::new(RefreshingPagedSource { calls: 0 })]).unwrap();
        let first = source.fetch_catalog().unwrap();
        assert_eq!(first.markets[0].market_id, "market:complete-old");
        let second = source.fetch_catalog().unwrap();
        assert_eq!(second.markets[0].market_id, "market:complete-old");
        assert_eq!(source.provider_health()[0].status, "syncing");
        let third = source.fetch_catalog().unwrap();
        assert_eq!(third.markets[0].market_id, "market:complete-new");
        assert_eq!(source.provider_health()[0].status, "ready");
    }
}
