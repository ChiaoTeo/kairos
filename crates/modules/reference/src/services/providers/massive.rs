//! Massive Reference sources, scoped discovery, and canonical mapping.

use std::collections::BTreeSet;

use kairos_primitives::reference::ReferenceSourceId;

use super::*;
use crate::logging::events as log_events;
use crate::services::sources::SourceChanges;

/// Massive stock-options discovery limited to explicitly managed underlyings.
///
/// The provider has a very large global option universe. Reference therefore
/// treats coverage as operational input and only promotes contracts belonging
/// to enabled underlyings. Market-data WebSocket observations may later add a
/// single underlying/contract to this input, but they never make a global
/// catalog scan authoritative.
pub struct MassiveOptionsCoverageSource {
    api_key: String,
    base_url: String,
    environment: String,
    scopes: BTreeMap<String, ScopedMassiveOptions>,
    last_good: BTreeMap<String, ProviderCatalog>,
    sync_store: SqlxProviderSyncStore,
    next_scope: usize,
    coverage_dirty: bool,
    removed_scans: BTreeSet<ReferenceSourceId>,
}

struct ScopedMassiveOptions {
    connection: ConnectionRef,
    cursor: Option<String>,
    pages_done: u64,
    legacy_accumulated: Option<ProviderCatalog>,
}

struct MassiveScopeAdvanceResult {
    catalog: ProviderCatalog,
    staged_changes: Option<SourceChanges>,
    pages_done: u64,
    records_seen: u64,
    records_changed: Option<u64>,
}

enum MassiveScopeAdvance {
    Complete(MassiveScopeAdvanceResult),
    InProgress { pages_done: u64, records_seen: u64 },
}

pub struct MassiveEquitySource {
    connection: ConnectionRef,
    cursor: Option<String>,
    pages_done: u64,
    accumulated: Option<ProviderCatalog>,
    sync_store: SqlxProviderSyncStore,
}
impl MassiveOptionsCoverageSource {
    pub(crate) fn connection_key(underlying: &str) -> ReferenceResult<String> {
        Ok(format!(
            "reference-massive-options-{}",
            normalize_option_underlying(underlying)?.to_ascii_lowercase()
        ))
    }

    pub(crate) fn connection_plan(
        &self,
        underlying: &str,
    ) -> ReferenceResult<(kairos_conflux::ConnectionKey, MassiveRestConfig)> {
        let underlying = normalize_option_underlying(underlying)?;
        let key = kairos_conflux::ConnectionKey::new(Self::connection_key(&underlying)?)
            .map_err(ReferenceError::Provider)?;
        Ok((
            key,
            MassiveRestConfig {
                environment: self.environment.clone(),
                endpoint: self.base_url.clone(),
                api_key: secrecy::SecretString::new(self.api_key.clone().into()),
                instrument_query: MassiveInstrumentQuery::options(Some(underlying)),
            },
        ))
    }

    pub(crate) async fn from_keys(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
        environment: impl Into<String>,
        sync_store: SqlxProviderSyncStore,
        connections: Vec<(String, kairos_conflux::ConnectionKey)>,
    ) -> ReferenceResult<Self> {
        #[cfg(not(test))]
        if !sync_store.supports_normalized_promotion() {
            return Err(ReferenceError::Persistence(
                "production Massive options ingestion requires normalized SQLite promotion".into(),
            ));
        }
        let mut source = Self {
            api_key: api_key.into(),
            base_url: base_url.into(),
            environment: environment.into(),
            scopes: BTreeMap::new(),
            last_good: BTreeMap::new(),
            sync_store,
            next_scope: 0,
            coverage_dirty: false,
            removed_scans: BTreeSet::new(),
        };
        for (underlying, key) in connections {
            source.load_scope_with_key(&underlying, key).await?;
        }
        Ok(source)
    }

    #[cfg(test)]
    pub async fn new(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
        mut sync_store: SqlxProviderSyncStore,
    ) -> ReferenceResult<(Self, kairos_conflux::ConfluxSystem)> {
        let api_key = api_key.into();
        let base_url = base_url.into();
        let underlyings = sync_store.option_underlyings("massive-options").await?;
        let mut system = kairos_conflux::ConfluxSystem::new();
        let mut keys = Vec::new();
        for underlying in underlyings {
            let key = kairos_conflux::ConnectionKey::new(Self::connection_key(&underlying)?)
                .map_err(ReferenceError::Provider)?;
            system
                .connections()
                .massive_rest
                .create(
                    key.clone(),
                    MassiveRestConfig {
                        environment: "public".into(),
                        endpoint: base_url.clone(),
                        api_key: secrecy::SecretString::new(api_key.clone().into()),
                        instrument_query: MassiveInstrumentQuery::options(Some(underlying.clone())),
                    },
                )
                .map_err(|error| ReferenceError::Provider(error.to_string()))?;
            keys.push((underlying, key));
        }
        let source = Self::from_keys(api_key, base_url, "public", sync_store, keys).await?;
        Ok((source, system))
    }

