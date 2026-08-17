//! Composition shared by the one-shot CLI and the long-running server.

mod config;
mod datasets;

pub use config::{
    ReferenceConfig, ReferenceParticipantConfig, ReferenceProductConfig, ReferenceProviderConfig,
};

pub use datasets::{
    prepare_massive_cash_dividends, prepare_massive_option_contract_snapshot,
    MassiveReferenceDatasetConfig,
};

use std::path::Path;

use crate::domain::ReferenceResult;
use crate::services::providers::{
    BinanceDerivativesSource, BinanceEquitySource, BinanceOptionsSource, BinanceSpotSource,
    CompositeSource, HyperliquidSource, MassiveEquitySource, MassiveOptionsCoverageSource,
    OkxSource, ParticipantAugmentedSource, ProviderUpdate, ReferenceSource,
};
use crate::services::sqlx_storage::{SqlxCatalogStore, SqlxProviderSyncStore};
use crate::ReferenceApplication;

use kairos_integration::application::credential::load_workspace_credential;
use kairos_integration::participants::binance::InstrumentType as BinanceInstrumentType;
use kairos_integration::participants::okx::InstrumentType as OkxInstrumentType;
use kairos_protocol::InstanceIdentity;
use kairos_reference_contract::{EncodeContext, ReferenceEncoder, ReferenceSqliteReader};
use kairos_transport::AeronBytePublisher;

impl From<kairos_reference_contract::ContractError> for crate::domain::ReferenceError {
    fn from(error: kairos_reference_contract::ContractError) -> Self {
        Self::Publication(error.to_string())
    }
}

#[derive(Clone, Debug)]
pub struct ReferenceCompositionConfig {
    pub workspace: Option<std::path::PathBuf>,
    pub database: std::path::PathBuf,
    pub aeron_dir: Option<String>,
    pub aeron_channel: String,
    pub reference_changes_stream: i32,
}

/// Build the same business application for both process modes.
///
/// Read-only CLI commands use `publish = false`, while refresh/publish and the
/// server use the real Aeron publisher. The provider and store are always the
/// production implementations; the disabled publisher is only for local
/// catalog inspection where no media driver is required.
pub struct ReferenceComposition {
    pub application: ComposedReferenceApplication,
    pub event_writer: Option<ReferenceEventWriter>,
}

pub type ComposedReferenceApplication =
    ReferenceApplication<ConfiguredReferenceSource, SqlxCatalogStore>;

type ProductionComposite = CompositeSource<ConfiguredProviderSource, SqlxProviderSyncStore>;

pub struct ConfiguredReferenceSource {
    inner: ParticipantAugmentedSource<ProductionComposite>,
}

enum ConfiguredProviderSource {
    BinanceSpot(BinanceSpotSource),
    BinanceDerivatives(BinanceDerivativesSource),
    BinanceOptions(BinanceOptionsSource),
    BinanceEquity(BinanceEquitySource),
    Okx(OkxSource),
    Hyperliquid(HyperliquidSource),
    MassiveEquity(MassiveEquitySource<SqlxProviderSyncStore>),
    MassiveOptions(MassiveOptionsCoverageSource<SqlxProviderSyncStore>),
}

