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
    connection: kairos_integration::participants::massive::MassiveInstrumentCatalog,
    cursor: Option<String>,
    legacy_accumulated: Option<ProviderCatalog>,
}

pub struct MassiveEquitySource {
    connection: kairos_integration::participants::massive::MassiveInstrumentCatalog,
    cursor: Option<String>,
    accumulated: Option<ProviderCatalog>,
    sync_store: SqlxProviderSyncStore,
}
impl MassiveOptionsCoverageSource {
    pub async fn new(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
        sync_store: SqlxProviderSyncStore,
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
        if self.sync_store.prepare_projection(&key).await? {
            tracing::info!(
                event = "reference_provider_projection_reset",
                component = "reference",
                provider = %key,
                projection_version = crate::services::sqlx_storage::PROVIDER_PROJECTION_VERSION,
                "unfinished provider scan was reset for the current canonical projection"
            );
        }
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

impl MassiveEquitySource {
    fn without_state(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
        sync_store: SqlxProviderSyncStore,
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
        mut sync_store: SqlxProviderSyncStore,
    ) -> ReferenceResult<Self> {
        #[cfg(not(test))]
        if !sync_store.supports_normalized_promotion() {
            return Err(ReferenceError::Persistence(
                "production Massive equity ingestion requires normalized SQLite promotion".into(),
            ));
        }
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

#[async_trait::async_trait]
impl ReferenceSource for MassiveOptionsCoverageSource {
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

#[async_trait::async_trait]
impl ReferenceSource for MassiveEquitySource {
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