    fn scope_key(underlying: &str) -> String {
        format!("massive-options:{underlying}")
    }

    async fn load_scope_with_key(
        &mut self,
        underlying: &str,
        key: kairos_conflux::ConnectionKey,
    ) -> ReferenceResult<()> {
        let underlying = normalize_option_underlying(underlying)?;
        if self.scopes.contains_key(&underlying) {
            return Err(ReferenceError::Invalid(format!(
                "duplicate Massive options coverage connection: {underlying}"
            )));
        }
        let scope_key = Self::scope_key(&underlying);
        if self.sync_store.prepare_scan(&scope_key).await? {
            let log_event = log_events::SOURCE_WORK_STARTED;
            tracing::info!(
                event = log_event.event,
                component = log_event.component,
                area = log_event.area,
                action = log_event.action,
                outcome = log_event.outcome,
                legacy_event = "reference_provider_scan_reset",
                source_id = %scope_key,
                scan_format_version = PROVIDER_SCAN_FORMAT_VERSION,
                "unfinished provider scan was reset for the current canonical catalog"
            );
        }
        let (cursor, legacy_accumulated) = self
            .sync_store
            .load_state(&scope_key)
            .await?
            .unwrap_or((None, None));
        self.scopes.insert(
            underlying,
            ScopedMassiveOptions {
                connection: ConnectionRef::managed(key),
                cursor,
                pages_done: 0,
                legacy_accumulated,
            },
        );
        Ok(())
    }

    pub(crate) async fn set_option_underlying_with_key(
        &mut self,
        underlying: &str,
        enabled: bool,
        connection_key: Option<kairos_conflux::ConnectionKey>,
    ) -> ReferenceResult<()> {
        let underlying = normalize_option_underlying(underlying)?;
        self.sync_store
            .set_option_underlying("massive-options", &underlying, enabled)
            .await?;
        if enabled {
            if self.scopes.contains_key(&underlying) {
                return Ok(());
            }
            let connection_key = connection_key.ok_or_else(|| {
                ReferenceError::Provider(format!(
                    "missing managed Massive options connection for {underlying}"
                ))
            })?;
            self.load_scope_with_key(&underlying, connection_key)
                .await?;
        } else {
            self.removed_scans
                .insert(ReferenceSourceId::new(Self::scope_key(&underlying))?);
            self.scopes.remove(&underlying);
            self.last_good.remove(&underlying);
            self.next_scope = 0;
            self.coverage_dirty = true;
        }
        Ok(())
    }

    #[cfg_attr(test, allow(dead_code))]
    pub(crate) async fn set_scope_with_key(
        &mut self,
        scope: SourceScope,
        enabled: bool,
        connection_key: Option<kairos_conflux::ConnectionKey>,
    ) -> ReferenceResult<()> {
        let underlying = massive_options_underlying_from_scope(scope)?;
        self.set_option_underlying_with_key(&underlying, enabled, connection_key)
            .await
    }

    pub(super) async fn set_option_underlying_with_connections(
        &mut self,
        underlying: &str,
        enabled: bool,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        let underlying = normalize_option_underlying(underlying)?;
        if enabled {
            if self.scopes.contains_key(&underlying) {
                return Ok(());
            }
            let (key, parameters) = self.connection_plan(&underlying)?;
            connections
                .massive_rest
                .create(key.clone(), parameters)
                .map_err(|error| ReferenceError::Provider(error.to_string()))?;
            self.set_option_underlying_with_key(&underlying, true, Some(key))
                .await?;
        } else {
            let key = self
                .scopes
                .get(&underlying)
                .map(|scope| match &scope.connection {
                    ConnectionRef(key) => key.clone(),
                });
            self.set_option_underlying_with_key(&underlying, false, None)
                .await?;
            if let Some(key) = key {
                connections
                    .massive_rest
                    .remove(&key)
                    .map_err(|error| ReferenceError::Provider(error.to_string()))?;
            }
        }
        Ok(())
    }

    pub(crate) async fn set_scope_with_connections(
        &mut self,
        scope: SourceScope,
        enabled: bool,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        let underlying = massive_options_underlying_from_scope(scope)?;
        self.set_option_underlying_with_connections(&underlying, enabled, connections)
            .await
    }

    fn merged_last_good(&self) -> ReferenceResult<ProviderCatalog> {
        ProviderCatalog::merge(self.last_good.values())
    }

