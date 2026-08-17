//! Provider fan-in, last-known-good retention, health, and runtime control.

use super::*;

pub(super) fn merge_provider_catalog_views<'a>(
    catalogs: impl IntoIterator<Item = &'a ProviderCatalog>,
) -> ReferenceResult<ProviderCatalog> {
    let mut entities = BTreeMap::new();
    let mut assets = BTreeMap::new();
    let mut instruments = BTreeMap::new();
    let mut listings = BTreeMap::new();
    let mut markets = BTreeMap::new();
    let mut execution_accesses = BTreeMap::new();
    let mut market_data_accesses = BTreeMap::new();
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
                    crate::domain::merge_instrument(previous, value).map_err(|error| {
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
        for value in &catalog.execution_accesses {
            if execution_accesses
                .insert(value.access_id.clone(), value.clone())
                .is_some_and(|previous| previous != *value)
            {
                conflicts.push(format!("execution_access:{}", value.access_id));
            }
        }
        for value in &catalog.market_data_accesses {
            if market_data_accesses
                .insert(value.access_id.clone(), value.clone())
                .is_some_and(|previous| previous != *value)
            {
                conflicts.push(format!("market_data_access:{}", value.access_id));
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
        execution_accesses: execution_accesses.into_values().collect(),
        market_data_accesses: market_data_accesses.into_values().collect(),
    })
}
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

#[async_trait::async_trait]
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

/// Reference-owned fan-in for the global catalog. Each provider remains an
/// independent integration connection; this source only merges normalized
/// records before they enter the Reference actor.
pub struct CompositeSource<S> {
    workers: Vec<ProviderWorker<S>>,
    #[cfg(test)]
    last_good: BTreeMap<String, ProviderCatalog>,
    known_last_good: BTreeSet<String>,
    health: BTreeMap<String, ProviderHealth>,
    sync_store: Option<SqlxProviderSyncStore>,
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
pub(super) const MASSIVE_PAGES_PER_REFRESH: usize = 1;
pub(super) const MASSIVE_PAGE_TIMEOUT: Duration = Duration::from_secs(20);

impl<S> CompositeSource<S>
where
    S: ReferenceSource,
{
    pub async fn new_with_sync_store(
        sources: Vec<S>,
        mut sync_store: Option<SqlxProviderSyncStore>,
    ) -> ReferenceResult<Self> {
        if sources.is_empty() {
            return Err(ReferenceError::Provider(
                "reference catalog has no sources".into(),
            ));
        }
        #[cfg(not(test))]
        if !sync_store
            .as_ref()
            .is_some_and(SqlxProviderSyncStore::supports_normalized_promotion)
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
                    let has_last_good = self
                        .sync_store
                        .as_mut()
                        .expect("store checked")
                        .has_last_good(&source_id)
                        .await?;
                    self.mark_failure(&source_id, has_last_good);
                    let (record_kind, record_id) = error.record_identity().unwrap_or(("", ""));
                    tracing::warn!(
                        event = if has_last_good { "reference_provider_degraded" } else { "reference_provider_unavailable" },
                        component = "reference",
                        provider = %source_id,
                        error_code = error.code(),
                        retryable = error.retryable(),
                        record_kind,
                        record_id,
                        fallback = if has_last_good { "last_known_good" } else { "none" },
                        error = %error,
                        "reference provider refresh failed"
                    );
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
impl<S: ReferenceSource> CompositeSource<S> {
    pub async fn new(sources: Vec<S>) -> ReferenceResult<Self> {
        Self::new_with_sync_store(sources, None).await
    }
}

#[async_trait::async_trait]
impl<S> ReferenceSource for CompositeSource<S>
where
    S: ReferenceSource,
{
    fn normalized_facts_authoritative(&self) -> bool {
        self.sync_store
            .as_ref()
            .is_some_and(SqlxProviderSyncStore::supports_normalized_promotion)
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
                        self.mark_failure(&source_id, self.last_good.contains_key(&source_id));
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
                            crate::domain::merge_instrument(previous, value).map_err(|error| {
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
                    let has_last_good = self
                        .sync_store
                        .as_mut()
                        .expect("normalized source has a store")
                        .has_last_good(source_id)
                        .await?;
                    self.mark_failure(source_id, has_last_good);
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
                    self.mark_failure(source_id, self.last_good.contains_key(source_id));
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
    for value in &mut catalog.execution_accesses {
        value.source_id = Some(source_id.clone());
    }
    for value in &mut catalog.market_data_accesses {
        value.source_id = Some(source_id.clone());
    }
}

#[cfg(test)]
pub(super) fn provider_catalog_uses_current_canonical_shape(catalog: &ProviderCatalog) -> bool {
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
            return value.instrument_type == InstrumentKind::Spot
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
        value.instrument_type.as_str() == family
            && value.symbol.as_str() == symbol
            && expiry_is_compact
    })
}

impl<S> CompositeSource<S>
where
    S: ReferenceSource,
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

    fn mark_failure(&mut self, source_id: &str, has_last_good: bool) {
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
        if has_last_good {
            self.known_last_good.insert(source_id.to_owned());
        }
        health.status = if has_last_good {
            "stale"
        } else {
            "unavailable"
        }
        .into();
        health.consecutive_failures = health.consecutive_failures.saturating_add(1);
        health.stale = has_last_good;
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
