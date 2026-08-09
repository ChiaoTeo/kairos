//! Provider sources and in-memory implementations for the Reference actor.

use std::collections::BTreeMap;
use std::sync::mpsc::{self, Receiver, SyncSender, TrySendError};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use kairos_integration::application::reference::{
    ReferenceCatalogPayload, ReferenceDataConnection,
};
use kairos_integration::application::{
    AccessScope, AssetType, IntegrationCapability, ProductFamily, TransportKind,
};
use kairos_integration::{ConnectionSpec, Integration, IntegrationRoute};

use super::storage::ProviderSyncStore;
use crate::domain::{
    Asset, Entity, ExecutionAccess, FinancialProduct, Instrument, Listing, Market, ProviderCatalog,
    ProviderHealth, ReferenceError, ReferenceResult,
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
        })
    }

    fn provider_health(&self) -> Vec<ProviderHealth> {
        Vec::new()
    }
}

pub(crate) struct ProviderUpdate {
    pub catalog: ProviderCatalog,
    pub complete: bool,
}

fn reference_spec(
    connection_id: &str,
    route: IntegrationRoute,
    product: Option<ProductFamily>,
    asset_type: Option<AssetType>,
) -> ConnectionSpec {
    ConnectionSpec {
        connection_id: connection_id.into(),
        route,
        product,
        access: AccessScope::Public,
        transport: TransportKind::Rest,
        capability: IntegrationCapability::Reference,
        credential_id: None,
        asset_type,
    }
}

pub(crate) fn reference_spec_for_public(
    route: IntegrationRoute,
    product: ProductFamily,
    asset_type: Option<AssetType>,
) -> ConnectionSpec {
    reference_spec("reference.public", route, Some(product), asset_type)
}

/// Binance Spot public reference source.
///
/// The provider connection and vendor normalization live in integration. This
/// source only maps the neutral integration payload into Reference-owned
/// domain records.
pub struct BinanceSpotSource {
    connection: Box<dyn ReferenceDataConnection>,
}

pub struct BinanceOptionsSource {
    connection: Box<dyn ReferenceDataConnection>,
}

pub struct BinanceEquitySource {
    connection: Box<dyn ReferenceDataConnection>,
}

pub struct MassiveSource {
    connection: Box<dyn ReferenceDataConnection>,
    cursor: Option<String>,
    accumulated: Option<ProviderCatalog>,
    sync_store: Option<Box<dyn ProviderSyncStore>>,
}

pub struct MassiveEquitySource {
    connection: Box<dyn ReferenceDataConnection>,
}

pub struct HyperliquidSource {
    connection: Box<dyn ReferenceDataConnection>,
}

pub struct PublicSource {
    id: String,
    connection: Box<dyn ReferenceDataConnection>,
}

/// Reference-owned fan-in for the global catalog. Each provider remains an
/// independent integration connection; this source only merges normalized
/// records before they enter the Reference actor.
pub struct CompositeSource {
    workers: Vec<ProviderWorker>,
    last_good: BTreeMap<String, ProviderCatalog>,
    health: BTreeMap<String, ProviderHealth>,
}

struct ProviderWorker {
    source_id: String,
    requests: SyncSender<ProviderRequest>,
    retry_after: Option<Instant>,
}

struct ProviderRequest {
    response: SyncSender<ReferenceResult<ProviderUpdate>>,
}

const PROVIDER_FETCH_TIMEOUT: Duration = Duration::from_secs(45);

impl CompositeSource {
    pub fn new(sources: Vec<Box<dyn ReferenceSource>>) -> ReferenceResult<Self> {
        if sources.is_empty() {
            return Err(ReferenceError::Provider(
                "reference catalog has no sources".into(),
            ));
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
                            let started = Instant::now();
                            let result = loop {
                                match source.fetch_catalog_step() {
                                    Ok(update) if update.complete => break Ok(update),
                                    Ok(_) if started.elapsed() >= PROVIDER_FETCH_TIMEOUT => {
                                        break Err(ReferenceError::Provider(
                                            "provider pagination timed out".into(),
                                        ));
                                    }
                                    Ok(_) => continue,
                                    Err(error) => break Err(error),
                                }
                            };
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
            last_good: BTreeMap::new(),
            health: BTreeMap::new(),
        })
    }
}

