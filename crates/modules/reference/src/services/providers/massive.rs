//! Massive Reference sources, scoped discovery, and canonical mapping.

use super::*;

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
    scopes: BTreeMap<String, ScopedMassiveOptions>,
    last_good: BTreeMap<String, ProviderCatalog>,
    sync_store: SqlxProviderSyncStore,
    next_scope: usize,
    coverage_dirty: bool,
}

struct ScopedMassiveOptions {
    connection: ConnectionRef,
    cursor: Option<String>,
    legacy_accumulated: Option<ProviderCatalog>,
}

pub struct MassiveEquitySource {
    connection: ConnectionRef,
    cursor: Option<String>,
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
                environment: "public".into(),
                endpoint: self.base_url.clone(),
                api_key: secrecy::SecretString::new(self.api_key.clone().into()),
                instrument_query: MassiveInstrumentQuery::options(Some(underlying)),
            },
        ))
    }

    pub(crate) async fn from_keys(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
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
            scopes: BTreeMap::new(),
            last_good: BTreeMap::new(),
            sync_store,
            next_scope: 0,
            coverage_dirty: false,
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
        let source = Self::from_keys(api_key, base_url, sync_store, keys).await?;
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
        if self.sync_store.prepare_projection(&scope_key).await? {
            tracing::info!(
                event = "reference_provider_projection_reset",
                component = "reference",
                provider = %scope_key,
                projection_version = crate::services::sqlx_storage::PROVIDER_PROJECTION_VERSION,
                "unfinished provider scan was reset for the current canonical projection"
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

    #[cfg(test)]
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

    fn merged_last_good(&self) -> ReferenceResult<ProviderCatalog> {
        merge_provider_catalog_views(self.last_good.values())
    }

    async fn advance_one_scope(
        &mut self,
        underlying: &str,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
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
            let result = match &mut scope.connection {
                ConnectionRef(connection_key) => {
                    tokio::time::timeout(
                        MASSIVE_PAGE_TIMEOUT,
                        connections
                            .massive_rest
                            .get(connection_key)
                            .map_err(|error| ReferenceError::Provider(error.to_string()))?
                            .fetch_instruments_page(cursor.as_deref(), 1000),
                    )
                    .await
                }
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

impl MassiveEquitySource {
    pub(crate) async fn from_key(
        key: kairos_conflux::ConnectionKey,
        mut sync_store: SqlxProviderSyncStore,
    ) -> ReferenceResult<Self> {
        if sync_store.prepare_projection("massive-equity").await? {
            tracing::info!(
                event = "reference_provider_projection_reset",
                component = "reference",
                provider = "massive-equity",
                projection_version = crate::services::sqlx_storage::PROVIDER_PROJECTION_VERSION,
                "unfinished provider scan was reset for the current canonical projection"
            );
        }
        let (cursor, accumulated) = sync_store
            .load_state("massive-equity")
            .await?
            .unwrap_or((None, None));
        Ok(Self {
            connection: ConnectionRef::managed(key),
            cursor,
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
        if sync_store.prepare_projection("massive-equity").await? {
            tracing::info!(
                event = "reference_provider_projection_reset",
                component = "reference",
                provider = "massive-equity",
                projection_version = crate::services::sqlx_storage::PROVIDER_PROJECTION_VERSION,
                "unfinished provider scan was reset for the current canonical projection"
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

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let mut system = kairos_conflux::ConfluxSystem::new();
        self.fetch_catalog_with_connections(&mut system.connections())
            .await
    }

    async fn fetch_catalog_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        Ok(self
            .fetch_catalog_step_with_connections(connections)
            .await?
            .catalog)
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<ProviderUpdate> {
        let mut system = kairos_conflux::ConfluxSystem::new();
        self.fetch_catalog_step_with_connections(&mut system.connections())
            .await
    }

    async fn fetch_catalog_step_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderUpdate> {
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
            match self.advance_one_scope(&underlying, connections).await? {
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

    fn option_underlyings(&self) -> Vec<String> {
        self.scopes.keys().cloned().collect()
    }
}

#[async_trait::async_trait(?Send)]
impl ReferenceSource for MassiveEquitySource {
    fn source_id(&self) -> &str {
        "massive-equity"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let mut system = kairos_conflux::ConfluxSystem::new();
        self.fetch_catalog_with_connections(&mut system.connections())
            .await
    }

    async fn fetch_catalog_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        let facts = match &mut self.connection {
            ConnectionRef(key) => {
                connections
                    .massive_rest
                    .get(key)
                    .map_err(|error| ReferenceError::Provider(error.to_string()))?
                    .fetch_instruments()
                    .await
            }
        }
        .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        massive_provider_catalog(facts)
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<ProviderUpdate> {
        let mut system = kairos_conflux::ConfluxSystem::new();
        self.fetch_catalog_step_with_connections(&mut system.connections())
            .await
    }

    async fn fetch_catalog_step_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderUpdate> {
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
            let page = match &mut self.connection {
                ConnectionRef(key) => {
                    tokio::time::timeout(
                        MASSIVE_PAGE_TIMEOUT,
                        connections
                            .massive_rest
                            .get(key)
                            .map_err(|error| ReferenceError::Provider(error.to_string()))?
                            .fetch_instruments_page(cursor.as_deref(), 1000),
                    )
                    .await
                }
            };
            let page = match page {
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

pub(super) fn massive_provider_catalog(
    facts: ExternalInstrumentCatalog,
) -> ReferenceResult<ProviderCatalog> {
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

fn append_massive_instrument(
    catalog: &mut ProviderCatalog,
    value: ExternalInstrument,
) -> ReferenceResult<()> {
    let source_symbol = value.source_symbol.as_str().to_ascii_uppercase();
    let exchange_id = massive_exchange_id(value.source_venue.as_deref());
    if let Some(exchange_id) = exchange_id.as_deref() {
        catalog.entities.push(Entity {
            entity_id: exchange_id.into(),
            entity_type: "exchange".into(),
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
    let status: kairos_primitives::ReferenceStatus =
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
        }
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
                    )))
                }
            };
            (
                "options",
                format!("instrument:option:{underlying}:{expiry}:{strike}:{right}"),
                format!("{underlying}-{expiry}-{strike}-{right}"),
                Some(kairos_primitives::InstrumentId::new(format!(
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
    let instrument_id = kairos_primitives::InstrumentId::new(instrument_id)?;
    catalog.instruments.push(Instrument {
        instrument_id: instrument_id.clone(),
        symbol: kairos_primitives::Symbol::new(symbol.clone())?,
        instrument_type: canonical_instrument_kind(value.kind)?,
        issuer_id: (family == "equity").then(|| {
            kairos_primitives::IssuerId::new(format!("issuer:US:{source_symbol}"))
                .expect("validated Massive issuer")
        }),
        share_class: (family == "equity").then(|| "common".into()),
        primary_currency_asset_id: Some(kairos_primitives::AssetId::new(format!(
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
    if let Some(exchange_id) = exchange_id {
        let listing_id = if family == "equity" {
            format!("listing:{exchange_id}:equity:{source_symbol}:{quote}")
        } else {
            format!("listing:{exchange_id}:option:{symbol}")
        };
        catalog.listings.push(Listing {
            listing_id: kairos_primitives::ListingId::new(listing_id)?,
            instrument_id,
            exchange_id: kairos_primitives::Exchange::new(exchange_id)?,
            exchange_symbol: kairos_primitives::Symbol::new(source_symbol)?,
            status,
            effective_from_unix_nanos: 0.into(),
            effective_to_unix_nanos: value.expiry_unix_nanos,
            ..Listing::default()
        });
    }
    Ok(())
}

fn ensure_massive_underlying(
    catalog: &mut ProviderCatalog,
    ticker: &str,
    quote: &str,
    status: kairos_primitives::ReferenceStatus,
) -> ReferenceResult<()> {
    let equity_asset = kairos_primitives::AssetId::new(format!("asset:equity:{ticker}"))?;
    let fiat_asset = kairos_primitives::AssetId::new(format!("asset:fiat:{quote}"))?;
    catalog.assets.push(Asset {
        asset_id: equity_asset.clone(),
        code: ticker.into(),
        asset_class: AssetClass::Equity,
        status: "active".into(),
        ..Asset::default()
    });
    catalog.assets.push(Asset {
        asset_id: fiat_asset.clone(),
        code: quote.into(),
        asset_class: AssetClass::Fiat,
        status: "active".into(),
        ..Asset::default()
    });
    let instrument_id =
        kairos_primitives::InstrumentId::new(format!("instrument:equity:US:{ticker}:common"))?;
    if catalog
        .instruments
        .iter()
        .any(|value| value.instrument_id == instrument_id)
    {
        return Ok(());
    }
    catalog.instruments.push(Instrument {
        instrument_id: instrument_id.clone(),
        symbol: kairos_primitives::Symbol::new(ticker.to_owned())?,
        instrument_type: InstrumentKind::Equity,
        issuer_id: Some(kairos_primitives::IssuerId::new(format!(
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
    match exchange_id {
        "exchange:nasdaq" => "Nasdaq",
        "exchange:nyse" => "NYSE",
        "exchange:amex" => "NYSE American",
        "exchange:cboe-bzx-options" => "Cboe BZX Options Exchange",
        _ => "Exchange",
    }
}
