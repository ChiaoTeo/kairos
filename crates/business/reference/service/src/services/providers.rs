//! Provider sources and in-memory implementations for the Reference actor.

use std::collections::{BTreeMap, BTreeSet};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use futures_util::future::join_all;
use kairos_integration::application::capabilities::reference::{
    AsyncInstrumentCatalogConnection, ExternalInstrument, ExternalInstrumentCatalog,
    ExternalInstrumentKind,
};
use kairos_integration::participants::binance::{
    BinanceConnection, BinanceConnectionConfig, BinanceEquityInstrumentCatalog,
    BinanceQuotaAllocation, InstrumentType as BinanceInstrumentType,
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
    fn normalized_facts_authoritative(&self) -> bool {
        false
    }
    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog>;

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<ProviderUpdate> {
        Ok(ProviderUpdate {
            catalog: self.fetch_catalog().await?,
            complete: true,
            page_count: 1,
            facts_persisted: false,
        })
    }

    /// Advance one source without querying its peers. A completed source
    /// returns the merged last-known-good fan-in; an incomplete page returns
    /// `None` and remains operational progress only.
    async fn advance_source(
        &mut self,
        source_id: &str,
    ) -> ReferenceResult<Option<ProviderCatalog>> {
        Err(ReferenceError::Invalid(format!(
            "reference source does not support targeted refresh: {source_id}"
        )))
    }

    async fn set_source_paused(&mut self, source_id: &str, _paused: bool) -> ReferenceResult<()> {
        Err(ReferenceError::Invalid(format!(
            "reference source does not support runtime control: {source_id}"
        )))
    }

    async fn set_option_underlying(
        &mut self,
        underlying: &str,
        _enabled: bool,
    ) -> ReferenceResult<()> {
        Err(ReferenceError::Invalid(format!(
            "reference source does not support option coverage: {underlying}"
        )))
    }

    fn option_underlyings(&self) -> Vec<String> {
        Vec::new()
    }

    fn provider_health(&self) -> Vec<ProviderHealth> {
        Vec::new()
    }
}

/// Adds workspace-declared participants to the provider fan-in without
/// creating another mutable catalog owner or another provider health entry.
pub(crate) struct ParticipantAugmentedSource<S> {
    inner: S,
    participants: Vec<Entity>,
}

impl<S> ParticipantAugmentedSource<S> {
    pub(crate) fn wrap(inner: S, participants: Vec<Entity>) -> Self {
        Self {
            inner,
            participants,
        }
    }
}

impl<S: ReferenceSource> ReferenceSource for ParticipantAugmentedSource<S> {
    fn source_id(&self) -> &str {
        self.inner.source_id()
    }