impl ReferenceSource for CompositeSource {
    fn source_id(&self) -> &str {
        "reference-default"
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
            health.last_attempt_unix_nanos = Some(unix_nanos());
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
                "reference provider fetch completed"
            );
            let catalog = match result {
                Ok(update) => {
                    successful_sources += 1;
                    self.mark_success(&source_id);
                    let catalog = if update.complete {
                        update.catalog
                    } else {
                        merge_provider_catalog(self.last_good.get(&source_id), update.catalog)
                    };
                    self.last_good.insert(source_id.clone(), catalog);
                    self.last_good
                        .get(&source_id)
                        .expect("inserted provider snapshot")
                }
                Err(error) => {
                    failures.push(format!("{source_id}: {error}"));
                    self.mark_failure(&source_id, "stale");
                    let Some(catalog) = self.last_good.get(&source_id) else {
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
        health.last_success_unix_nanos = Some(unix_nanos());
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
}

fn unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

impl PublicSource {
    pub fn new(id: impl Into<String>, connection: Box<dyn ReferenceDataConnection>) -> Self {
        Self {
            id: id.into(),
            connection,
        }
    }
}

impl ReferenceSource for PublicSource {
    fn source_id(&self) -> &str {
        &self.id
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let payload = self
            .connection
            .fetch_reference_catalog()
            .map_err(ReferenceError::Provider)?;
        provider_catalog_from_integration(payload)
    }
}

impl BinanceSpotSource {
    pub fn new(endpoint: impl Into<String>) -> ReferenceResult<Self> {
        let integration = Integration::new()
            .with_binance_reference(endpoint)
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        let connection = integration
            .connect_reference(&reference_spec(
                "reference.binance.spot.rest",
                IntegrationRoute::exchange("binance"),
                Some(ProductFamily::Spot),
                None,
            ))
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        Ok(Self { connection })
    }
}

impl BinanceOptionsSource {
    pub fn new(endpoint: impl Into<String>) -> ReferenceResult<Self> {
        let integration = Integration::new()
            .with_binance_options_reference(endpoint)
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        let connection = integration
            .connect_reference(&reference_spec(
                "reference.binance.options.rest",
                IntegrationRoute::exchange("binance"),
                Some(ProductFamily::Options),
                None,
            ))
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        Ok(Self { connection })
    }
}

impl BinanceEquitySource {
    pub fn new(api_key: impl Into<String>, secret: impl Into<String>) -> ReferenceResult<Self> {
        let integration = Integration::new().with_binance_equity(api_key, secret);
        let connection = integration
            .connect_reference(&reference_spec(
                "reference.binance.equity.rest",
                IntegrationRoute::exchange("binance"),
                Some(ProductFamily::Equity),
                Some(AssetType::Equity),
            ))
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
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
        let integration = Integration::new()
            .with_massive_reference_for_underlying(api_key, base_url, underlying)
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        let connection = integration
            .connect_reference(&reference_spec(
                "reference.massive.market",
                IntegrationRoute::data_provider("massive"),
                None,
                Some(AssetType::Equity),
            ))
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
        let integration = Integration::new()
            .with_massive_equity_reference(api_key, base_url)
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        let connection = integration
            .connect_reference(&reference_spec(
                "reference.massive.equity",
                IntegrationRoute::data_provider("massive"),
                Some(ProductFamily::Equity),
                Some(AssetType::Equity),
            ))
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        Ok(Self { connection })
    }
}

impl HyperliquidSource {
    pub fn new(endpoint: impl Into<String>) -> ReferenceResult<Self> {
        let integration = Integration::new().with_hyperliquid_reference(endpoint);
        let connection = integration
            .connect_reference(&reference_spec(
                "reference.hyperliquid.info",
                IntegrationRoute::exchange("hyperliquid"),
                Some(ProductFamily::UsdMFutures),
                Some(AssetType::Crypto),
            ))
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        Ok(Self { connection })
    }
}

impl ReferenceSource for BinanceSpotSource {
    fn source_id(&self) -> &str {
        "binance-spot"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let payload = self
            .connection
            .fetch_reference_catalog()
            .map_err(ReferenceError::Provider)?;
        provider_catalog_from_integration(payload)
    }
}

impl ReferenceSource for BinanceOptionsSource {
    fn source_id(&self) -> &str {
        "binance-options"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let payload = self
            .connection
            .fetch_reference_catalog()
            .map_err(ReferenceError::Provider)?;
        provider_catalog_from_integration(payload)
    }
}

impl ReferenceSource for BinanceEquitySource {
    fn source_id(&self) -> &str {
        "binance-equity"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let payload = self
            .connection
            .fetch_reference_catalog()
            .map_err(ReferenceError::Provider)?;
        provider_catalog_from_integration(payload)
    }
}

impl ReferenceSource for MassiveSource {
    fn source_id(&self) -> &str {
        "massive-options"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let payload = self
            .connection
            .fetch_reference_catalog()
            .map_err(ReferenceError::Provider)?;
        provider_catalog_from_integration(payload)
    }

    fn fetch_catalog_step(&mut self) -> ReferenceResult<ProviderUpdate> {
        let page = self
            .connection
            .fetch_reference_catalog_page(self.cursor.as_deref(), 1000)
            .map_err(ReferenceError::Provider)?;
        self.cursor = page.next_cursor;
        let page_catalog = provider_catalog_from_integration(page.catalog)?;
        let catalog = merge_provider_catalog(self.accumulated.as_ref(), page_catalog);
        if page.complete {
            self.cursor = None;
            self.accumulated = None;
        } else {
            self.accumulated = Some(catalog.clone());
        }
        if let Some(store) = self.sync_store.as_mut() {
            store.save_state(
                "massive-options",
                self.cursor.as_deref(),
                self.accumulated.as_ref(),
            )?;
        }
        Ok(ProviderUpdate {
            catalog,
            complete: page.complete,
        })
    }
}

impl ReferenceSource for MassiveEquitySource {
    fn source_id(&self) -> &str {
        "massive-equity"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let payload = self
            .connection
            .fetch_reference_catalog()
            .map_err(ReferenceError::Provider)?;
        provider_catalog_from_integration(payload)
    }
}

impl ReferenceSource for HyperliquidSource {
    fn source_id(&self) -> &str {
        "hyperliquid"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let payload = self
            .connection
            .fetch_reference_catalog()
            .map_err(ReferenceError::Provider)?;
        provider_catalog_from_integration(payload)
    }
}

fn provider_catalog_from_integration(
    payload: ReferenceCatalogPayload,
) -> ReferenceResult<ProviderCatalog> {
    let instruments: Vec<Instrument> = payload
        .instruments
        .into_iter()
        .map(|value| Instrument {
            instrument_id: value.instrument_id,
            symbol: value.symbol,
            instrument_type: value.instrument_type,
            product_family: value.product_family,
            underlying_instrument_id: value.underlying_instrument_id,
            expiry_unix_nanos: value.expiry_unix_nanos,
            strike: value.strike,
            option_right: value.option_right,
            status: value.status,
            ..Instrument::default()
        })
        .collect();
    let underlying_by_instrument: BTreeMap<_, _> = instruments
        .iter()
        .filter_map(|value| {
            value
                .underlying_instrument_id
                .as_ref()
                .map(|underlying| (value.instrument_id.clone(), underlying.clone()))
        })
        .collect();
    Ok(ProviderCatalog {
        entities: payload
            .entities
            .into_iter()
            .map(|value| Entity {
                entity_id: value.entity_id,
                entity_type: value.entity_type,
                name: value.name,
                status: value.status,
            })
            .collect(),
        assets: payload
            .assets
            .into_iter()
            .map(|value| Asset {
                asset_id: value.asset_id,
                code: value.code,
                asset_class: value.asset_class,
                status: value.status,
                ..Asset::default()
            })
            .collect(),
        instruments,
        listings: payload
            .listings
            .into_iter()
            .map(|value| Listing {
                listing_id: value.listing_id,
                instrument_id: value.instrument_id,
                venue_id: value.venue_id,
                venue_symbol: value.venue_symbol,
                status: value.status,
                effective_from_unix_nanos: value.effective_from_unix_nanos,
                ..Listing::default()
            })
            .collect(),
        markets: payload
            .markets
            .into_iter()
            .map(|value| {
                let instrument_id = value.instrument_id.clone();
                Market {
                    market_id: value.market_id,
                    market_key: value.market_key,
                    instrument_id,
                    listing_id: value.listing_id,
                    venue_id: value.venue_id,
                    market_type: value.market_type,
                    asset_type: value.asset_type,
                    underlying_instrument_id: underlying_by_instrument
                        .get(&value.instrument_id)
                        .cloned(),
                    source_symbol: value.source_symbol,
                    base_asset_id: value.base_asset_id,
                    quote_asset_id: value.quote_asset_id,
                    status: value.status,
                    price_tick: value.price_tick,
                    quantity_tick: value.quantity_tick,
                    price_precision: value.price_precision,
                    quantity_precision: value.quantity_precision,
                    minimum_quantity: value.minimum_quantity,
                    minimum_notional: value.minimum_notional,
                    contract_size: value.contract_size,
                    effective_to_unix_nanos: value.effective_to_unix_nanos,
                    ..Market::default()
                }
            })
            .collect(),
        financial_products: payload
            .financial_products
            .into_iter()
            .map(|value| FinancialProduct {
                product_id: value.product_id,
                product_type: value.product_type,
                name: value.name,
                asset_id: value.asset_id,
                provider_product_id: value.provider_product_id,
                provider_id: value.provider_id,
                issuer_id: value.issuer_id,
                currency_asset_id: value.currency_asset_id,
                min_amount: value.min_amount,
                max_amount: value.max_amount,
                apr: value.apr,
                lock_period_days: value.lock_period_days,
                maturity_at_unix_nanos: value.maturity_at_unix_nanos,
                status: value.status,
                effective_from_unix_nanos: value.effective_from_unix_nanos,
                effective_to_unix_nanos: value.effective_to_unix_nanos,
            })
            .collect(),
        execution_accesses: payload
            .execution_accesses
            .into_iter()
            .map(|value| ExecutionAccess {
                access_id: value.access_id,
                instrument_id: value.instrument_id,
                provider_id: value.provider_id,
                product_family: value.product_family,
                provider_symbol: value.provider_symbol,
                settlement_asset_id: value.settlement_asset_id,
                status: value.status,
                effective_from_unix_nanos: value.effective_from_unix_nanos,
                effective_to_unix_nanos: value.effective_to_unix_nanos,
            })
            .collect(),
    })
}

fn merge_provider_catalog(
    previous: Option<&ProviderCatalog>,
    incoming: ProviderCatalog,
) -> ProviderCatalog {
    let mut merged = previous.cloned().unwrap_or_default();
    for value in incoming.entities {
        upsert_vec(&mut merged.entities, value.entity_id.clone(), value, |v| {
            &v.entity_id
        });
    }
    for value in incoming.assets {
        upsert_vec(&mut merged.assets, value.asset_id.clone(), value, |v| {
            &v.asset_id
        });
    }
    for value in incoming.instruments {
        upsert_vec(
            &mut merged.instruments,
            value.instrument_id.clone(),
            value,
            |v| &v.instrument_id,
        );
    }
    for value in incoming.listings {
        upsert_vec(&mut merged.listings, value.listing_id.clone(), value, |v| {
            &v.listing_id
        });
    }
    for value in incoming.markets {
        upsert_vec(&mut merged.markets, value.market_id.clone(), value, |v| {
            &v.market_id
        });
    }
    for value in incoming.financial_products {
        upsert_vec(
            &mut merged.financial_products,
            value.product_id.clone(),
            value,
            |v| &v.product_id,
        );
    }
    for value in incoming.execution_accesses {
        upsert_vec(
            &mut merged.execution_accesses,
            value.access_id.clone(),
            value,
            |v| &v.access_id,
        );
    }
    merged
}

fn upsert_vec<T>(values: &mut Vec<T>, id: String, value: T, key: impl Fn(&T) -> &String) {
    if let Some(existing) = values.iter_mut().find(|existing| key(existing) == &id) {
        *existing = value;
    } else {
        values.push(value);
    }
}

#[cfg(test)]
mod tests {
    use super::{CompositeSource, ReferenceSource};
    use crate::domain::{Market, ProviderCatalog, ReferenceResult};
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    };

    struct FlakySource {
        calls: Arc<AtomicUsize>,
    }

    struct PagedSource {
        calls: usize,
    }

    impl ReferenceSource for FlakySource {
        fn source_id(&self) -> &str {
            "test-flaky"
        }

        fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
            if self.calls.fetch_add(1, Ordering::SeqCst) == 0 {
                Ok(ProviderCatalog {
                    markets: vec![Market {
                        market_id: "market:test".into(),
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
                market_id: "market:page-1".into(),
                status: "active".into(),
                ..Default::default()
            }];
            if self.calls >= 2 {
                markets.push(Market {
                    market_id: "market:page-2".into(),
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
            })
        }
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
        assert_eq!(calls.load(Ordering::SeqCst), 2);
        assert_eq!(source.provider_health()[0].status, "stale");
        assert!(source.provider_health()[0].stale);
    }

    #[test]
    fn partial_provider_pages_do_not_drop_previous_page() {
        let mut source = CompositeSource::new(vec![Box::new(PagedSource { calls: 0 })]).unwrap();
        let first = source.fetch_catalog().unwrap();
        assert!(first
            .markets
            .iter()
            .any(|market| market.market_id == "market:page-1"));
        let second = source.fetch_catalog().unwrap();
        assert_eq!(second.markets.len(), 2);
        assert!(second
            .markets
            .iter()
            .any(|market| market.market_id == "market:page-1"));
    }
}