impl ReferenceSource for ConfiguredProviderSource {
    fn source_id(&self) -> &str {
        match self {
            Self::BinanceSpot(source) => source.source_id(),
            Self::BinanceDerivatives(source) => source.source_id(),
            Self::BinanceOptions(source) => source.source_id(),
            Self::BinanceEquity(source) => source.source_id(),
            Self::Okx(source) => source.source_id(),
            Self::Hyperliquid(source) => source.source_id(),
            Self::MassiveEquity(source) => source.source_id(),
            Self::MassiveOptions(source) => source.source_id(),
        }
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<crate::domain::ProviderCatalog> {
        match self {
            Self::BinanceSpot(source) => source.fetch_catalog().await,
            Self::BinanceDerivatives(source) => source.fetch_catalog().await,
            Self::BinanceOptions(source) => source.fetch_catalog().await,
            Self::BinanceEquity(source) => source.fetch_catalog().await,
            Self::Okx(source) => source.fetch_catalog().await,
            Self::Hyperliquid(source) => source.fetch_catalog().await,
            Self::MassiveEquity(source) => source.fetch_catalog().await,
            Self::MassiveOptions(source) => source.fetch_catalog().await,
        }
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<ProviderUpdate> {
        match self {
            Self::BinanceSpot(source) => source.fetch_catalog_step().await,
            Self::BinanceDerivatives(source) => source.fetch_catalog_step().await,
            Self::BinanceOptions(source) => source.fetch_catalog_step().await,
            Self::BinanceEquity(source) => source.fetch_catalog_step().await,
            Self::Okx(source) => source.fetch_catalog_step().await,
            Self::Hyperliquid(source) => source.fetch_catalog_step().await,
            Self::MassiveEquity(source) => source.fetch_catalog_step().await,
            Self::MassiveOptions(source) => source.fetch_catalog_step().await,
        }
    }

    async fn set_option_underlying(
        &mut self,
        underlying: &str,
        enabled: bool,
    ) -> ReferenceResult<()> {
        match self {
            Self::MassiveOptions(source) => source.set_option_underlying(underlying, enabled).await,
            _ => Err(crate::domain::ReferenceError::Invalid(format!(
                "{} does not support option coverage",
                self.source_id()
            ))),
        }
    }

    fn option_underlyings(&self) -> Vec<String> {
        match self {
            Self::MassiveOptions(source) => source.option_underlyings(),
            _ => Vec::new(),
        }
    }
}

impl ReferenceSource for ConfiguredReferenceSource {
    fn source_id(&self) -> &str {
        self.inner.source_id()
    }

    fn normalized_facts_authoritative(&self) -> bool {
        self.inner.normalized_facts_authoritative()
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<crate::domain::ProviderCatalog> {
        self.inner.fetch_catalog().await
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<ProviderUpdate> {
        self.inner.fetch_catalog_step().await
    }

    async fn advance_source(
        &mut self,
        source_id: &str,
    ) -> ReferenceResult<Option<crate::domain::ProviderCatalog>> {
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

    fn provider_health(&self) -> Vec<crate::domain::ProviderHealth> {
        self.inner.provider_health()
    }
}

pub struct ReferenceEventWriter {
    publisher: kairos_transport::AeronBytePublisher,
    reader: ReferenceSqliteReader,
}

pub struct ReferenceCurrentViewPublisher {
    inner: kairos_reference_contract::MmapReferenceLatestPublisher,
    identity: InstanceIdentity,
}

impl ReferenceCurrentViewPublisher {
    pub fn create(
        root: impl AsRef<Path>,
        slot_capacity: usize,
        actor_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> ReferenceResult<Self> {
        Ok(Self {
            inner: kairos_reference_contract::MmapReferenceLatestPublisher::create(
                root,
                actor_id,
                slot_capacity,
            )?,
            identity,
        })
    }

    pub fn publish(&mut self, view: &crate::ReferenceCurrentView) -> ReferenceResult<()> {
        self.inner
            .publish(&reference_contract_view(view, &self.identity))?;
        Ok(())
    }
}

fn reference_contract_view(
    view: &crate::ReferenceCurrentView,
    identity: &InstanceIdentity,
) -> kairos_reference_contract::ReferenceLatestSnapshot {
    use kairos_reference_contract as contract;
    let catalog = &view.catalog;
    contract::ReferenceLatestSnapshot {
        actor_id: view.actor_id.clone(),
        workspace_id: identity.workspace_id.clone(),
        launch_id: (!identity.launch_id.is_empty()).then(|| identity.launch_id.clone()),
        instance_id: (!identity.instance_id.is_empty()).then(|| identity.instance_id.clone()),
        generation: view.generation.get(),
        event_sequence: view.event_sequence.get(),
        entities: catalog
            .entities
            .values()
            .map(|value| contract::Entity {
                entity_id: value.entity_id.clone(),
                entity_type: value.entity_type.clone(),
                name: value.name.clone(),
                status: value.status.as_str().into(),
            })
            .collect(),
        assets: catalog
            .assets
            .values()
            .map(|value| contract::Asset {
                asset_id: value.asset_id.to_string(),
                code: value.code.clone(),
                name: value.name.clone(),
                asset_class: value.asset_class,
                status: value.status.as_str().into(),
            })
            .collect(),
        instruments: catalog
            .instruments
            .values()
            .map(|value| contract::Instrument {
                instrument_id: value.instrument_id.to_string(),
                symbol: value.symbol.to_string(),
                name: value.name.clone(),
                instrument_type: value.instrument_type,
                product_family: None,
                issuer_id: value.issuer_id.as_ref().map(ToString::to_string),
                share_class: value.share_class.clone(),
                primary_currency_asset_id: value
                    .primary_currency_asset_id
                    .as_ref()
                    .map(ToString::to_string),
                underlying_instrument_id: value
                    .underlying_instrument_id
                    .as_ref()
                    .map(ToString::to_string),
                expiry_unix_nanos: value.expiry_unix_nanos.map(|value| value.get()),
                strike: value.strike.clone(),
                option_right: value.option_right.clone(),
                status: value.status.as_str().into(),
            })
            .collect(),
        listings: catalog
            .listings
            .values()
            .map(|value| contract::Listing {
                listing_id: value.listing_id.to_string(),
                instrument_id: value.instrument_id.to_string(),
                exchange_id: value.exchange_id.to_string(),
                exchange_symbol: value.exchange_symbol.to_string(),
                status: value.status.as_str().into(),
                effective_from_unix_nanos: value.effective_from_unix_nanos.get(),
                effective_to_unix_nanos: value.effective_to_unix_nanos.map(|value| value.get()),
            })
            .collect(),
        markets: catalog
            .markets
            .values()
            .map(|value| contract::Market {
                market_id: value.market_id.to_string(),
                market_key: value.market_key.clone(),
                instrument_id: value.instrument_id.to_string(),
                listing_id: value.listing_id.to_string(),
                exchange_id: value.exchange_id.to_string(),
                market_type: value.market_type.clone(),
                asset_type: value.asset_type,
                underlying_instrument_id: value
                    .underlying_instrument_id
                    .as_ref()
                    .map(ToString::to_string),
                source_symbol: value.source_symbol.to_string(),
                base_asset_id: value.base_asset_id.as_ref().map(ToString::to_string),
                quote_asset_id: value.quote_asset_id.as_ref().map(ToString::to_string),
                status: value.status.as_str().into(),
                price_tick: value.price_tick.clone(),
                quantity_tick: value.quantity_tick.clone(),
                price_precision: value.price_precision,
                quantity_precision: value.quantity_precision,
                minimum_quantity: value.minimum_quantity.clone(),
                minimum_notional: value.minimum_notional.clone(),
                contract_size: value.contract_size.clone(),
                effective_from_unix_nanos: value.effective_from_unix_nanos.get(),
                effective_to_unix_nanos: value.effective_to_unix_nanos.map(|value| value.get()),
            })
            .collect(),
        financial_products: catalog
            .financial_products
            .values()
            .map(|value| contract::FinancialProduct {
                product_id: value.product_id.clone(),
                product_type: value.product_type.clone(),
                name: value.name.clone(),
                asset_id: value.asset_id.to_string(),
                provider_product_id: value.provider_product_id.clone(),
                provider_id: value.provider_id.clone(),
                issuer_id: value.issuer_id.as_ref().map(ToString::to_string),
                currency_asset_id: value.currency_asset_id.as_ref().map(ToString::to_string),
                min_amount: value.min_amount.clone(),
                max_amount: value.max_amount.clone(),
                apr: value.apr.clone(),
                lock_period_days: value.lock_period_days,
                maturity_at_unix_nanos: value.maturity_at_unix_nanos.map(|value| value.get()),
                status: value.status.as_str().into(),
                effective_from_unix_nanos: value.effective_from_unix_nanos.get(),
                effective_to_unix_nanos: value.effective_to_unix_nanos.map(|value| value.get()),
            })
            .collect(),
        execution_accesses: catalog
            .execution_accesses
            .values()
            .map(|value| contract::ExecutionAccess {
                access_id: value.access_id.to_string(),
                routing_mode: value.routing_mode.clone(),
                instrument_id: value.instrument_id.as_ref().map(ToString::to_string),
                listing_id: value.listing_id.as_ref().map(ToString::to_string),
                market_id: value.market_id.as_ref().map(ToString::to_string),
                destination_market_id: value
                    .destination_market_id
                    .as_ref()
                    .map(ToString::to_string),
                broker_id: value.broker_id.clone(),
                provider_id: value.provider_id.to_string(),
                provider_product: value.provider_product.to_string(),
                provider_symbol: value.provider_symbol.to_string(),
                settlement_asset_id: value.settlement_asset_id.as_ref().map(ToString::to_string),
                status: value.status.as_str().into(),
                effective_from_unix_nanos: value.effective_from_unix_nanos.get(),
                effective_to_unix_nanos: value.effective_to_unix_nanos.map(|value| value.get()),
            })
            .collect(),
        market_data_accesses: catalog
            .market_data_accesses
            .values()
            .map(|value| contract::MarketDataAccess {
                access_id: value.access_id.clone(),
                market_id: value.market_id.to_string(),
                provider_id: value.provider_id.to_string(),
                provider_product: value.provider_product.to_string(),
                provider_symbol: value.provider_symbol.to_string(),
                status: value.status.as_str().into(),
                effective_from_unix_nanos: value.effective_from_unix_nanos.get(),
                effective_to_unix_nanos: value.effective_to_unix_nanos.map(|value| value.get()),
            })
            .collect(),
        provider_health: view
            .provider_health
            .iter()
            .map(|value| contract::ProviderHealthState {
                provider_id: value.source_id.clone(),
                status: value.status.clone(),
                message: (value.consecutive_failures > 0)
                    .then(|| format!("consecutive_failures={}", value.consecutive_failures)),
                updated_at_unix_nanos: value
                    .last_attempt_unix_nanos
                    .or(value.last_success_unix_nanos)
                    .map(|value| value.get())
                    .unwrap_or_default(),
            })
            .collect(),
        option_underlyings: view.option_underlyings.clone(),
        lifecycle_events: catalog
            .lifecycle_events
            .iter()
            .rev()
            .take(4096)
            .rev()
            .map(|value| contract::LifecycleEntry {
                event_id: value.event_id.clone(),
                event_type: value.event_type.clone(),
                event_time_unix_nanos: value.event_time_unix_nanos.get(),
                record_kind: value.record_kind.clone(),
                record_id: value.record_id.clone(),
            })
            .collect(),
    }
}

#[derive(Clone, Debug)]
pub struct ReferenceEventWriterConfig {
    pub aeron_dir: Option<String>,
    pub aeron_channel: String,
    pub reference_changes_stream: i32,
    pub database: std::path::PathBuf,
}

/// Canonical provider endpoint defaults shared by the one-shot CLI and the
/// long-running Reference server.
pub fn default_endpoint(provider: &str) -> &'static str {
    match provider {
        "hyperliquid" => "https://api.hyperliquid.xyz/info",
        "binance-spot" | "binance-spot-rest" => "https://api.binance.com",
        "binance-equity" | "binance-equity-rest" => "https://api.binance.com",
        "binance-options" | "binance-options-rest" => "https://eapi.binance.com",
        "binance-usdm-futures" | "binance-usdm-futures-rest" => {
            "https://fapi.binance.com/fapi/v1/exchangeInfo"
        }
        "binance-coinm-futures" | "binance-coinm-futures-rest" => {
            "https://dapi.binance.com/dapi/v1/exchangeInfo"
        }
        "okx-spot" | "okx-margin" | "okx-equity" | "okx-swap" | "okx-futures" | "okx-options"
        | "okx-spot-rest" | "okx-margin-rest" | "okx-swap-rest" | "okx-futures-rest"
        | "okx-options-rest" => "https://www.okx.com",
        "massive"
        | "massive-equity"
        | "massive-equity-websocket"
        | "massive-options"
        | "massive-options-websocket" => "http://api.massiveprivateserver.site",
        _ => "",
    }
}

/// Build the normal Workspace Reference catalog.
///
/// Reference owns the source selection for the global catalog. Public Binance,
/// OKX, and Hyperliquid products are built in; credentialed providers such as
/// Massive are added only when explicitly enabled in
/// `[reference.providers.*]`. Every provider can be explicitly disabled there.
async fn build_default_source(
    config: &ReferenceCompositionConfig,
) -> ReferenceResult<ConfiguredReferenceSource> {
    let workspace = config
        .workspace
        .as_ref()
        .map(kairos_workspace::workspace::Workspace::open)
        .transpose()
        .map_err(|error| crate::domain::ReferenceError::Provider(error.to_string()))?;
    let reference = workspace
        .as_ref()
        .map(ReferenceConfig::load)
        .transpose()
        .map_err(crate::domain::ReferenceError::Provider)?;
    let reference = reference.as_ref();

    let mut sources = Vec::new();
    if !provider_disabled(reference, "binance")
        && product_enabled_or_default(reference, "binance", "spot", true)
    {
        sources.push(ConfiguredProviderSource::BinanceSpot(
            BinanceSpotSource::new(product_endpoint(
                reference,
                "binance",
                "spot",
                default_endpoint("binance-spot"),
            ))?,
        ));
    }
    if !provider_disabled(reference, "binance")
        && product_enabled_or_default(reference, "binance", "usd-m-futures", true)
    {
        sources.push(ConfiguredProviderSource::BinanceDerivatives(
            BinanceDerivativesSource::new(
                BinanceInstrumentType::UsdMFutures,
                product_endpoint(
                    reference,
                    "binance",
                    "usd-m-futures",
                    default_endpoint("binance-usdm-futures"),
                ),
            )?,
        ));
    }
    if !provider_disabled(reference, "binance")
        && product_enabled_or_default(reference, "binance", "coin-m-futures", true)
    {
        sources.push(ConfiguredProviderSource::BinanceDerivatives(
            BinanceDerivativesSource::new(
                BinanceInstrumentType::CoinMFutures,
                product_endpoint(
                    reference,
                    "binance",
                    "coin-m-futures",
                    default_endpoint("binance-coinm-futures"),
                ),
            )?,
        ));
    }
    if !provider_disabled(reference, "binance")
        && product_enabled_or_default(reference, "binance", "options", true)
    {
        sources.push(ConfiguredProviderSource::BinanceOptions(
            BinanceOptionsSource::new(product_endpoint(
                reference,
                "binance",
                "options",
                default_endpoint("binance-options"),
            ))?,
        ));
    }

    let credentials_root = config
        .workspace
        .as_ref()
        .map(|root| root.join("credentials"));
    if !provider_disabled(reference, "okx") {
        for (product, source_id, instrument_type) in [
            ("spot", "okx-spot", OkxInstrumentType::Spot),
            ("margin", "okx-margin", OkxInstrumentType::Margin),
            ("swap", "okx-swap", OkxInstrumentType::Swap),
            ("futures", "okx-futures", OkxInstrumentType::Futures),
            ("options", "okx-options", OkxInstrumentType::Option),
        ] {
            if product_enabled_or_default(reference, "okx", product, true) {
                sources.push(ConfiguredProviderSource::Okx(OkxSource::new(
                    source_id,
                    instrument_type,
                    product_endpoint(reference, "okx", product, default_endpoint(source_id)),
                )?));
            }
        }
    }
    if !provider_disabled(reference, "hyperliquid") {
        if product_enabled_or_default(reference, "hyperliquid", "perpetual", true) {
            sources.push(ConfiguredProviderSource::Hyperliquid(
                HyperliquidSource::new(product_endpoint(
                    reference,
                    "hyperliquid",
                    "perpetual",
                    default_endpoint("hyperliquid"),
                ))?,
            ));
        }
        if product_enabled_or_default(reference, "hyperliquid", "spot", true) {
            sources.push(ConfiguredProviderSource::Hyperliquid(
                HyperliquidSource::spot(product_endpoint(
                    reference,
                    "hyperliquid",
                    "spot",
                    default_endpoint("hyperliquid"),
                ))?,
            ));
        }
    }

    let massive_credential = credentials_root.as_deref().and_then(|root| {
        load_workspace_credential(
            root,
            "massive",
            provider_config(reference, "massive").and_then(|value| value.credential_id.as_deref()),
        )
        .ok()
        .flatten()
    });
    let massive_enabled = provider_enabled(reference, "massive");
    if massive_enabled {
        let credential = massive_credential.ok_or_else(|| {
            crate::domain::ReferenceError::Provider(
                "Reference Massive source is enabled but its credential is missing".into(),
            )
        })?;
        if credential.api_key.trim().is_empty() {
            return Err(crate::domain::ReferenceError::Provider(
                "Reference Massive source is enabled but its API key is missing".into(),
            ));
        }
        let endpoint = provider_endpoint(reference, "massive", default_endpoint("massive"));
        if product_enabled_or_default(reference, "massive", "equity", true) {
            let equity_sync_store = SqlxProviderSyncStore::open(&config.database).await?;
            sources.push(ConfiguredProviderSource::MassiveEquity(
                MassiveEquitySource::new_with_sync_store(
                    credential.api_key.clone(),
                    endpoint.clone(),
                    equity_sync_store,
                )
                .await?,
            ));
        }
        if product_enabled_or_default(reference, "massive", "options", true) {
            // Stock-options coverage is explicit and mutable at runtime. Do
            // not make a global options reference scan the default just
            // because Massive can enumerate it.
            let sync_store = SqlxProviderSyncStore::open(&config.database).await?;
            sources.push(ConfiguredProviderSource::MassiveOptions(
                MassiveOptionsCoverageSource::new(credential.api_key, endpoint, sync_store).await?,
            ));
        }
    }

    if product_enabled(reference, "binance", "equity") {
        let product = product_config(reference, "binance", "equity");
        let credential_id = product.and_then(|value| value.credential_id.as_deref());
        let credential = credentials_root
            .as_deref()
            .and_then(|root| {
                load_workspace_credential(root, "binance", credential_id)
                    .ok()
                    .flatten()
            })
            .ok_or_else(|| {
                crate::domain::ReferenceError::Provider(
                    "Reference Binance equity source is enabled but its credential is missing"
                        .into(),
                )
            })?;
        if credential.api_key.trim().is_empty() {
            return Err(crate::domain::ReferenceError::Provider(
                "Reference Binance equity source is enabled but its API key is missing".into(),
            ));
        }
        let endpoint = product
            .and_then(|value| value.endpoint.clone())
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| default_endpoint("binance-equity").to_owned());
        sources.push(ConfiguredProviderSource::BinanceEquity(
            BinanceEquitySource::new(
                endpoint,
                secrecy::SecretString::new(credential.api_key.into()),
            )?,
        ));
    }

    let sync_store = SqlxProviderSyncStore::open(&config.database).await?;
    let source = CompositeSource::new_with_sync_store(sources, Some(sync_store)).await?;
    let mut participants = vec![
        configured_provider("binance", "Binance"),
        configured_provider("hyperliquid", "Hyperliquid"),
    ];
    if !provider_disabled(reference, "okx") {
        participants.push(configured_provider("okx", "OKX"));
    }
    if let Some(reference) = reference {
        for (id, participant) in &reference.participants {
            if participant.enabled != Some(false) {
                if participant.entity_type.trim().is_empty() || participant.name.trim().is_empty() {
                    return Err(crate::domain::ReferenceError::Provider(format!(
                        "reference participant {id} requires type and name"
                    )));
                }
                participants.push(crate::domain::Entity {
                    source_id: None,
                    entity_id: format!("{}:{id}", participant.entity_type),
                    entity_type: participant.entity_type.clone(),
                    name: participant.name.clone(),
                    status: "active".into(),
                });
            }
        }
    }
    Ok(ConfiguredReferenceSource {
        inner: ParticipantAugmentedSource::wrap(source, participants),
    })
}

fn configured_provider(id: &str, name: &str) -> crate::domain::Entity {
    crate::domain::Entity {
        source_id: None,
        entity_id: format!("data_provider:{id}"),
        entity_type: "data_provider".into(),
        name: name.into(),
        status: "active".into(),
    }
}

fn provider_config<'a>(
    reference: Option<&'a ReferenceConfig>,
    provider: &str,
) -> Option<&'a ReferenceProviderConfig> {
    reference.and_then(|value| value.providers.get(provider))
}

fn provider_enabled(reference: Option<&ReferenceConfig>, provider: &str) -> bool {
    provider_config(reference, provider)
        .and_then(|value| value.enabled)
        .unwrap_or(false)
}

fn provider_disabled(reference: Option<&ReferenceConfig>, provider: &str) -> bool {
    provider_config(reference, provider).and_then(|value| value.enabled) == Some(false)
}

fn provider_endpoint(reference: Option<&ReferenceConfig>, provider: &str, default: &str) -> String {
    provider_config(reference, provider)
        .and_then(|value| value.endpoint.clone())
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| default.to_owned())
}

fn product_endpoint(
    reference: Option<&ReferenceConfig>,
    provider: &str,
    product: &str,
    default: &str,
) -> String {
    product_config(reference, provider, product)
        .and_then(|value| value.endpoint.clone())
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| provider_endpoint(reference, provider, default))
}