    async fn advance_one_scope(
        &mut self,
        underlying: &str,
        connections: &kairos_conflux::ConnectionCollections<'_>,
        record_limit: usize,
    ) -> ReferenceResult<MassiveScopeAdvance> {
        let key = Self::scope_key(underlying);
        let (legacy, cursor) = {
            let scope = self
                .scopes
                .get_mut(underlying)
                .expect("enabled coverage scope is present");
            (scope.legacy_accumulated.take(), scope.cursor.clone())
        };
        if cursor.is_none() && legacy.is_none() {
            self.sync_store.clear_staged_pages(&key).await?;
        }
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
            let result = match &mut scope.connection {
                ConnectionRef(connection_key) => {
                    tokio::time::timeout(
                        MASSIVE_PAGE_TIMEOUT,
                        connections
                            .massive_rest
                            .get_shared(connection_key)
                            .map_err(|error| ReferenceError::Provider(error.to_string()))?
                            .fetch_instruments_page(cursor.as_deref(), record_limit),
                    )
                    .await
                },
            };
            result
                .map_err(|error| {
                    ReferenceError::Provider(format!(
                        "Massive {underlying} page timed out: {error}"
                    ))
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
        let records_seen = page_catalog.record_count() as u64;
        let pages_done = self
            .scopes
            .get(underlying)
            .map_or(0, |scope| scope.pages_done)
            .saturating_add(1);
        let next_cursor = if page.complete {
            None
        } else {
            page.next_cursor
        };
        self.sync_store
            .append_staged_page(&key, next_cursor.as_deref(), &page_catalog)
            .await?;
        {
            let scope = self
                .scopes
                .get_mut(underlying)
                .expect("enabled coverage scope is present");
            scope.cursor = next_cursor;
            scope.pages_done = pages_done;
        }
        if !page.complete {
            return Ok(MassiveScopeAdvance::InProgress {
                pages_done,
                records_seen,
            });
        }
        let normalized = self.sync_store.supports_normalized_promotion();
        let (catalog, records_changed) = if normalized {
            let records_changed = self.sync_store.staged_change_count(&key).await?;
            (ProviderCatalog::default(), Some(records_changed))
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
            (catalog, None)
        };
        self.scopes
            .get_mut(underlying)
            .expect("enabled coverage scope is present")
            .cursor = None;
        if normalized {
            self.scopes
                .get_mut(underlying)
                .expect("enabled coverage scope is present")
                .pages_done = 0;
            Ok(MassiveScopeAdvance::Complete(MassiveScopeAdvanceResult {
                catalog: ProviderCatalog::default(),
                staged_changes: Some(SourceChanges {
                    completed_scans: BTreeSet::from([ReferenceSourceId::new(&key)?]),
                    removed_scans: BTreeSet::new(),
                }),
                pages_done,
                records_seen,
                records_changed,
            }))
        } else {
            self.last_good.insert(underlying.into(), catalog);
            self.scopes
                .get_mut(underlying)
                .expect("enabled coverage scope is present")
                .pages_done = 0;
            Ok(MassiveScopeAdvance::Complete(MassiveScopeAdvanceResult {
                catalog: self.merged_last_good()?,
                staged_changes: None,
                pages_done,
                records_seen,
                records_changed: None,
            }))
        }
    }
}

pub(crate) fn massive_options_underlying_from_scope(scope: SourceScope) -> ReferenceResult<String> {
    match scope.kind {
        SourceScopeKind::UnderlyingInstrument | SourceScopeKind::Coverage => {
            let Some(id) = scope.id else {
                return Err(ReferenceError::Invalid(
                    "Massive options coverage scope requires an id".into(),
                ));
            };
            normalize_option_underlying(scope_underlying_symbol(&id))
        },
        SourceScopeKind::Global | SourceScopeKind::ProviderCatalog | SourceScopeKind::Custom => {
            Err(ReferenceError::Invalid(format!(
                "Massive options does not support {} coverage scope",
                scope.kind.as_str()
            )))
        },
    }
}

fn scope_underlying_symbol(id: &str) -> &str {
    let mut parts = id.split(':');
    match (
        parts.next(),
        parts.next(),
        parts.next(),
        parts.next(),
        parts.next(),
    ) {
        (Some("instrument"), Some("equity"), Some("US"), Some(symbol), Some("common"))
            if parts.next().is_none() =>
        {
            symbol
        },
        _ => id,
    }
}

impl MassiveEquitySource {
    pub(crate) async fn from_key(
        key: kairos_conflux::ConnectionKey,
        mut sync_store: SqlxProviderSyncStore,
    ) -> ReferenceResult<Self> {
        if sync_store.prepare_scan("massive-equity").await? {
            let log_event = log_events::SOURCE_WORK_STARTED;
            tracing::info!(
                event = log_event.event,
                component = log_event.component,
                area = log_event.area,
                action = log_event.action,
                outcome = log_event.outcome,
                legacy_event = "reference_provider_scan_reset",
                source_id = "massive-equity",
                scan_format_version = PROVIDER_SCAN_FORMAT_VERSION,
                "unfinished provider scan was reset for the current canonical catalog"
            );
        }
        let (cursor, accumulated) = sync_store
            .load_state("massive-equity")
            .await?
            .unwrap_or((None, None));
        Ok(Self {
            connection: ConnectionRef::managed(key),
            cursor,
            pages_done: 0,
            accumulated,
            sync_store,
        })
    }

    #[cfg(test)]
    fn without_state(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
        sync_store: SqlxProviderSyncStore,
    ) -> ReferenceResult<(Self, kairos_conflux::ConfluxSystem)> {
        let key = kairos_conflux::ConnectionKey::new("reference-massive-equity")
            .map_err(ReferenceError::Provider)?;
        let mut system = kairos_conflux::ConfluxSystem::new();
        system
            .connections()
            .massive_rest
            .create(
                key.clone(),
                MassiveRestConfig {
                    environment: "public".into(),
                    endpoint: base_url.into(),
                    api_key: secrecy::SecretString::new(api_key.into().into()),
                    instrument_query: MassiveInstrumentQuery::equities(),
                },
            )
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        Ok((
            Self {
                connection: ConnectionRef::managed(key),
                cursor: None,
                pages_done: 0,
                accumulated: None,
                sync_store,
            },
            system,
        ))
    }

    #[cfg(test)]
    pub async fn new_with_sync_store(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
        mut sync_store: SqlxProviderSyncStore,
    ) -> ReferenceResult<(Self, kairos_conflux::ConfluxSystem)> {
        if sync_store.prepare_scan("massive-equity").await? {
            let log_event = log_events::SOURCE_WORK_STARTED;
            tracing::info!(
                event = log_event.event,
                component = log_event.component,
                area = log_event.area,
                action = log_event.action,
                outcome = log_event.outcome,
                legacy_event = "reference_provider_scan_reset",
                source_id = "massive-equity",
                scan_format_version = PROVIDER_SCAN_FORMAT_VERSION,
                "unfinished provider scan was reset for the current canonical catalog"
            );
        }
        let (cursor, accumulated) = sync_store
            .load_state("massive-equity")
            .await?
            .unwrap_or((None, None));
        let (mut source, system) = Self::without_state(api_key, base_url, sync_store)?;
        source.cursor = cursor;
        source.accumulated = accumulated;
        Ok((source, system))
    }
}

#[async_trait::async_trait(?Send)]
impl ReferenceSource for MassiveOptionsCoverageSource {
    fn source_id(&self) -> &str {
        "massive-options"
    }

    async fn fetch_catalog_with_connections(
        &mut self,
        connections: &kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        Ok(self
            .fetch_catalog_step_with_connections(connections)
            .await?
            .catalog)
    }

    async fn fetch_catalog_step_with_connections(
        &mut self,
        connections: &kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<SourceUpdate> {
        self.fetch_catalog_step_with_budget(connections, SourceTickBudget::default())
            .await
    }

    async fn fetch_catalog_step_with_budget(
        &mut self,
        connections: &kairos_conflux::ConnectionCollections<'_>,
        budget: SourceTickBudget,
    ) -> ReferenceResult<SourceUpdate> {
        let result = async {
            if self.coverage_dirty {
                self.coverage_dirty = false;
                let normalized = self.sync_store.supports_normalized_promotion();
                return Ok(SourceUpdate {
                    catalog: if normalized {
                        ProviderCatalog::default()
                    } else {
                        self.merged_last_good()?
                    },
                    complete: true,
                    page_count: 0,
                    staged_changes: normalized.then(|| SourceChanges {
                        completed_scans: BTreeSet::new(),
                        removed_scans: std::mem::take(&mut self.removed_scans),
                    }),
                    work_item_id: Some("massive-options:coverage".to_owned()),
                    scope_id: None,
                    scope_kind: Some("coverage".to_owned()),
                    cursor_present: Some(false),
                    ..SourceUpdate::default()
                });
            }
            if self.scopes.is_empty() {
                let normalized = self.sync_store.supports_normalized_promotion();
                return Ok(SourceUpdate {
                    catalog: ProviderCatalog::default(),
                    complete: true,
                    page_count: 0,
                    staged_changes: normalized.then(SourceChanges::default),
                    work_item_id: Some("massive-options:coverage".to_owned()),
                    scope_id: None,
                    scope_kind: Some("coverage".to_owned()),
                    cursor_present: Some(false),
                    ..SourceUpdate::default()
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
            let work_item_id = Some(Self::scope_key(&underlying));
            let scope_id = Some(format!("instrument:equity:US:{underlying}:common"));
            let scope_kind = Some("underlying_instrument".to_owned());
            match self
                .advance_one_scope(&underlying, connections, massive_page_limit(budget))
                .await?
            {
                MassiveScopeAdvance::Complete(result) => Ok(SourceUpdate {
                    catalog: result.catalog,
                    complete: true,
                    page_count: 1,
                    pages_done: Some(result.pages_done),
                    pages_total: Some(result.pages_done),
                    records_seen: Some(result.records_seen),
                    records_changed: result.records_changed,
                    staged_changes: result.staged_changes,
                    work_item_id,
                    scope_id,
                    scope_kind,
                    cursor_present: Some(false),
                    ..SourceUpdate::default()
                }),
                MassiveScopeAdvance::InProgress {
                    pages_done,
                    records_seen,
                } if self.last_good.is_empty() => Ok(SourceUpdate {
                    catalog: ProviderCatalog::default(),
                    complete: false,
                    page_count: 1,
                    pages_done: Some(pages_done),
                    records_seen: Some(records_seen),
                    staged_changes: None,
                    work_item_id,
                    scope_id,
                    scope_kind,
                    cursor_present: Some(true),
                    ..SourceUpdate::default()
                }),
                MassiveScopeAdvance::InProgress {
                    pages_done,
                    records_seen,
                } => Ok(SourceUpdate {
                    // A replacement scope is still paging, but a completed
                    // scoped snapshot exists. Keep it authoritative until that
                    // one underlying finishes rather than degrading the entire
                    // Massive provider.
                    catalog: self.merged_last_good()?,
                    complete: false,
                    page_count: 1,
                    pages_done: Some(pages_done),
                    records_seen: Some(records_seen),
                    staged_changes: None,
                    work_item_id,
                    scope_id,
                    scope_kind,
                    cursor_present: Some(true),
                    ..SourceUpdate::default()
                }),
            }
        }
        .await;
        #[cfg(not(test))]
        self.last_good.clear();
        result
    }

    fn option_underlyings(&self) -> Vec<String> {
        self.scopes.keys().cloned().collect()
    }
}

#[async_trait::async_trait(?Send)]
impl ReferenceSource for MassiveEquitySource {
    fn source_id(&self) -> &str {
        "massive-equity"
    }

    async fn fetch_catalog_with_connections(
        &mut self,
        connections: &kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        let facts = match &mut self.connection {
            ConnectionRef(key) => {
                connections
                    .massive_rest
                    .get_shared(key)
                    .map_err(|error| ReferenceError::Provider(error.to_string()))?
                    .fetch_instruments()
                    .await
            },
        }
        .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        massive_provider_catalog(facts)
    }

    async fn fetch_catalog_step_with_connections(
        &mut self,
        connections: &kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<SourceUpdate> {
        self.fetch_catalog_step_with_budget(connections, SourceTickBudget::default())
            .await
    }

    async fn fetch_catalog_step_with_budget(
        &mut self,
        connections: &kairos_conflux::ConnectionCollections<'_>,
        budget: SourceTickBudget,
    ) -> ReferenceResult<SourceUpdate> {
        if self.accumulated.is_none() {
            if let Some((cursor, accumulated)) =
                self.sync_store.load_state("massive-equity").await?
            {
                self.cursor = cursor;
                self.accumulated = accumulated;
            }
        }
        if self.cursor.is_none() && self.accumulated.is_none() {
            self.sync_store.clear_staged_pages("massive-equity").await?;
        }
        if let Some(legacy_catalog) = self.accumulated.take() {
            self.sync_store
                .append_staged_page("massive-equity", self.cursor.as_deref(), &legacy_catalog)
                .await?;
        }
        let mut cursor = self.cursor.clone();
        let mut complete = false;
        let mut page_count = 0;
        let mut records_seen = 0u64;
        let mut records_changed = None;
        let record_limit = massive_page_limit(budget);
        let max_batches = usize::try_from(budget.max_batches_per_source)
            .unwrap_or(usize::MAX)
            .max(1);
        for _ in 0..max_batches {
            let page = match &mut self.connection {
                ConnectionRef(key) => {
                    tokio::time::timeout(
                        MASSIVE_PAGE_TIMEOUT,
                        connections
                            .massive_rest
                            .get_shared(key)
                            .map_err(|error| ReferenceError::Provider(error.to_string()))?
                            .fetch_instruments_page(cursor.as_deref(), record_limit),
                    )
                    .await
                },
            };
            let page = match page {
                Ok(result) => {
                    result.map_err(|error| ReferenceError::Provider(error.to_string()))?
                },
                Err(_) => {
                    let log_event = log_events::SOURCE_SCAN_DEGRADED;
                    tracing::warn!(
                        event = log_event.event,
                        component = log_event.component,
                        area = log_event.area,
                        action = log_event.action,
                        outcome = log_event.outcome,
                        legacy_event = "reference_massive_page_timeout",
                        source_id = "massive-equity",
                        page_count,
                        "Massive page budget expired; persisted cursor will resume on the next refresh"
                    );
                    break;
                },
            };
            page_count += 1;
            self.pages_done = self.pages_done.saturating_add(1);
            cursor = page.next_cursor;
            let page_catalog = massive_provider_catalog(page.catalog)?;
            records_seen = records_seen.saturating_add(page_catalog.record_count() as u64);
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
        let mut staged_changes = None;
        let result_catalog = if complete {
            self.cursor = None;
            if self.sync_store.supports_normalized_promotion() {
                records_changed = Some(
                    self.sync_store
                        .staged_change_count("massive-equity")
                        .await?,
                );
                staged_changes = Some(SourceChanges {
                    completed_scans: BTreeSet::from([ReferenceSourceId::new("massive-equity")?]),
                    removed_scans: BTreeSet::new(),
                });
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
        let pages_done = if page_count == 0 {
            self.pages_done
        } else {
            self.pages_done.max(page_count as u64)
        };
        if complete {
            self.pages_done = 0;
        }
        Ok(SourceUpdate {
            catalog: result_catalog,
            complete,
            page_count,
            pages_done: Some(pages_done),
            pages_total: complete.then_some(pages_done),
            records_seen: Some(records_seen),
            records_changed,
            staged_changes,
            work_item_id: Some("massive-equity:catalog".to_owned()),
            scope_id: Some("massive-equity".to_owned()),
            scope_kind: Some("provider_catalog".to_owned()),
            cursor_present: Some(self.cursor.is_some()),
            ..SourceUpdate::default()
        })
    }
}

fn massive_page_limit(budget: SourceTickBudget) -> usize {
    budget
        .max_records_per_batch
        .and_then(|value| usize::try_from(value).ok())
        .filter(|value| *value > 0)
        .unwrap_or(1_000)
}

pub(super) fn massive_provider_catalog(
    facts: ExternalInstrumentCatalog,
) -> ReferenceResult<ProviderCatalog> {
    if facts.participant.id.as_str() != "massive" {
        return Err(ReferenceError::Provider(format!(
            "Massive source received catalog for {}",
            facts.participant.id
        )));
    }
    let mut catalog = ProviderCatalog::default();
    append_massive_venues(&mut catalog, facts.venues)?;
    for value in facts.instruments {
        append_massive_instrument(&mut catalog, value)?;
    }
    catalog
        .exchanges
        .sort_by(|left, right| left.exchange_id.cmp(&right.exchange_id));
    catalog
        .exchanges
        .dedup_by(|left, right| left.exchange_id == right.exchange_id);
    catalog
        .assets
        .sort_by(|left, right| left.asset_id.cmp(&right.asset_id));
    catalog
        .assets
        .dedup_by(|left, right| left.asset_id == right.asset_id);
    crate::domain::reconcile_instruments(&mut catalog.instruments)?;
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

fn append_massive_venues(
    catalog: &mut ProviderCatalog,
    venues: Vec<kairos_conflux::ExternalVenue>,
) -> ReferenceResult<()> {
    use kairos_conflux::ExternalVenueKind;

    use crate::domain::{Venue, VenueIdentifierKind, VenueIdentifierMapping, VenueKind, VenueRole};

    for value in venues {
        let Some(mic) = value
            .mic
            .as_deref()
            .filter(|value| !value.trim().is_empty())
        else {
            // A provider identifier alone is not a canonical identity. Keep
            // such rows outside the catalog until the source supplies a MIC.
            continue;
        };
        let mic_value = kairos_primitives::reference::Mic::new(mic)?;
        let venue_id = kairos_primitives::reference::VenueId::new(format!(
            "venue:{}",
            mic.to_ascii_lowercase()
        ))?;
        let (venue_kind, roles) = match value.kind {
            ExternalVenueKind::Exchange => (
                VenueKind::RegulatedExchange,
                [VenueRole::Execution].into_iter().collect(),
            ),
            ExternalVenueKind::TradeReportingFacility => (
                VenueKind::TradeReportingFacility,
                [VenueRole::Reporting].into_iter().collect(),
            ),
            ExternalVenueKind::Sip | ExternalVenueKind::Unknown => (
                VenueKind::Unknown,
                [VenueRole::Reporting].into_iter().collect(),
            ),
        };
        let status: kairos_primitives::reference::ReferenceStatus =
            if value.active { "active" } else { "inactive" }.into();
        catalog.venues.push(Venue {
            venue_id: venue_id.clone(),
            name: value.name,
            venue_kind,
            roles,
            mic: Some(mic_value),
            operating_mic: value
                .operating_mic
                .map(kairos_primitives::reference::Mic::new)
                .transpose()?,
            parent_venue_id: None,
            jurisdiction: Some(kairos_primitives::reference::JurisdictionCode::new("US")?),
            status,
        });
        for (kind, identifier) in [
            (
                VenueIdentifierKind::Exchange,
                Some(value.provider_identifier),
            ),
            (VenueIdentifierKind::Mic, Some(mic.to_owned())),
        ] {
            let Some(identifier) = identifier.filter(|value| !value.trim().is_empty()) else {
                continue;
            };
            catalog
                .venue_identifier_mappings
                .push(VenueIdentifierMapping {
                    source_id: kairos_primitives::reference::ReferenceSourceId::new(
                        "massive-equity",
                    )?,
                    provider: kairos_primitives::market::Provider::new("massive")?,
                    provider_product: "equity".into(),
                    identifier_kind: kind,
                    identifier,
                    venue_id: venue_id.clone(),
                    status,
                });
        }
    }
    catalog
        .venues
        .sort_by(|left, right| left.venue_id.cmp(&right.venue_id));
    catalog
        .venues
        .dedup_by(|left, right| left.venue_id == right.venue_id);
    Ok(())
}

fn append_massive_instrument(
    catalog: &mut ProviderCatalog,
    value: ExternalInstrument,
) -> ReferenceResult<()> {
    let source_symbol = value.source_symbol.as_str().to_ascii_uppercase();
    let exchange_id = massive_exchange_id(value.source_venue.as_deref());
    if let Some(exchange_id) = exchange_id.as_deref() {
        catalog.exchanges.push(Exchange {
            exchange_id: ExchangeId::new(exchange_id)?,
            name: massive_exchange_name(exchange_id).into(),
            status: "active".into(),
            source_id: None,
        });
    }
    let quote = value
        .quote_currency
        .as_ref()
        .map(|value| value.as_str().to_ascii_uppercase())
        .unwrap_or_else(|| "USD".into());
    let status: kairos_primitives::reference::ReferenceStatus =
        if value.active { "active" } else { "inactive" }.into();
    let (family, instrument_id, symbol, underlying_id) = match value.kind {
        ExternalInstrumentKind::Equity => {
            let ticker = source_symbol.clone();
            ensure_massive_underlying(catalog, &ticker, &quote, status)?;
            (
                "equity",
                format!("instrument:equity:US:{ticker}:common"),
                ticker,
                None,
            )
        },
        ExternalInstrumentKind::Option => {
            let underlying = value
                .underlying
                .as_ref()
                .map(|value| value.as_str().to_ascii_uppercase())
                .ok_or_else(|| {
                    ReferenceError::Provider("Massive option underlying is missing".into())
                })?;
            ensure_massive_underlying(catalog, &underlying, &quote, status)?;
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
                    )));
                },
            };
            (
                "options",
                format!("instrument:option:{underlying}:{expiry}:{strike}:{right}"),
                format!("{underlying}-{expiry}-{strike}-{right}"),
                Some(kairos_primitives::reference::InstrumentId::new(format!(
                    "instrument:equity:US:{underlying}:common"
                ))?),
            )
        },
        other => {
            return Err(ReferenceError::Provider(format!(
                "unsupported Massive instrument kind: {other:?}"
            )));
        },
    };
    let instrument_id = kairos_primitives::reference::InstrumentId::new(instrument_id)?;
    catalog.instruments.push(Instrument {
        instrument_id: instrument_id.clone(),
        symbol: kairos_primitives::reference::Symbol::new(symbol.clone())?,
        instrument_type: canonical_instrument_kind(value.kind)?,
        issuer_id: (family == "equity").then(|| {
            kairos_primitives::reference::IssuerId::new(format!("issuer:US:{source_symbol}"))
                .expect("validated Massive issuer")
        }),
        share_class: (family == "equity").then(|| "common".into()),
        primary_currency_asset_id: Some(kairos_primitives::reference::AssetId::new(format!(
            "asset:fiat:{quote}"
        ))?),
        underlying_instrument_id: underlying_id.clone(),
        expiry_unix_nanos: matches!(
            value.kind,
            ExternalInstrumentKind::Future | ExternalInstrumentKind::Option
        )
        .then_some(value.expiry_unix_nanos)
        .flatten(),
        strike: super::optional_decimal(value.strike.clone(), "Massive option strike")?,
        option_right: value.option_right.clone(),
        status,
        ..Instrument::default()
    });
    if let Some(exchange_id) = exchange_id {
        let exchange = kairos_primitives::reference::ExchangeId::new(exchange_id.clone())?;
        let listing_id = kairos_primitives::reference::ListingId::venue(
            &exchange,
            canonical_instrument_kind(value.kind)?,
            if family == "equity" {
                source_symbol.as_str()
            } else {
                symbol.as_str()
            },
        )?;
        catalog.listings.push(Listing {
            source_id: Some(ReferenceSourceId::new(if family == "equity" {
                "massive-equity"
            } else {
                "massive-options"
            })?),
            listing_id: listing_id.clone(),
            instrument_id: instrument_id.clone(),
            exchange_id: exchange.clone(),
            exchange_symbol: kairos_primitives::reference::Symbol::new(source_symbol.clone())?,
            status,
            effective_from_unix_nanos: 0.into(),
            effective_to_unix_nanos: value.expiry_unix_nanos,
            ..Listing::default()
        });
        if family == "equity" {
            catalog.markets.push(Market {
                market_id: kairos_primitives::reference::MarketId::venue(
                    &exchange,
                    InstrumentKind::Equity,
                    format!("{source_symbol}:{quote}"),
                )?,
                instrument_id,
                listing_id: Some(listing_id),
                exchange_id: exchange,
                instrument_kind: InstrumentKind::Equity,
                asset_type: Some(AssetClass::Equity),
                venue_symbol: Some(kairos_primitives::reference::Symbol::new(source_symbol)?),
                base_asset_id: Some(kairos_primitives::reference::AssetId::new(format!(
                    "asset:equity:{symbol}"
                ))?),
                quote_asset_id: Some(kairos_primitives::reference::AssetId::new(format!(
                    "asset:fiat:{quote}"
                ))?),
                status,
                price_tick: super::optional_decimal(value.price_tick, "Massive price tick")?,
                quantity_tick: super::optional_decimal(
                    value.quantity_tick,
                    "Massive quantity tick",
                )?,
                price_precision: value.price_precision.unwrap_or_default() as i32,
                quantity_precision: value.quantity_precision.unwrap_or_default() as i32,
                minimum_quantity: super::optional_decimal(
                    value.minimum_quantity,
                    "Massive minimum quantity",
                )?,
                minimum_notional: super::optional_decimal(
                    value.minimum_notional,
                    "Massive minimum notional",
                )?,
                contract_size: super::optional_decimal(
                    value.contract_value,
                    "Massive contract size",
                )?,
                effective_from_unix_nanos: 0.into(),
                effective_to_unix_nanos: None,
                ..Market::default()
            });
        } else if family == "options" {
            catalog.markets.push(Market {
                market_id: kairos_primitives::reference::MarketId::venue(
                    &exchange,
                    InstrumentKind::Option,
                    source_symbol.clone(),
                )?,
                instrument_id,
                listing_id: Some(listing_id),
                exchange_id: exchange,
                instrument_kind: InstrumentKind::Option,
                underlying_instrument_id: underlying_id,
                venue_symbol: Some(kairos_primitives::reference::Symbol::new(source_symbol)?),
                base_asset_id: None,
                quote_asset_id: Some(kairos_primitives::reference::AssetId::new(format!(
                    "asset:fiat:{quote}"
                ))?),
                status,
                price_tick: super::optional_decimal(value.price_tick, "Massive price tick")?,
                quantity_tick: super::optional_decimal(
                    value.quantity_tick,
                    "Massive quantity tick",
                )?,
                price_precision: value.price_precision.unwrap_or_default() as i32,
                quantity_precision: value.quantity_precision.unwrap_or_default() as i32,
                minimum_quantity: super::optional_decimal(
                    value.minimum_quantity,
                    "Massive minimum quantity",
                )?,
                minimum_notional: super::optional_decimal(
                    value.minimum_notional,
                    "Massive minimum notional",
                )?,
                contract_size: super::optional_decimal(
                    value.contract_value,
                    "Massive contract size",
                )?,
                effective_from_unix_nanos: 0.into(),
                effective_to_unix_nanos: value.expiry_unix_nanos,
                ..Market::default()
            });
        }
    }
    Ok(())
}

fn ensure_massive_underlying(
    catalog: &mut ProviderCatalog,
    ticker: &str,
    quote: &str,
    status: kairos_primitives::reference::ReferenceStatus,
) -> ReferenceResult<()> {
    let equity_asset =
        kairos_primitives::reference::AssetId::new(format!("asset:equity:{ticker}"))?;
    let fiat_asset = kairos_primitives::reference::AssetId::new(format!("asset:fiat:{quote}"))?;
    catalog.assets.push(Asset {
        asset_id: equity_asset.clone(),
        code: kairos_primitives::reference::Symbol::new(ticker)?,
        asset_class: AssetClass::Equity,
        status: "active".into(),
        ..Asset::default()
    });
    catalog.assets.push(Asset {
        asset_id: fiat_asset.clone(),
        code: kairos_primitives::reference::Symbol::new(quote)?,
        asset_class: AssetClass::Fiat,
        status: "active".into(),
        ..Asset::default()
    });
    let instrument_id = kairos_primitives::reference::InstrumentId::new(format!(
        "instrument:equity:US:{ticker}:common"
    ))?;
    if catalog
        .instruments
        .iter()
        .any(|value| value.instrument_id == instrument_id)
    {
        return Ok(());
    }
    catalog.instruments.push(Instrument {
        instrument_id: instrument_id.clone(),
        symbol: kairos_primitives::reference::Symbol::new(ticker.to_owned())?,
        instrument_type: InstrumentKind::Equity,
        issuer_id: Some(kairos_primitives::reference::IssuerId::new(format!(
            "issuer:US:{ticker}"
        ))?),
        share_class: Some("common".into()),
        primary_currency_asset_id: Some(fiat_asset.clone()),
        status,
        ..Instrument::default()
    });
    Ok(())
}

fn massive_exchange_id(source_venue: Option<&str>) -> Option<String> {
    match source_venue?.trim().to_ascii_uppercase().as_str() {
        "" | "UNKNOWN" | "OPRA" => None,
        "XNAS" | "NASDAQ" => Some("exchange:nasdaq".into()),
        "XNYS" | "NYSE" => Some("exchange:nyse".into()),
        "XASE" | "AMEX" => Some("exchange:amex".into()),
        "BATO" => Some("exchange:cboe-bzx-options".into()),
        value => Some(format!("exchange:{}", value.to_ascii_lowercase())),
    }
}

fn massive_exchange_name(exchange_id: &str) -> &str {
    crate::domain::canonical_exchange_name(exchange_id).unwrap_or("Exchange")
}