    fn normalized_facts_authoritative(&self) -> bool {
        self.inner.normalized_facts_authoritative()
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let mut catalog = self.inner.fetch_catalog().await?;
        catalog.entities.extend(self.participants.iter().cloned());
        Ok(catalog)
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<ProviderUpdate> {
        let mut update = self.inner.fetch_catalog_step().await?;
        update
            .catalog
            .entities
            .extend(self.participants.iter().cloned());
        Ok(update)
    }

    async fn advance_source(
        &mut self,
        source_id: &str,
    ) -> ReferenceResult<Option<ProviderCatalog>> {
        self.inner.advance_source(source_id).await
    }

    async fn set_source_paused(&mut self, source_id: &str, paused: bool) -> ReferenceResult<()> {
        self.inner.set_source_paused(source_id, paused).await
    }

    async fn set_option_underlying(
        &mut self,
        underlying: &str,
        enabled: bool,
    ) -> ReferenceResult<()> {
        self.inner.set_option_underlying(underlying, enabled).await
    }

    fn option_underlyings(&self) -> Vec<String> {
        self.inner.option_underlyings()
    }

    fn provider_health(&self) -> Vec<ProviderHealth> {
        self.inner.provider_health()
    }
}

pub(crate) struct ProviderUpdate {
    pub catalog: ProviderCatalog,
    pub complete: bool,
    pub page_count: usize,
    pub facts_persisted: bool,
}

/// Binance Spot public reference source.
///
/// The provider connection and vendor normalization live in integration. This
/// source only maps the neutral integration payload into Reference-owned
/// domain records.
pub struct BinanceSpotSource {
    connection: kairos_integration::participants::binance::BinanceInstrumentCatalog,
}

pub struct BinanceOptionsSource {
    connection: kairos_integration::participants::binance::BinanceInstrumentCatalog,
}

pub struct BinanceDerivativesSource {
    id: &'static str,
    instrument_type: BinanceInstrumentType,
    connection: kairos_integration::participants::binance::BinanceInstrumentCatalog,
}

pub struct BinanceEquitySource {
    connection: BinanceEquityInstrumentCatalog,
}

/// Massive stock-options discovery limited to explicitly managed underlyings.
///
/// The provider has a very large global option universe. Reference therefore
/// treats coverage as operational input and only promotes contracts belonging
/// to enabled underlyings. Market-data WebSocket observations may later add a
/// single underlying/contract to this input, but they never make a global
/// catalog scan authoritative.
pub struct MassiveOptionsCoverageSource<P> {
    api_key: String,
    base_url: String,
    scopes: BTreeMap<String, ScopedMassiveOptions>,
    last_good: BTreeMap<String, ProviderCatalog>,
    sync_store: P,
    next_scope: usize,
    coverage_dirty: bool,
}

struct ScopedMassiveOptions {
    connection: kairos_integration::participants::massive::MassiveInstrumentCatalog,
    cursor: Option<String>,
    legacy_accumulated: Option<ProviderCatalog>,
}

pub struct MassiveEquitySource<P> {
    connection: kairos_integration::participants::massive::MassiveInstrumentCatalog,
    cursor: Option<String>,
    accumulated: Option<ProviderCatalog>,
    sync_store: P,
}

pub struct HyperliquidSource {
    connection: kairos_integration::participants::hyperliquid::HyperliquidInstrumentCatalog,
}

pub struct OkxSource {
    id: String,
    connection: kairos_integration::participants::okx::OkxInstrumentCatalog,
}

/// Reference-owned fan-in for the global catalog. Each provider remains an
/// independent integration connection; this source only merges normalized
/// records before they enter the Reference actor.
pub struct CompositeSource<S, P> {
    workers: Vec<ProviderWorker<S>>,
    #[cfg(test)]
    last_good: BTreeMap<String, ProviderCatalog>,
    known_last_good: BTreeSet<String>,
    health: BTreeMap<String, ProviderHealth>,
    sync_store: Option<P>,
    paused: BTreeSet<String>,
}

struct ProviderWorker<S> {
    source_id: String,
    source: S,
    retry_after: Option<Instant>,
}

// Each provider refresh advances a bounded page batch. Large providers persist
// their cursor and candidate catalog between refreshes, while only completed
// candidates are promoted to last-known-good.
const PROVIDER_FETCH_TIMEOUT: Duration = Duration::from_secs(150);
// Persist one large-provider page per refresh. Rebuilding and serializing the
// complete accumulated candidate after every page makes an eight-page step
// quadratic in real Massive option catalogs and causes multi-gigabyte peaks.
// The persisted cursor makes one-page steps cheap, restartable, and schedulable.
const MASSIVE_PAGES_PER_REFRESH: usize = 1;
const MASSIVE_PAGE_TIMEOUT: Duration = Duration::from_secs(20);

impl<S, P> CompositeSource<S, P>
where
    S: ReferenceSource,
    P: ProviderSyncStore,
{
    pub async fn new_with_sync_store(
        sources: Vec<S>,
        mut sync_store: Option<P>,
    ) -> ReferenceResult<Self> {
        if sources.is_empty() {
            return Err(ReferenceError::Provider(
                "reference catalog has no sources".into(),
            ));
        }
        #[cfg(not(test))]
        if !sync_store
            .as_ref()
            .is_some_and(ProviderSyncStore::supports_normalized_promotion)
        {
            return Err(ReferenceError::Persistence(
                "production reference fan-in requires normalized SQLite promotion".into(),
            ));
        }
        #[cfg(test)]
        let mut last_good = BTreeMap::new();
        let mut known_last_good = BTreeSet::new();
        let mut paused = BTreeSet::new();
        for source in &sources {
            if let Some(store) = sync_store.as_mut() {
                if store.supports_normalized_promotion() {
                    if store.has_last_good(source.source_id()).await? {
                        known_last_good.insert(source.source_id().to_owned());
                    }
                    continue;
                }
                #[cfg(test)]
                if let Some(catalog) = store.load_last_good(source.source_id()).await? {
                    if provider_catalog_uses_current_canonical_shape(&catalog) {
                        last_good.insert(source.source_id().to_owned(), catalog);
                        known_last_good.insert(source.source_id().to_owned());
                    } else {
                        tracing::warn!(
                            event = "reference_provider_snapshot_schema_mismatch",
                            component = "reference",
                            provider = source.source_id(),
                            "persisted provider snapshot uses an obsolete canonical shape and will be refreshed before reuse"
                        );
                    }
                }
            }
        }
        if let Some(store) = sync_store.as_mut() {
            paused.extend(store.paused_sources().await?);
        }
        let workers = sources
            .into_iter()
            .map(|source| {
                let source_id = source.source_id().to_owned();
                ProviderWorker {
                    source_id,
                    source,
                    retry_after: None,
                }
            })
            .collect();
        Ok(Self {
            workers,
            #[cfg(test)]
            last_good,
            known_last_good,
            health: BTreeMap::new(),
            sync_store,
            paused,
        })
    }

    /// Build a catalog from already committed provider snapshots. This is used
    /// after one independently scheduled source finishes so the completion
    /// never triggers network work for Binance, OKX, or another peer.
    #[cfg(test)]
    fn last_good_catalog(&self) -> ReferenceResult<Option<ProviderCatalog>> {
        if self.last_good.is_empty() {
            return Ok(None);
        }
        merge_provider_catalog_views(self.last_good.values()).map(Some)
    }

    async fn fetch_normalized_facts(&mut self) -> ReferenceResult<ProviderCatalog> {
        let mut requests = Vec::new();
        let mut skipped = Vec::new();
        for worker in &mut self.workers {
            if self.paused.contains(&worker.source_id)
                || worker
                    .retry_after
                    .is_some_and(|until| until > Instant::now())
            {
                skipped.push(worker.source_id.clone());
                continue;
            }
            let source_id = worker.source_id.clone();
            let source = &mut worker.source;
            requests.push(async move {
                let result =
                    tokio::time::timeout(PROVIDER_FETCH_TIMEOUT, source.fetch_catalog_step())
                        .await
                        .map_err(|error| {
                            ReferenceError::Provider(format!("provider fetch timed out: {error}"))
                        })?;
                Ok::<_, ReferenceError>((source_id, result))
            });
        }
        let requests = join_all(requests).await;
        if self.sync_store.is_none() {
            return Err(ReferenceError::Persistence(
                "normalized provider store is unavailable".into(),
            ));
        }
        let mut unavailable = Vec::new();
        for source_id in skipped {
            if !self
                .sync_store
                .as_mut()
                .expect("store checked")
                .has_last_good(&source_id)
                .await?
            {
                unavailable.push(source_id);
            }
        }
        for result in requests {
            let (source_id, result) = match result {
                Ok(value) => value,
                Err(error) => return Err(error),
            };
            match result {
                Ok(mut update) if update.complete => {
                    tracing::info!(
                        event = "reference_provider_scan_completed",
                        component = "reference",
                        provider = %source_id,
                        page_count = update.page_count,
                        "normalized provider facts are ready for atomic promotion"
                    );
                    if !update.facts_persisted {
                        tag_catalog_source(&mut update.catalog, &source_id);
                        update.catalog.validate()?;
                        self.sync_store
                            .as_mut()
                            .expect("store checked")
                            .save_last_good(&source_id, &update.catalog)
                            .await?;
                    }
                    self.mark_success(&source_id);
                }
                Ok(update) => {
                    tracing::info!(
                        event = "reference_provider_sync_in_progress",
                        component = "reference",
                        provider = %source_id,
                        page_count = update.page_count,
                        "normalized provider scan will resume from its durable cursor"
                    );
                    self.mark_syncing(&source_id);
                    let has_last_good = self
                        .sync_store
                        .as_mut()
                        .expect("store checked")
                        .has_last_good(&source_id)
                        .await?;
                    if !has_last_good {
                        unavailable.push(source_id);
                    }
                }
                Err(error) => {
                    tracing::warn!(
                        event = "reference_provider_degraded",
                        component = "reference",
                        provider = %source_id,
                        error = %error,
                        "reference refresh retained normalized last-known-good facts"
                    );
                    self.mark_failure(&source_id, "stale");
                    let has_last_good = self
                        .sync_store
                        .as_mut()
                        .expect("store checked")
                        .has_last_good(&source_id)
                        .await?;
                    if !has_last_good {
                        unavailable.push(source_id);
                    }
                }
            }
        }
        if !unavailable.is_empty() {
            return Err(ReferenceError::Provider(format!(
                "providers unavailable without last-known-good facts: {}",
                unavailable.join(", ")
            )));
        }
        Ok(ProviderCatalog::default())
    }
}

#[cfg(test)]
impl<S: ReferenceSource> CompositeSource<S, ()> {
    pub async fn new(sources: Vec<S>) -> ReferenceResult<Self> {
        Self::new_with_sync_store(sources, None).await
    }
}

impl<S, P> ReferenceSource for CompositeSource<S, P>
where
    S: ReferenceSource,
    P: ProviderSyncStore,
{
    fn normalized_facts_authoritative(&self) -> bool {
        self.sync_store
            .as_ref()
            .is_some_and(ProviderSyncStore::supports_normalized_promotion)
    }

    fn source_id(&self) -> &str {
        if self.workers.len() == 1 {
            &self.workers[0].source_id
        } else {
            "reference-default"
        }
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        if self.normalized_facts_authoritative() {
            return self.fetch_normalized_facts().await;
        }
        #[cfg(not(test))]
        return Err(ReferenceError::Persistence(
            "production reference sources require normalized SQLite promotion".into(),
        ));
        #[cfg(test)]
        {
            // Provider queries are polled concurrently by the caller's runtime.
            // Each query has the same bounded deadline, so one slow provider does
            // not serialize or indefinitely hold the fan-in.
            let started = Instant::now();
            let mut failures = Vec::new();
            let mut unavailable_without_last_good = Vec::new();
            let mut requests = Vec::new();
            let mut paused_sources = Vec::new();
            for worker in &mut self.workers {
                if self.paused.contains(&worker.source_id) {
                    paused_sources.push(worker.source_id.clone());
                    continue;
                }
                if worker
                    .retry_after
                    .is_some_and(|until| until > Instant::now())
                {
                    failures.push(format!("{}: provider circuit is open", worker.source_id));
                    if !self.last_good.contains_key(&worker.source_id) {
                        unavailable_without_last_good.push(worker.source_id.clone());
                    }
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
                let source_id = worker.source_id.clone();
                let source = &mut worker.source;
                requests.push(async move {
                    let provider_started = Instant::now();
                    let result = match tokio::time::timeout(
                        PROVIDER_FETCH_TIMEOUT,
                        source.fetch_catalog_step(),
                    )
                    .await
                    {
                        Ok(result) => result,
                        Err(error) => Err(ReferenceError::Provider(format!(
                            "provider fetch timed out: {error}"
                        ))),
                    };
                    (source_id, result, provider_started)
                });
            }
            let requests = join_all(requests).await;
            for source_id in paused_sources {
                self.mark_paused(&source_id);
            }
            let mut entities = BTreeMap::new();
            let mut assets = BTreeMap::new();
            let mut instruments = BTreeMap::new();
            let mut listings = BTreeMap::new();
            let mut markets = BTreeMap::new();
            let mut financial_products = BTreeMap::new();
            let mut execution_accesses = BTreeMap::new();
            let mut conflicts = Vec::new();
            let mut reconciled_instruments = 0usize;
            let mut successful_sources = 0usize;
            for (source_id, result, provider_started) in requests {
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
                                store
                                    .save_last_good(
                                        &source_id,
                                        self.last_good
                                            .get(&source_id)
                                            .expect("inserted provider snapshot"),
                                    )
                                    .await?;
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
                        conflicts.push(format!("entity:{}", value.entity_id));
                    }
                }
                for value in &catalog.assets {
                    if assets
                        .insert(value.asset_id.clone(), value.clone())
                        .is_some_and(|previous| previous != *value)
                    {
                        conflicts.push(format!("asset:{}", value.asset_id));
                    }
                }
                for value in &catalog.instruments {
                    if let Some(previous) = instruments.get_mut(&value.instrument_id) {
                        if previous != value {
                            merge_canonical_instrument(previous, value).map_err(|error| {
                                ReferenceError::Provider(format!(
                                    "canonical instrument conflict for {}: {error}",
                                    value.instrument_id
                                ))
                            })?;
                            reconciled_instruments += 1;
                        }
                    } else {
                        instruments.insert(value.instrument_id.clone(), value.clone());
                    }
                }
                for value in &catalog.listings {
                    if listings
                        .insert(value.listing_id.clone(), value.clone())
                        .is_some_and(|previous| previous != *value)
                    {
                        conflicts.push(format!("listing:{}", value.listing_id));
                    }
                }
                for value in &catalog.markets {
                    if markets
                        .insert(value.market_id.clone(), value.clone())
                        .is_some_and(|previous| previous != *value)
                    {
                        conflicts.push(format!("market:{}", value.market_id));
                    }
                }
                for value in &catalog.financial_products {
                    if financial_products
                        .insert(value.product_id.clone(), value.clone())
                        .is_some_and(|previous| previous != *value)
                    {
                        conflicts.push(format!("financial_product:{}", value.product_id));
                    }
                }
                for value in &catalog.execution_accesses {
                    if execution_accesses
                        .insert(value.access_id.clone(), value.clone())
                        .is_some_and(|previous| previous != *value)
                    {
                        conflicts.push(format!("execution_access:{}", value.access_id));
                    }
                }
            }
            if !unavailable_without_last_good.is_empty() {
                #[cfg(not(test))]
                self.last_good.clear();
                return Err(ReferenceError::Provider(format!(
                "reference providers unavailable without a last-known-good snapshot: {}; failures: {}",
                unavailable_without_last_good.join(", "),
                failures.join("; ")
            )));
            }
            if successful_sources == 0 && self.last_good.is_empty() {
                #[cfg(not(test))]
                self.last_good.clear();
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
            if !conflicts.is_empty() {
                let sample = conflicts.iter().take(8).cloned().collect::<Vec<_>>();
                #[cfg(not(test))]
                self.last_good.clear();
                return Err(ReferenceError::Provider(format!(
                    "providers returned {} irreconcilable canonical record conflicts (sample: {})",
                    conflicts.len(),
                    sample.join(", ")
                )));
            }
            if reconciled_instruments > 0 {
                tracing::info!(
                    event = "reference_canonical_instruments_reconciled",
                    component = "reference",
                    instrument_count = reconciled_instruments,
                    "shared canonical instruments were reconciled across provider listings"
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
            // Include paused and circuit-open providers through their durable
            // snapshots without cloning them into a synthetic request result.
            // This also makes the returned fan-in independent from which workers
            // happened to be scheduled in this refresh cycle.
            let result = merge_provider_catalog_views(self.last_good.values());
            #[cfg(not(test))]
            self.last_good.clear();
            result
        }
    }

    async fn advance_source(
        &mut self,
        source_id: &str,
    ) -> ReferenceResult<Option<ProviderCatalog>> {
        let Some(index) = self
            .workers
            .iter()
            .position(|worker| worker.source_id == source_id)
        else {
            return Err(ReferenceError::Invalid(format!(
                "unknown reference source: {source_id}"
            )));
        };
        if self.paused.contains(source_id) {
            self.mark_paused(source_id);
            return Ok(None);
        }
        if self.workers[index]
            .retry_after
            .is_some_and(|until| until > Instant::now())
        {
            return Err(ReferenceError::Provider(format!(
                "{source_id}: provider circuit is open"
            )));
        }
        if self.normalized_facts_authoritative() {
            let result = tokio::time::timeout(
                PROVIDER_FETCH_TIMEOUT,
                self.workers[index].source.fetch_catalog_step(),
            )
            .await
            .map_err(|error| {
                ReferenceError::Provider(format!("provider fetch timed out: {error}"))
            })?;
            return match result {
                Ok(mut update) if update.complete => {
                    if !update.facts_persisted {
                        tag_catalog_source(&mut update.catalog, source_id);
                        update.catalog.validate()?;
                        self.sync_store
                            .as_mut()
                            .expect("normalized source has a store")
                            .save_last_good(source_id, &update.catalog)
                            .await?;
                    }
                    self.mark_success(source_id);
                    Ok(Some(ProviderCatalog::default()))
                }
                Ok(_) => {
                    self.mark_syncing(source_id);
                    Ok(None)
                }
                Err(error) => {
                    self.mark_failure(source_id, "failed");
                    Err(error)
                }
            };
        }
        #[cfg(not(test))]
        return Err(ReferenceError::Persistence(
            "production reference sources require normalized SQLite promotion".into(),
        ));
        #[cfg(test)]
        {
            let health =
                self.health
                    .entry(source_id.to_owned())
                    .or_insert_with(|| ProviderHealth {
                        source_id: source_id.to_owned(),
                        status: "unknown".into(),
                        last_attempt_unix_nanos: None,
                        last_success_unix_nanos: None,
                        consecutive_failures: 0,
                        stale: false,
                    });
            health.last_attempt_unix_nanos = Some(unix_nanos().into());
            let started = Instant::now();
            let result = {
                let source = &mut self.workers[index].source;
                match tokio::time::timeout(PROVIDER_FETCH_TIMEOUT, source.fetch_catalog_step())
                    .await
                {
                    Ok(result) => result,
                    Err(error) => Err(ReferenceError::Provider(format!(
                        "provider fetch timed out: {error}"
                    ))),
                }
            };
            tracing::info!(
                event = "reference_provider_targeted_fetch_completed",
                component = "reference",
                provider = source_id,
                duration_ms = started.elapsed().as_millis() as u64,
                success = result.is_ok(),
                complete = result
                    .as_ref()
                    .map(|update| update.complete)
                    .unwrap_or(false),
                page_count = result.as_ref().map(|update| update.page_count).unwrap_or(0),
                "targeted reference provider fetch completed"
            );
            match result {
                Ok(update) if !update.complete => {
                    self.mark_syncing(source_id);
                    #[cfg(not(test))]
                    self.last_good.clear();
                    Ok(None)
                }
                Ok(mut update) => {
                    tag_catalog_source(&mut update.catalog, source_id);
                    self.last_good.insert(source_id.to_owned(), update.catalog);
                    if let Some(store) = self.sync_store.as_mut() {
                        store
                            .save_last_good(
                                source_id,
                                self.last_good
                                    .get(source_id)
                                    .expect("inserted provider snapshot"),
                            )
                            .await?;
                    }
                    self.mark_success(source_id);
                    let result = self.last_good_catalog();
                    #[cfg(not(test))]
                    self.last_good.clear();
                    result
                }
                Err(error) => {
                    self.mark_failure(source_id, "failed");
                    #[cfg(not(test))]
                    self.last_good.clear();
                    Err(error)
                }
            }
        }
    }

    async fn set_source_paused(&mut self, source_id: &str, paused: bool) -> ReferenceResult<()> {
        if !self
            .workers
            .iter()
            .any(|worker| worker.source_id == source_id)
        {
            return Err(ReferenceError::Invalid(format!(
                "unknown reference source: {source_id}"
            )));
        }
        let store = self.sync_store.as_mut().ok_or_else(|| {
            ReferenceError::Persistence("reference source control store is unavailable".into())
        })?;
        store.set_source_paused(source_id, paused).await?;
        if paused {
            self.paused.insert(source_id.to_owned());
            self.mark_paused(source_id);
        } else {
            self.paused.remove(source_id);
            if let Some(health) = self.health.get_mut(source_id) {
                health.status = "unknown".into();
                health.stale = false;
            }
        }
        Ok(())
    }

    async fn set_option_underlying(
        &mut self,
        underlying: &str,
        enabled: bool,
    ) -> ReferenceResult<()> {
        let Some(worker) = self
            .workers
            .iter_mut()
            .find(|worker| worker.source_id == "massive-options")
        else {
            return Err(ReferenceError::Invalid(
                "Massive options source is not configured".into(),
            ));
        };
        worker
            .source
            .set_option_underlying(underlying, enabled)
            .await
    }

    fn option_underlyings(&self) -> Vec<String> {
        self.workers
            .iter()
            .find(|worker| worker.source_id == "massive-options")
            .map(|worker| worker.source.option_underlyings())
            .unwrap_or_default()
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
    // Entity, Asset, and Instrument are canonical records and may be observed
    // by several providers. A single provider ID on those records is both
    // lossy and order-dependent. Provider provenance belongs on the concrete
    // Listing/Market/access projections until the domain supports a provenance
    // set explicitly.
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

fn reconcile_canonical_instruments(values: &mut Vec<Instrument>) -> ReferenceResult<()> {
    let mut reconciled = BTreeMap::new();
    for value in std::mem::take(values) {
        if let Some(previous) = reconciled.get_mut(&value.instrument_id) {
            merge_canonical_instrument(previous, &value).map_err(|error| {
                ReferenceError::Provider(format!(
                    "provider produced conflicting canonical instrument {}: {error}",
                    value.instrument_id
                ))
            })?;
        } else {
            reconciled.insert(value.instrument_id.clone(), value);
        }
    }
    *values = reconciled.into_values().collect();
    Ok(())
}

fn merge_provider_catalog_views<'a>(
    catalogs: impl IntoIterator<Item = &'a ProviderCatalog>,
) -> ReferenceResult<ProviderCatalog> {
    let mut entities = BTreeMap::new();
    let mut assets = BTreeMap::new();
    let mut instruments = BTreeMap::new();
    let mut listings = BTreeMap::new();
    let mut markets = BTreeMap::new();
    let mut financial_products = BTreeMap::new();
    let mut execution_accesses = BTreeMap::new();
    let mut conflicts = Vec::new();
    let mut reconciled_instruments = 0usize;

    for catalog in catalogs {
        for value in &catalog.entities {
            if entities
                .insert(value.entity_id.clone(), value.clone())
                .is_some_and(|previous| previous != *value)
            {
                conflicts.push(format!("entity:{}", value.entity_id));
            }
        }
        for value in &catalog.assets {
            if assets
                .insert(value.asset_id.clone(), value.clone())
                .is_some_and(|previous| previous != *value)
            {
                conflicts.push(format!("asset:{}", value.asset_id));
            }
        }
        for value in &catalog.instruments {
            if let Some(previous) = instruments.get_mut(&value.instrument_id) {
                if previous != value {
                    merge_canonical_instrument(previous, value).map_err(|error| {
                        ReferenceError::Provider(format!(
                            "canonical instrument conflict for {}: {error}",
                            value.instrument_id
                        ))
                    })?;
                    reconciled_instruments += 1;
                }
            } else {
                instruments.insert(value.instrument_id.clone(), value.clone());
            }
        }
        for value in &catalog.listings {
            if listings
                .insert(value.listing_id.clone(), value.clone())
                .is_some_and(|previous| previous != *value)
            {
                conflicts.push(format!("listing:{}", value.listing_id));
            }
        }
        for value in &catalog.markets {
            if markets
                .insert(value.market_id.clone(), value.clone())
                .is_some_and(|previous| previous != *value)
            {
                conflicts.push(format!("market:{}", value.market_id));
            }
        }
        for value in &catalog.financial_products {
            if financial_products
                .insert(value.product_id.clone(), value.clone())
                .is_some_and(|previous| previous != *value)
            {
                conflicts.push(format!("financial_product:{}", value.product_id));
            }
        }
        for value in &catalog.execution_accesses {
            if execution_accesses
                .insert(value.access_id.clone(), value.clone())
                .is_some_and(|previous| previous != *value)
            {
                conflicts.push(format!("execution_access:{}", value.access_id));
            }
        }
    }
    if !conflicts.is_empty() {
        let sample = conflicts.iter().take(8).cloned().collect::<Vec<_>>();
        return Err(ReferenceError::Provider(format!(
            "providers returned {} irreconcilable canonical record conflicts (sample: {})",
            conflicts.len(),
            sample.join(", ")
        )));
    }
    if reconciled_instruments > 0 {
        tracing::info!(
            event = "reference_canonical_instruments_reconciled",
            component = "reference",
            instrument_count = reconciled_instruments,
            "shared canonical instruments were reconciled across provider listings"
        );
    }
    Ok(ProviderCatalog {
        entities: entities.into_values().collect(),
        assets: assets.into_values().collect(),
        instruments: instruments.into_values().collect(),
        listings: listings.into_values().collect(),
        markets: markets.into_values().collect(),
        financial_products: financial_products.into_values().collect(),
        execution_accesses: execution_accesses.into_values().collect(),
        market_data_accesses: Vec::new(),
    })
}

fn merge_canonical_instrument(
    previous: &mut Instrument,
    incoming: &Instrument,
) -> Result<(), String> {
    let status = canonical_instrument_status(previous.status, incoming.status);
    let mut left = previous.clone();
    let mut right = incoming.clone();
    left.source_id = None;
    right.source_id = None;
    left.status = status;
    right.status = status;
    if left != right {
        let mut fields = Vec::new();
        if left.symbol != right.symbol {
            fields.push("symbol");
        }
        if left.instrument_type != right.instrument_type {
            fields.push("instrument_type");
        }
        if left.product_family != right.product_family {
            fields.push("product_family");
        }
        if left.primary_currency_asset_id != right.primary_currency_asset_id {
            fields.push("primary_currency_asset_id");
        }
        if left.underlying_instrument_id != right.underlying_instrument_id {
            fields.push("underlying_instrument_id");
        }
        if left.expiry_unix_nanos != right.expiry_unix_nanos {
            fields.push("expiry_unix_nanos");
        }
        if left.strike != right.strike {
            fields.push("strike");
        }
        if left.option_right != right.option_right {
            fields.push("option_right");
        }
        if fields.is_empty() {
            fields.push("canonical attributes");
        }
        return Err(format!("different {}", fields.join(", ")));
    }
    *previous = left;
    Ok(())
}

fn canonical_instrument_status(
    left: kairos_domain_types::ReferenceStatus,
    right: kairos_domain_types::ReferenceStatus,
) -> kairos_domain_types::ReferenceStatus {
    use kairos_domain_types::ReferenceStatus;
    if matches!(left, ReferenceStatus::Active | ReferenceStatus::Trading)
        || matches!(right, ReferenceStatus::Active | ReferenceStatus::Trading)
    {
        ReferenceStatus::Active
    } else if left == right {
        left
    } else if left == ReferenceStatus::Unknown {
        right
    } else if right == ReferenceStatus::Unknown {
        left
    } else {
        ReferenceStatus::Inactive
    }
}

#[cfg(test)]
fn provider_catalog_uses_current_canonical_shape(catalog: &ProviderCatalog) -> bool {
    if catalog
        .entities
        .iter()
        .any(|value| value.source_id.is_some())
        || catalog.assets.iter().any(|value| value.source_id.is_some())
        || catalog
            .instruments
            .iter()
            .any(|value| value.source_id.is_some())
    {
        return false;
    }
    catalog.instruments.iter().all(|value| {
        let id = value.instrument_id.as_str();
        if let Some(base) = id.strip_prefix("instrument:spot:") {
            return value.instrument_type == "spot"
                && value.product_family.as_deref() == Some("spot")
                && value.symbol.as_str() == base
                && value
                    .primary_currency_asset_id
                    .as_ref()
                    .is_some_and(|asset| asset.as_str() == format!("asset:crypto:{base}"));
        }
        let expected = if id.starts_with("instrument:perpetual:") {
            Some(("perpetual", "instrument:perpetual:"))
        } else if id.starts_with("instrument:future:") {
            Some(("future", "instrument:future:"))
        } else if id.starts_with("instrument:option:") {
            Some(("option", "instrument:option:"))
        } else {
            None
        };
        let Some((family, prefix)) = expected else {
            return true;
        };
        let canonical_tail = id.strip_prefix(prefix).expect("prefix checked");
        let symbol = canonical_tail.replace(':', "-");
        let expiry_is_compact = family == "perpetual"
            || canonical_tail.split(':').nth(1).is_some_and(|expiry| {
                expiry.len() == 8 && expiry.chars().all(|ch| ch.is_ascii_digit())
            });
        value.instrument_type == family
            && value.product_family.as_deref() == Some(family)
            && value.symbol.as_str() == symbol
            && expiry_is_compact
    })
}

impl<S, P> CompositeSource<S, P>
where
    S: ReferenceSource,
    P: ProviderSyncStore,
{
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
        self.known_last_good.insert(source_id.to_owned());
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
        health.status = if self.known_last_good.contains(source_id) {
            "stale"
        } else {
            status
        }
        .into();
        health.consecutive_failures = health.consecutive_failures.saturating_add(1);
        health.stale = self.known_last_good.contains(source_id);
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
        let has_last_good = self.known_last_good.contains(source_id);
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
        // A replacement snapshot being assembled does not invalidate the
        // completed snapshot currently being served. Only a provider without
        // any last-known-good catalog is unavailable while its first full
        // scan is in progress.
        health.status = if has_last_good { "ready" } else { "syncing" }.into();
        health.stale = false;
        if let Some(worker) = self
            .workers
            .iter_mut()
            .find(|worker| worker.source_id == source_id)
        {
            worker.retry_after = None;
        }
    }

    fn mark_paused(&mut self, source_id: &str) {
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
        health.status = "paused".into();
        health.stale = false;
    }
}

fn unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

fn normalize_option_underlying(value: &str) -> ReferenceResult<String> {
    let value = value.trim().to_ascii_uppercase();
    if value.is_empty()
        || value.len() > 32
        || !value.bytes().all(|byte| {
            byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'.' || byte == b'-'
        })
    {
        return Err(ReferenceError::Invalid(format!(
            "invalid option underlying: {value:?}"
        )));
    }
    Ok(value)
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
            connection: provider.instrument_catalog(instrument_type),
        })
    }
}