fn product_config<'a>(
    reference: Option<&'a ReferenceConfig>,
    provider: &str,
    product: &str,
) -> Option<&'a ReferenceProductConfig> {
    reference
        .and_then(|value| value.products.get(provider))
        .and_then(|value| value.get(product))
}

fn product_enabled_or_default(
    reference: Option<&ReferenceConfig>,
    provider: &str,
    product: &str,
    default: bool,
) -> bool {
    product_config(reference, provider, product)
        .and_then(|value| value.enabled)
        .unwrap_or(default)
}

fn product_enabled(reference: Option<&ReferenceConfig>, provider: &str, product: &str) -> bool {
    product_config(reference, provider, product)
        .and_then(|value| value.enabled)
        .unwrap_or(false)
}

impl ReferenceEventWriter {
    pub fn connect(config: &ReferenceEventWriterConfig) -> ReferenceResult<Self> {
        Ok(Self {
            publisher: AeronBytePublisher::connect(
                config.aeron_dir.as_deref(),
                &config.aeron_channel,
                config.reference_changes_stream,
            )
            .map_err(|error| crate::domain::ReferenceError::Publication(error.to_string()))?,
            reader: ReferenceSqliteReader::open(&config.database)?,
        })
    }

    pub fn publish(
        &mut self,
        generation: kairos_primitives::Generation,
        current_event_sequence: kairos_primitives::Sequence,
        events: &[crate::domain::LifecycleEvent],
    ) -> ReferenceResult<()> {
        for event in events {
            let record_kind = event.record_kind.as_deref().ok_or_else(|| {
                crate::domain::ReferenceError::Publication(
                    "Reference event is missing record_kind".into(),
                )
            })?;
            let record_id = event.record_id.as_deref().ok_or_else(|| {
                crate::domain::ReferenceError::Publication(
                    "Reference event is missing record_id".into(),
                )
            })?;
            let sequence = event
                .event_id
                .rsplit(':')
                .next()
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(current_event_sequence.get());
            let context = EncodeContext::event(
                "reference-actor",
                InstanceIdentity::default(),
                sequence,
                event.event_id.clone(),
                generation.get(),
            );
            let updated = !event.event_type.ends_with("_added") && event.event_type != "listed";
            let payload = match record_kind {
                "asset" => {
                    let record = self
                        .reader
                        .asset(record_id)?
                        .ok_or_else(|| missing(record_kind, record_id))?;
                    if updated {
                        ReferenceEncoder::asset_updated(
                            &record,
                            &context,
                            event.event_time_unix_nanos.get(),
                        )?
                    } else {
                        ReferenceEncoder::asset_upserted(
                            &record,
                            &context,
                            event.event_time_unix_nanos.get(),
                        )?
                    }
                }
                "entity" => {
                    let record = self
                        .reader
                        .entity(record_id)?
                        .ok_or_else(|| missing(record_kind, record_id))?;
                    if updated {
                        ReferenceEncoder::entity_updated(
                            &record,
                            &context,
                            event.event_time_unix_nanos.get(),
                        )?
                    } else {
                        ReferenceEncoder::entity_upserted(
                            &record,
                            &context,
                            event.event_time_unix_nanos.get(),
                        )?
                    }
                }
                "instrument" => {
                    let record = self
                        .reader
                        .instrument(record_id)?
                        .ok_or_else(|| missing(record_kind, record_id))?;
                    if updated {
                        ReferenceEncoder::instrument_updated(
                            &record,
                            &context,
                            event.event_time_unix_nanos.get(),
                        )?
                    } else {
                        ReferenceEncoder::instrument_upserted(
                            &record,
                            &context,
                            event.event_time_unix_nanos.get(),
                        )?
                    }
                }
                "listing" => {
                    let record = self
                        .reader
                        .listing(record_id)?
                        .ok_or_else(|| missing(record_kind, record_id))?;
                    if updated {
                        ReferenceEncoder::listing_updated(
                            &record,
                            &context,
                            event.event_time_unix_nanos.get(),
                        )?
                    } else {
                        ReferenceEncoder::listing_upserted(
                            &record,
                            &context,
                            event.event_time_unix_nanos.get(),
                        )?
                    }
                }
                "market" => {
                    let record = self
                        .reader
                        .market(record_id)?
                        .ok_or_else(|| missing(record_kind, record_id))?;
                    let record = kairos_reference_contract::Market {
                        market_id: record.market_id,
                        market_key: record.market_key,
                        instrument_id: record.instrument_id,
                        listing_id: record.listing_id,
                        exchange_id: record.exchange_id,
                        market_type: kairos_primitives::ProviderProductCode::new(
                            record.market_type,
                        )
                        .map_err(|error| {
                            crate::domain::ReferenceError::Publication(error.to_string())
                        })?,
                        asset_type: record
                            .asset_type
                            .map(|value| value.parse::<kairos_primitives::AssetClass>())
                            .transpose()
                            .map_err(|error| {
                                crate::domain::ReferenceError::Publication(error.to_string())
                            })?,
                        underlying_instrument_id: record.underlying_instrument_id,
                        source_symbol: record.source_symbol,
                        base_asset_id: record.base_asset_id,
                        quote_asset_id: record.quote_asset_id,
                        status: record.status,
                        price_tick: record.price_tick,
                        quantity_tick: record.quantity_tick,
                        price_precision: record.price_precision,
                        quantity_precision: record.quantity_precision,
                        minimum_quantity: record.minimum_quantity,
                        minimum_notional: record.minimum_notional,
                        contract_size: record.contract_size,
                        effective_from_unix_nanos: record.effective_from_unix_nanos,
                        effective_to_unix_nanos: record.effective_to_unix_nanos,
                    };
                    if updated {
                        ReferenceEncoder::market_updated(
                            &record,
                            &context,
                            event.event_time_unix_nanos.get(),
                        )?
                    } else {
                        ReferenceEncoder::market_upserted(
                            &record,
                            &context,
                            event.event_time_unix_nanos.get(),
                        )?
                    }
                }
                "financial_product" => {
                    let record = self
                        .reader
                        .financial_product(record_id)?
                        .ok_or_else(|| missing(record_kind, record_id))?;
                    if updated {
                        ReferenceEncoder::financial_product_updated(
                            &record,
                            &context,
                            event.event_time_unix_nanos.get(),
                        )?
                    } else {
                        ReferenceEncoder::financial_product_upserted(
                            &record,
                            &context,
                            event.event_time_unix_nanos.get(),
                        )?
                    }
                }
                "execution_access" => {
                    let record = self
                        .reader
                        .execution_access(record_id)?
                        .ok_or_else(|| missing(record_kind, record_id))?;
                    if updated {
                        ReferenceEncoder::execution_access_updated(
                            &record,
                            &context,
                            event.event_time_unix_nanos.get(),
                        )?
                    } else {
                        ReferenceEncoder::execution_access_upserted(
                            &record,
                            &context,
                            event.event_time_unix_nanos.get(),
                        )?
                    }
                }
                "market_data_access" => {
                    let record = self
                        .reader
                        .market_data_access(record_id)?
                        .ok_or_else(|| missing(record_kind, record_id))?;
                    if updated {
                        ReferenceEncoder::market_data_access_updated(
                            &record,
                            &context,
                            event.event_time_unix_nanos.get(),
                        )?
                    } else {
                        ReferenceEncoder::market_data_access_upserted(
                            &record,
                            &context,
                            event.event_time_unix_nanos.get(),
                        )?
                    }
                }
                other => {
                    return Err(crate::domain::ReferenceError::Publication(format!(
                        "Reference v2 event schema is not defined for record kind {other}"
                    )))
                }
            };
            self.publisher
                .publish(&payload)
                .map_err(|error| crate::domain::ReferenceError::Publication(error.to_string()))?;
        }
        Ok(())
    }
}