impl ReferenceSource for OkxSource {
    fn source_id(&self) -> &str {
        &self.id
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .await
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        okx_provider_catalog(facts)
    }
}

impl BinanceSpotSource {
    pub fn new(endpoint: impl Into<String>) -> ReferenceResult<Self> {
        Ok(Self {
            connection: binance_public_connection(endpoint)?
                .instrument_catalog(BinanceInstrumentType::Spot),
        })
    }
}

impl BinanceOptionsSource {
    pub fn new(endpoint: impl Into<String>) -> ReferenceResult<Self> {
        Ok(Self {
            connection: binance_public_connection(endpoint)?
                .instrument_catalog(BinanceInstrumentType::Option),
        })
    }
}

impl BinanceEquitySource {
    pub fn new(
        endpoint: impl Into<String>,
        api_key: secrecy::SecretString,
    ) -> ReferenceResult<Self> {
        let provider = binance_public_connection(endpoint)?;
        Ok(Self {
            connection: provider.equity_instrument_catalog(api_key),
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
            connection: binance_public_connection(endpoint)?.instrument_catalog(instrument_type),
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

impl<P: ProviderSyncStore> MassiveOptionsCoverageSource<P> {
    pub async fn new(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
        sync_store: P,
    ) -> ReferenceResult<Self> {
        #[cfg(not(test))]
        if !sync_store.supports_normalized_promotion() {
            return Err(ReferenceError::Persistence(
                "production Massive options ingestion requires normalized SQLite promotion".into(),
            ));
        }
        let api_key = api_key.into();
        let base_url = base_url.into();
        let mut source = Self {
            api_key,
            base_url,
            scopes: BTreeMap::new(),
            last_good: BTreeMap::new(),
            sync_store,
            next_scope: 0,
            coverage_dirty: false,
        };
        for underlying in source
            .sync_store
            .option_underlyings("massive-options")
            .await?
        {
            source.load_scope(&underlying).await?;
        }
        Ok(source)
    }

    fn scope_key(underlying: &str) -> String {
        format!("massive-options:{underlying}")
    }

    fn make_scope(&self, underlying: &str) -> ReferenceResult<ScopedMassiveOptions> {
        let connection = massive_public_connection(self.api_key.clone(), self.base_url.clone())?
            .instrument_catalog(MassiveInstrumentQuery::options(Some(underlying.into())));
        Ok(ScopedMassiveOptions {
            connection,
            cursor: None,
            legacy_accumulated: None,
        })
    }

    async fn load_scope(&mut self, underlying: &str) -> ReferenceResult<()> {
        let underlying = normalize_option_underlying(underlying)?;
        if self.scopes.contains_key(&underlying) {
            return Ok(());
        }
        let key = Self::scope_key(&underlying);
        let (cursor, accumulated) = self
            .sync_store
            .load_state(&key)
            .await?
            .unwrap_or((None, None));
        #[cfg(test)]
        if let Some(catalog) = self.sync_store.load_last_good(&key).await? {
            self.last_good.insert(underlying.clone(), catalog);
        }
        let mut scope = self.make_scope(&underlying)?;
        scope.cursor = cursor;
        scope.legacy_accumulated = accumulated;
        self.scopes.insert(underlying, scope);
        Ok(())
    }

    fn merged_last_good(&self) -> ReferenceResult<ProviderCatalog> {
        merge_provider_catalog_views(self.last_good.values())
    }

    async fn advance_one_scope(
        &mut self,
        underlying: &str,
    ) -> ReferenceResult<Option<(ProviderCatalog, bool)>> {
        let key = Self::scope_key(underlying);
        let (legacy, cursor) = {
            let scope = self
                .scopes
                .get_mut(underlying)
                .expect("enabled coverage scope is present");
            (scope.legacy_accumulated.take(), scope.cursor.clone())
        };
        if let Some(legacy) = legacy {
            self.sync_store
                .append_staged_page(&key, cursor.as_deref(), &legacy)
                .await?;
        }
        let page = {
            let scope = self
                .scopes
                .get_mut(underlying)
                .expect("enabled coverage scope is present");
            tokio::time::timeout(
                MASSIVE_PAGE_TIMEOUT,
                scope
                    .connection
                    .fetch_instruments_page(cursor.as_deref(), 1000),
            )
            .await
            .map_err(|error| {
                ReferenceError::Provider(format!("Massive {underlying} page timed out: {error}"))
            })?
            .map_err(|error| ReferenceError::Provider(error.to_string()))?
        };
        // The REST endpoint's `expired=false` filter excludes expired
        // contracts, while provider `active` is the remaining tradability
        // fact. Coverage snapshots intentionally contain only currently
        // tradable contracts; a later completed scope reconcile removes a
        // contract that has become inactive.
        let mut facts = page.catalog;
        facts.instruments.retain(|instrument| instrument.active);
        let page_catalog = massive_provider_catalog(facts)?;
        let next_cursor = if page.complete {
            None
        } else {
            page.next_cursor
        };
        self.sync_store
            .append_staged_page(&key, next_cursor.as_deref(), &page_catalog)
            .await?;
        self.scopes
            .get_mut(underlying)
            .expect("enabled coverage scope is present")
            .cursor = next_cursor;
        if !page.complete {
            return Ok(None);
        }
        let normalized = self.sync_store.supports_normalized_promotion();
        let catalog = if normalized {
            self.sync_store.promote_staged(&key).await?;
            ProviderCatalog::default()
        } else {
            let catalog = self
                .sync_store
                .staged_pages(&key)
                .await?
                .into_iter()
                .fold(None, |merged, page| {
                    Some(merge_provider_catalog(merged, page))
                })
                .unwrap_or_default();
            self.sync_store.clear_staged_pages(&key).await?;
            self.sync_store.save_last_good(&key, &catalog).await?;
            catalog
        };
        self.scopes
            .get_mut(underlying)
            .expect("enabled coverage scope is present")
            .cursor = None;
        if normalized {
            Ok(Some((ProviderCatalog::default(), true)))
        } else {
            self.last_good.insert(underlying.into(), catalog);
            Ok(Some((self.merged_last_good()?, false)))
        }
    }
}

impl<P: ProviderSyncStore> MassiveEquitySource<P> {
    fn without_state(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
        sync_store: P,
    ) -> ReferenceResult<Self> {
        let connection = massive_public_connection(api_key, base_url)?
            .instrument_catalog(MassiveInstrumentQuery::equities());
        Ok(Self {
            connection,
            cursor: None,
            accumulated: None,
            sync_store,
        })
    }

    pub async fn new_with_sync_store(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
        mut sync_store: P,
    ) -> ReferenceResult<Self> {
        #[cfg(not(test))]
        if !sync_store.supports_normalized_promotion() {
            return Err(ReferenceError::Persistence(
                "production Massive equity ingestion requires normalized SQLite promotion".into(),
            ));
        }
        let (cursor, accumulated) = sync_store
            .load_state("massive-equity")
            .await?
            .unwrap_or((None, None));
        let mut source = Self::without_state(api_key, base_url, sync_store)?;
        source.cursor = cursor;
        source.accumulated = accumulated;
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
        let connection = provider.instrument_catalog();
        Ok(Self { connection })
    }
}

impl ReferenceSource for BinanceSpotSource {
    fn source_id(&self) -> &str {
        "binance-spot"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .await
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        binance_provider_catalog(facts, BinanceInstrumentType::Spot)
    }
}

impl ReferenceSource for BinanceOptionsSource {
    fn source_id(&self) -> &str {
        "binance-options"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .await
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        binance_provider_catalog(facts, BinanceInstrumentType::Option)
    }
}

impl ReferenceSource for BinanceDerivativesSource {
    fn source_id(&self) -> &str {
        self.id
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .await
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        binance_provider_catalog(facts, self.instrument_type)
    }
}

impl ReferenceSource for BinanceEquitySource {
    fn source_id(&self) -> &str {
        "binance-equity"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .await
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        binance_equity_provider_catalog(facts)
    }
}

impl<P> ReferenceSource for MassiveOptionsCoverageSource<P>
where
    P: ProviderSyncStore,
{
    fn source_id(&self) -> &str {
        "massive-options"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        Ok(self.fetch_catalog_step().await?.catalog)
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<ProviderUpdate> {
        let result = async {
            if self.coverage_dirty {
                self.coverage_dirty = false;
                let normalized = self.sync_store.supports_normalized_promotion();
                return Ok(ProviderUpdate {
                    catalog: if normalized {
                        ProviderCatalog::default()
                    } else {
                        self.merged_last_good()?
                    },
                    complete: true,
                    page_count: 0,
                    facts_persisted: normalized,
                });
            }
            if self.scopes.is_empty() {
                let normalized = self.sync_store.supports_normalized_promotion();
                return Ok(ProviderUpdate {
                    catalog: ProviderCatalog::default(),
                    complete: true,
                    page_count: 0,
                    facts_persisted: normalized,
                });
            }
            let scope_count = self.scopes.len();
            let index = self.next_scope % scope_count;
            self.next_scope = (self.next_scope + 1) % scope_count;
            let underlying = self
                .scopes
                .keys()
                .nth(index)
                .cloned()
                .expect("scope count was non-zero");
            match self.advance_one_scope(&underlying).await? {
                Some((catalog, facts_persisted)) => Ok(ProviderUpdate {
                    catalog,
                    complete: true,
                    page_count: 1,
                    facts_persisted,
                }),
                None if self.last_good.is_empty() => Ok(ProviderUpdate {
                    catalog: ProviderCatalog::default(),
                    complete: false,
                    page_count: 1,
                    facts_persisted: false,
                }),
                None => Ok(ProviderUpdate {
                    // A replacement scope is still paging, but a completed
                    // scoped snapshot exists. Keep it authoritative until that
                    // one underlying finishes rather than degrading the entire
                    // Massive provider.
                    catalog: self.merged_last_good()?,
                    complete: true,
                    page_count: 1,
                    facts_persisted: false,
                }),
            }
        }
        .await;
        #[cfg(not(test))]
        self.last_good.clear();
        result
    }

    async fn set_option_underlying(
        &mut self,
        underlying: &str,
        enabled: bool,
    ) -> ReferenceResult<()> {
        let underlying = normalize_option_underlying(underlying)?;
        self.sync_store
            .set_option_underlying("massive-options", &underlying, enabled)
            .await?;
        if enabled {
            self.load_scope(&underlying).await?;
        } else {
            self.sync_store
                .remove_last_good(&Self::scope_key(&underlying))
                .await?;
            self.scopes.remove(&underlying);
            self.last_good.remove(&underlying);
            self.next_scope = 0;
            self.coverage_dirty = true;
        }
        Ok(())
    }

    fn option_underlyings(&self) -> Vec<String> {
        self.scopes.keys().cloned().collect()
    }
}

impl<P: ProviderSyncStore> ReferenceSource for MassiveEquitySource<P> {
    fn source_id(&self) -> &str {
        "massive-equity"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .await
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        massive_provider_catalog(facts)
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<ProviderUpdate> {
        if self.accumulated.is_none() {
            if let Some((cursor, accumulated)) =
                self.sync_store.load_state("massive-equity").await?
            {
                self.cursor = cursor;
                self.accumulated = accumulated;
            }
        }
        if let Some(legacy_catalog) = self.accumulated.take() {
            self.sync_store
                .append_staged_page("massive-equity", self.cursor.as_deref(), &legacy_catalog)
                .await?;
        }
        let mut cursor = self.cursor.clone();
        let mut complete = false;
        let mut page_count = 0;
        for _ in 0..MASSIVE_PAGES_PER_REFRESH {
            let page = match tokio::time::timeout(
                MASSIVE_PAGE_TIMEOUT,
                self.connection
                    .fetch_instruments_page(cursor.as_deref(), 1000),
            )
            .await
            {
                Ok(result) => {
                    result.map_err(|error| ReferenceError::Provider(error.to_string()))?
                }
                Err(_) => {
                    tracing::warn!(
                        event = "reference_massive_page_timeout",
                        component = "reference",
                        provider = "massive-equity",
                        page_count,
                        "Massive page budget expired; persisted cursor will resume on the next refresh"
                    );
                    break;
                }
            };
            page_count += 1;
            cursor = page.next_cursor;
            let page_catalog = massive_provider_catalog(page.catalog)?;
            complete = page.complete;
            let next_cursor = if complete { None } else { cursor.clone() };
            self.sync_store
                .append_staged_page("massive-equity", next_cursor.as_deref(), &page_catalog)
                .await?;
            self.cursor = next_cursor;
            if complete {
                break;
            }
        }
        let mut facts_persisted = false;
        let result_catalog = if complete {
            self.cursor = None;
            if self.sync_store.supports_normalized_promotion() {
                self.sync_store.promote_staged("massive-equity").await?;
                facts_persisted = true;
                ProviderCatalog::default()
            } else {
                let catalog = self
                    .sync_store
                    .staged_pages("massive-equity")
                    .await?
                    .into_iter()
                    .fold(None, |merged, page| {
                        Some(merge_provider_catalog(merged, page))
                    })
                    .unwrap_or_default();
                self.sync_store.clear_staged_pages("massive-equity").await?;
                catalog
            }
        } else {
            self.cursor = cursor;
            self.accumulated = None;
            ProviderCatalog::default()
        };
        Ok(ProviderUpdate {
            catalog: result_catalog,
            complete,
            page_count,
            facts_persisted,
        })
    }
}

impl ReferenceSource for HyperliquidSource {
    fn source_id(&self) -> &str {
        "hyperliquid"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .await
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
            symbol: kairos_domain_types::Symbol::new(format!("{base}-{quote}"))?,
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
            instrument_id: instrument_id.clone(),
            listing_id: listing_id.clone(),
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
    reconcile_canonical_instruments(&mut catalog.instruments)?;
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
    let (family, canonical_family, instrument_id, symbol, underlying_id) = match value.kind {
        ExternalInstrumentKind::Equity => {
            let ticker = source_symbol.clone();
            ensure_massive_underlying(catalog, &ticker, &quote, &exchange_id, status)?;
            (
                "equity",
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
                "option",
                format!("instrument:option:{underlying}:{expiry}:{strike}:{right}"),
                format!("{underlying}-{expiry}-{strike}-{right}"),
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
        instrument_type: canonical_family.into(),
        product_family: Some(canonical_family.into()),
        issuer_id: (family == "equity").then(|| {
            kairos_domain_types::IssuerId::new(format!("issuer:US:{source_symbol}"))
                .expect("validated Massive issuer")
        }),
        share_class: (family == "equity").then(|| "common".into()),
        primary_currency_asset_id: Some(kairos_domain_types::AssetId::new(format!(
            "asset:fiat:{quote}"
        ))?),
        underlying_instrument_id: underlying_id,
        expiry_unix_nanos: matches!(
            value.kind,
            ExternalInstrumentKind::Future | ExternalInstrumentKind::Option
        )
        .then_some(value.expiry_unix_nanos)
        .flatten(),
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
        effective_to_unix_nanos: value.expiry_unix_nanos,
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

fn binance_equity_provider_catalog(
    facts: ExternalInstrumentCatalog,
) -> ReferenceResult<ProviderCatalog> {
    if facts.participant.id.as_str() != "binance"
        || facts.participant.kind
            != kairos_integration::application::capabilities::ParticipantKind::Broker
    {
        return Err(ReferenceError::Provider(
            "Binance Equity source requires the Binance broker participant".into(),
        ));
    }
    let exchange_id = kairos_domain_types::Exchange::new("exchange:binance")?;
    let mut catalog = ProviderCatalog {
        entities: vec![Entity {
            entity_id: exchange_id.to_string(),
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
        let symbol = value.source_symbol.as_str().trim().to_ascii_uppercase();
        let status: kairos_domain_types::ReferenceStatus =
            if value.active { "active" } else { "inactive" }.into();
        let equity_asset = kairos_domain_types::AssetId::new(format!("asset:equity:{symbol}"))?;
        let instrument_id = kairos_domain_types::InstrumentId::new(format!(
            "instrument:equity:US:{symbol}:common"
        ))?;
        let listing_id =
            kairos_domain_types::ListingId::new(format!("listing:binance:equity:{symbol}"))?;
        let market_id =
            kairos_domain_types::MarketId::new(format!("market:binance:equity:{symbol}"))?;
        catalog.assets.push(Asset {
            asset_id: equity_asset.clone(),
            code: symbol.clone(),
            asset_class: "equity".into(),
            status,
            ..Asset::default()
        });
        catalog.instruments.push(Instrument {
            instrument_id: instrument_id.clone(),
            symbol: kairos_domain_types::Symbol::new(symbol.clone())?,
            instrument_type: "equity".into(),
            product_family: Some("equity".into()),
            issuer_id: Some(kairos_domain_types::IssuerId::new(format!(
                "issuer:US:{symbol}"
            ))?),
            share_class: Some("common".into()),
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
            instrument_id: instrument_id.clone(),
            listing_id: listing_id.clone(),
            exchange_id: exchange_id.clone(),
            market_type: "equity".into(),
            asset_type: Some("equity".into()),
            source_symbol: kairos_domain_types::Symbol::new(symbol.clone())?,
            base_asset_id: Some(equity_asset),
            status,
            quantity_tick: value.quantity_tick,
            quantity_precision: value.quantity_precision.unwrap_or_default() as i32,
            minimum_quantity: value.minimum_quantity,
            minimum_notional: value.minimum_notional,
            effective_from_unix_nanos: 0.into(),
            ..Market::default()
        });
        catalog.execution_accesses.push(ExecutionAccess {
            access_id: kairos_domain_types::ExecutionAccessId::new(format!(
                "execution-access:binance:equity:{symbol}"
            ))?,
            routing_mode: "direct".into(),
            instrument_id: Some(instrument_id),
            listing_id: Some(listing_id),
            market_id: Some(market_id),
            destination_market_id: None,
            broker_id: None,
            provider_id: "binance".into(),
            product_family: "equity".into(),
            provider_symbol: value.source_symbol,
            settlement_asset_id: None,
            status,
            effective_from_unix_nanos: 0.into(),
            effective_to_unix_nanos: None,
            source_id: None,
        });
    }
    catalog.validate()?;
    Ok(catalog)
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
    reconcile_canonical_instruments(&mut catalog.instruments)?;
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
    for (code, asset_class) in [
        (
            &base,
            if value.kind == ExternalInstrumentKind::EquityPerpetual {
                "equity"
            } else {
                "crypto"
            },
        ),
        (&quote, "crypto"),
    ] {
        catalog.assets.push(Asset {
            asset_id: kairos_domain_types::AssetId::new(format!("asset:{asset_class}:{code}"))?,
            code: code.clone(),
            asset_class: asset_class.into(),
            status: "active".into(),
            ..Asset::default()
        });
    }
    let (family, canonical_family, instrument_id, canonical_symbol, underlying_instrument_id) =
        match value.kind {
            ExternalInstrumentKind::Spot if instrument_type == BinanceInstrumentType::Spot => (
                "spot",
                "spot",
                format!("instrument:spot:{base}"),
                base.clone(),
                None,
            ),
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
                    "perpetual",
                    format!("instrument:perpetual:{base}-{quote}"),
                    format!("{base}-{quote}"),
                    None,
                )
            }
            ExternalInstrumentKind::EquityPerpetual
                if instrument_type == BinanceInstrumentType::UsdMFutures =>
            {
                let underlying = kairos_domain_types::InstrumentId::new(format!(
                    "instrument:equity:US:{base}:common"
                ))?;
                if !catalog
                    .instruments
                    .iter()
                    .any(|value| value.instrument_id == underlying)
                {
                    catalog.instruments.push(Instrument {
                        instrument_id: underlying.clone(),
                        symbol: kairos_domain_types::Symbol::new(base.clone())?,
                        instrument_type: "equity".into(),
                        product_family: Some("equity".into()),
                        issuer_id: Some(kairos_domain_types::IssuerId::new(format!(
                            "issuer:US:{base}"
                        ))?),
                        share_class: Some("common".into()),
                        status: "active".into(),
                        ..Instrument::default()
                    });
                }
                (
                    "usd-m-futures",
                    "perpetual",
                    format!("instrument:perpetual:equity:US:{base}:{quote}"),
                    format!("{base}-{quote}"),
                    Some(underlying),
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
                    "future",
                    format!("instrument:future:{base}-{quote}:{expiry}"),
                    format!("{base}-{quote}-{expiry}"),
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
                        primary_currency_asset_id: Some(kairos_domain_types::AssetId::new(
                            format!("asset:crypto:{base}"),
                        )?),
                        status: "active".into(),
                        ..Instrument::default()
                    });
                }
                (
                    "options",
                    "option",
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
                    format!(
                        "{base}-{quote}-{expiry}-{strike}-{}",
                        match right.to_ascii_lowercase().as_str() {
                            "call" | "c" => "C",
                            "put" | "p" => "P",
                            _ => unreachable!("option right validated above"),
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
        symbol: kairos_domain_types::Symbol::new(canonical_symbol)?,
        instrument_type: canonical_family.into(),
        product_family: Some(canonical_family.into()),
        primary_currency_asset_id: Some(kairos_domain_types::AssetId::new(format!(
            "asset:crypto:{}",
            if canonical_family == "spot" {
                &base
            } else {
                &quote
            }
        ))?),
        underlying_instrument_id: underlying_instrument_id.clone(),
        expiry_unix_nanos: matches!(
            value.kind,
            ExternalInstrumentKind::Future | ExternalInstrumentKind::Option
        )
        .then_some(value.expiry_unix_nanos)
        .flatten(),
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
        effective_to_unix_nanos: value.expiry_unix_nanos,
        ..Listing::default()
    });
    catalog.markets.push(Market {
        market_id,
        market_key: format!("binance.{family}.{source_symbol}"),
        instrument_id,
        listing_id,
        exchange_id,
        market_type: family.into(),
        asset_type: Some(
            if value.kind == ExternalInstrumentKind::EquityPerpetual {
                "equity"
            } else {
                "crypto"
            }
            .into(),
        ),
        source_symbol: kairos_domain_types::Symbol::new(source_symbol)?,
        base_asset_id: Some(kairos_domain_types::AssetId::new(format!(
            "asset:{}:{base}",
            if value.kind == ExternalInstrumentKind::EquityPerpetual {
                "equity"
            } else {
                "crypto"
            }
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
        underlying_instrument_id,
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
    reconcile_canonical_instruments(&mut catalog.instruments)?;
    catalog.validate()?;
    Ok(catalog)
}

fn append_okx_instrument(
    catalog: &mut ProviderCatalog,
    value: ExternalInstrument,
) -> ReferenceResult<()> {
    let source_symbol = value.source_symbol.as_str().to_ascii_uppercase();
    let (base, quote) = okx_base_quote(&value)?;
    let (family, canonical_family, instrument_id, canonical_symbol, underlying_instrument_id) =
        match value.kind {
            ExternalInstrumentKind::Equity | ExternalInstrumentKind::EquityPerpetual => {
                return Err(ReferenceError::Provider(
                    "OKX catalog cannot contain Binance equity instrument kinds".into(),
                ))
            }
            ExternalInstrumentKind::Spot => (
                "spot",
                "spot",
                format!("instrument:spot:{base}"),
                base.clone(),
                None,
            ),
            ExternalInstrumentKind::Margin => (
                "margin",
                "margin",
                format!("instrument:margin:{base}-{quote}"),
                format!("{base}-{quote}"),
                None,
            ),
            ExternalInstrumentKind::Perpetual => (
                "swap",
                "perpetual",
                format!("instrument:perpetual:{base}-{quote}"),
                format!("{base}-{quote}"),
                None,
            ),
            ExternalInstrumentKind::Future => {
                let expiry = canonical_expiry(value.expiry_unix_nanos)?;
                (
                    "futures",
                    "future",
                    format!("instrument:future:{base}-{quote}:{expiry}"),
                    format!("{base}-{quote}-{expiry}"),
                    None,
                )
            }
            ExternalInstrumentKind::Option => {
                let expiry = canonical_expiry(value.expiry_unix_nanos)?;
                let strike = value.strike.as_deref().ok_or_else(|| {
                    ReferenceError::Provider("OKX option strike is missing".into())
                })?;
                let right = value.option_right.as_deref().ok_or_else(|| {
                    ReferenceError::Provider("OKX option right is missing".into())
                })?;
                let canonical_right = right.to_ascii_uppercase();
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
                        primary_currency_asset_id: Some(kairos_domain_types::AssetId::new(
                            format!("asset:crypto:{base}"),
                        )?),
                        status: "active".into(),
                        ..Instrument::default()
                    });
                }
                (
                    "options",
                    "option",
                    format!(
                        "instrument:option:{base}-{quote}:{expiry}:{strike}:{}",
                        canonical_right
                    ),
                    format!("{base}-{quote}-{expiry}-{strike}-{canonical_right}"),
                    Some(underlying),
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
        symbol: kairos_domain_types::Symbol::new(canonical_symbol)?,
        instrument_type: canonical_family.into(),
        product_family: Some(canonical_family.into()),
        primary_currency_asset_id: Some(kairos_domain_types::AssetId::new(format!(
            "asset:crypto:{}",
            if canonical_family == "spot" {
                &base
            } else {
                &quote
            }
        ))?),
        underlying_instrument_id,
        expiry_unix_nanos: matches!(
            value.kind,
            ExternalInstrumentKind::Future | ExternalInstrumentKind::Option
        )
        .then_some(value.expiry_unix_nanos)
        .flatten(),
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
        effective_to_unix_nanos: value.expiry_unix_nanos,
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
        effective_to_unix_nanos: value.expiry_unix_nanos,
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
    Ok(date.format("%Y%m%d").to_string())
}

fn merge_provider_catalog(
    previous: Option<ProviderCatalog>,
    incoming: ProviderCatalog,
) -> ProviderCatalog {
    let previous = previous.unwrap_or_default();
    ProviderCatalog {
        entities: merge_records(previous.entities, incoming.entities, |value| {
            value.entity_id.clone()
        }),
        assets: merge_records(previous.assets, incoming.assets, |value| {
            value.asset_id.clone()
        }),
        instruments: merge_records(previous.instruments, incoming.instruments, |value| {
            value.instrument_id.clone()
        }),
        listings: merge_records(previous.listings, incoming.listings, |value| {
            value.listing_id.clone()
        }),
        markets: merge_records(previous.markets, incoming.markets, |value| {
            value.market_id.clone()
        }),
        financial_products: merge_records(
            previous.financial_products,
            incoming.financial_products,
            |value| value.product_id.clone(),
        ),
        execution_accesses: merge_records(
            previous.execution_accesses,
            incoming.execution_accesses,
            |value| value.access_id.clone(),
        ),
        market_data_accesses: merge_records(
            previous.market_data_accesses,
            incoming.market_data_accesses,
            |value| value.access_id.clone(),
        ),
    }
}

fn merge_records<T, K>(previous: Vec<T>, incoming: Vec<T>, key: impl Fn(&T) -> K) -> Vec<T>
where
    K: Ord,
{
    let mut merged = BTreeMap::new();
    for value in previous {
        merged.insert(key(&value), value);
    }
    for value in incoming {
        merged.insert(key(&value), value);
    }
    merged.into_values().collect()
}

#[cfg(test)]
mod tests {
    use super::{
        binance_equity_provider_catalog, binance_provider_catalog, hyperliquid_provider_catalog,
        massive_provider_catalog, okx_provider_catalog,
        provider_catalog_uses_current_canonical_shape, BinanceSpotSource, CompositeSource,
        HyperliquidSource, MassiveEquitySource, MassiveOptionsCoverageSource, OkxSource,
        ReferenceSource,
    };
    use crate::domain::{Entity, Instrument, Market, ProviderCatalog, ReferenceResult};
    use crate::services::actor::ReferenceActor;
    use crate::services::sqlx_storage::{SqlxCatalogStore, SqlxProviderSyncStore};
    use crate::services::store::ProviderSyncStore;
    use kairos_domain_types::{Currency, InstrumentId, MarketId, ProviderSymbol, Symbol};
    use kairos_integration::application::capabilities::reference::{
        ExternalInstrument, ExternalInstrumentCatalog, ExternalInstrumentKind,
    };
    use kairos_integration::application::{ParticipantKind, ParticipantRef};
    use kairos_integration::participants::binance::InstrumentType as BinanceInstrumentType;
    use kairos_integration::participants::okx::InstrumentType as OkxInstrumentType;
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
        let mut composite = CompositeSource::new_with_sync_store(
            vec![TestProviderSource::from(source)],
            Some(store),
        )
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
        let mut actor = ReferenceActor::new("reference-test", composite, catalog_store)
            .await
            .unwrap();

        let result = actor.refresh().await.unwrap();
        assert!(result.changed);
        assert_eq!(result.generation.get(), 1);
        assert_eq!(result.event_sequence.get(), 1);
        assert!(result.events.is_empty());
        let reader = kairos_reference_contract::ReferenceSqliteReader::open(&path).unwrap();
        assert!(reader.record("provider:a").unwrap().is_some());
    }

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

    impl ReferenceSource for FixedSource {
        fn source_id(&self) -> &str {
            self.id
        }

        async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
            Ok(self.catalog.clone())
        }
    }

    impl ReferenceSource for CountingSource {
        fn source_id(&self) -> &str {
            self.id
        }

        async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            Ok(self.catalog.clone())
        }
    }

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
            .any(|value| value.market_id == "market:okx:swap:BTC-USDT-SWAP"));
        assert!(catalog
            .markets
            .iter()
            .all(|value| value.exchange_id == "exchange:okx"));
    }

    #[test]
    fn spot_listing_expiry_does_not_split_or_mutate_the_canonical_instrument() {
        let first_expiry = kairos_domain_types::UnixNanos::new(1_786_694_400_000_000_000);
        let second_expiry = kairos_domain_types::UnixNanos::new(1_786_953_600_000_000_000);
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
            "instrument:future:BTC-USDT:20260626"
        );
        assert_eq!(
            catalog.markets[0].market_id,
            "market:binance:usd-m-futures:BTCUSDT_260626"
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
            BinanceInstrumentType::UsdMFutures,
        )
        .unwrap();
        let market = &catalog.markets[0];
        assert_eq!(market.market_id, "market:binance:usd-m-futures:AAPLUSDT");
        assert_eq!(market.asset_type.as_deref(), Some("equity"));
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
    fn binance_equity_catalog_builds_broker_execution_access() {
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
        assert_eq!(catalog.markets[0].market_id, "market:binance:equity:AAPL");
        assert_eq!(catalog.markets[0].asset_type.as_deref(), Some("equity"));
        assert_eq!(catalog.execution_accesses[0].provider_id, "binance");
        assert_eq!(catalog.execution_accesses[0].provider_symbol, "AAPL");
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
            value.instrument_id == "instrument:option:SPY:20270115:500:C"
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
        assert_eq!(first.markets[0].source_id.as_deref(), Some("test-flaky"));
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
                            market_id: typed_market_id("market:massive"),
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
            instrument_type: "spot".into(),
            product_family: Some("spot".into()),
            primary_currency_asset_id: Some(
                kairos_domain_types::AssetId::new("asset:crypto:BTC").unwrap(),
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

    #[test]
    fn obsolete_provider_snapshot_shape_is_not_eligible_for_fallback() {
        let canonical = Instrument {
            instrument_id: InstrumentId::new("instrument:spot:BTC").unwrap(),
            symbol: Symbol::new("BTC").unwrap(),
            instrument_type: "spot".into(),
            product_family: Some("spot".into()),
            primary_currency_asset_id: Some(
                kairos_domain_types::AssetId::new("asset:crypto:BTC").unwrap(),
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
            Some(kairos_domain_types::AssetId::new("asset:crypto:USDT").unwrap());
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
            assert!(String::from_utf8_lossy(&request[..length])
                .starts_with("GET /api/v3/exchangeInfo "));
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
        let mut source = OkxSource::new(
            "okx-swap",
            OkxInstrumentType::Swap,
            format!("http://{address}"),
        )
        .expect("build OKX source");

        let catalog = source.fetch_catalog().await.unwrap();
        server.join().unwrap();

        assert_eq!(catalog.markets.len(), 1);
        assert_eq!(
            catalog.markets[0].market_id,
            "market:okx:swap:BTC-USDT-SWAP"
        );
        assert_eq!(catalog.markets[0].price_tick.as_deref(), Some("0.1"));
        assert_eq!(catalog.markets[0].contract_size.as_deref(), Some("0.01"));
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
        let mut source = MassiveEquitySource::new_with_sync_store(
            "test-key",
            format!("http://{address}"),
            store,
        )
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
            .flat_map(|catalog| catalog.markets)
            .any(|market| market.source_symbol == "AAPL"));
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
            .markets
            .iter()
            .any(|market| market.source_symbol == "O:SPY260821C00500000"));
        assert!(!completed
            .catalog
            .markets
            .iter()
            .any(|market| market.source_symbol == "O:SPY260821P00400000"));

        source.set_option_underlying("SPY", false).await.unwrap();
        let removed = source.fetch_catalog_step().await.unwrap();
        assert!(removed.complete);
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
        let mut first = MassiveEquitySource::new_with_sync_store(
            "test-key",
            format!("http://{address}"),
            store,
        )
        .await
        .unwrap();

        let partial = first.fetch_catalog_step().await.unwrap();
        assert!(!partial.complete);
        assert_eq!(partial.page_count, 1);
        assert!(partial.catalog.markets.is_empty());
        drop(first);

        let store = SqlxProviderSyncStore::open_legacy(&path).await.unwrap();
        let mut resumed = MassiveEquitySource::new_with_sync_store(
            "test-key",
            format!("http://{address}"),
            store,
        )
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
        assert_eq!(complete.catalog.markets.len(), 9);
        assert!(complete
            .catalog
            .markets
            .iter()
            .any(|market| market.source_symbol == "TEST0"));
        assert!(complete
            .catalog
            .markets
            .iter()
            .any(|market| market.source_symbol == "TEST8"));
    }

    #[tokio::test]
    async fn partial_provider_pages_do_not_drop_previous_page() {
        let mut source =
            CompositeSource::new(vec![TestProviderSource::from(PagedSource { calls: 0 })])
                .await
                .unwrap();
        let first_error = source.fetch_catalog().await.unwrap_err().to_string();
        assert!(first_error.contains("without a last-known-good snapshot"));
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
        let mut source =
            CompositeSource::new(vec![TestProviderSource::from(RefreshingPagedSource {
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
}