fn missing(kind: &str, id: &str) -> crate::domain::ReferenceError {
    crate::domain::ReferenceError::Publication(format!(
        "Reference SQLite record missing: {kind}:{id}"
    ))
}

pub async fn build_application(
    config: &ReferenceCompositionConfig,
    publish: bool,
) -> ReferenceResult<ReferenceComposition> {
    if config.reference_changes_stream != kairos_transport::stream_ids::REFERENCE_CHANGES {
        return Err(crate::domain::ReferenceError::Invalid(format!(
            "reference changes stream must be the registered stream {}",
            kairos_transport::stream_ids::REFERENCE_CHANGES
        )));
    }
    let source = build_default_source(config).await?;
    let store = SqlxCatalogStore::open(&config.database).await?;
    let event_writer = if publish {
        Some(ReferenceEventWriter::connect(
            &ReferenceEventWriterConfig {
                aeron_dir: config.aeron_dir.clone(),
                aeron_channel: config.aeron_channel.clone(),
                reference_changes_stream: config.reference_changes_stream,
                database: config.database.clone(),
            },
        )?)
    } else {
        None
    };
    Ok(ReferenceComposition {
        application: ReferenceApplication::new("reference-actor", source, store).await?,
        event_writer,
    })
}

pub fn ensure_database_parent(path: &Path) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    Ok(())
}
