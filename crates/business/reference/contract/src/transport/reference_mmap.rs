//! File-backed mmap publication for Reference read models.

use std::path::PathBuf;

use crate::encoding::FlatbuffersSnapshotEncoder;
use crate::model::{
    Asset, Entity, ExecutionAccess, FinancialProduct, Instrument, Listing, Market, ReferenceCatalog,
};
use crate::projection::ReferenceMarket;
use crate::snapshot::{SnapshotEnvelope, SnapshotPublisher, SnapshotReader};
use crate::transport::{MmapSnapshotPublisher, MmapSnapshotReader};
use kairos_protocol::generated::kairos::reference::v_1::{
    catalog_snapshot_buffer_has_identifier, markets_snapshot_buffer_has_identifier,
    reference_collections_snapshot_buffer_has_identifier, root_as_catalog_snapshot,
    root_as_markets_snapshot, root_as_reference_collections_snapshot,
};
use kairos_protocol::InstanceIdentity;

use crate::{ContractError, ContractResult};

pub struct ReferenceMmapSnapshotWriter {
    catalog: MmapSnapshotPublisher,
    entities: MmapSnapshotPublisher,
    assets: MmapSnapshotPublisher,
    instruments: MmapSnapshotPublisher,
    listings: MmapSnapshotPublisher,
    markets: MmapSnapshotPublisher,
    financial_products: MmapSnapshotPublisher,
    execution_accesses: MmapSnapshotPublisher,
    encoder: FlatbuffersSnapshotEncoder,
    manifest_path: PathBuf,
    last_generation: Option<u64>,
}

#[derive(Clone, Debug)]
pub struct ReferenceMmapSnapshotConfig {
    pub catalog_path: PathBuf,
    pub entities_path: PathBuf,
    pub assets_path: PathBuf,
    pub instruments_path: PathBuf,
    pub listings_path: PathBuf,
    pub markets_path: PathBuf,
    pub financial_products_path: PathBuf,
    pub execution_accesses_path: PathBuf,
    pub slot_size: usize,
    pub actor_id: String,
    pub event_stream_id: String,
    pub identity: InstanceIdentity,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceMarketsSnapshot {
    pub view_key: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub markets: Vec<ReferenceMarket>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceSnapshotSet {
    pub generation: u64,
    pub event_sequence: u64,
    pub catalog: ReferenceCatalog,
    pub entities: Vec<Entity>,
    pub assets: Vec<Asset>,
    pub instruments: Vec<Instrument>,
    pub listings: Vec<Listing>,
    pub markets: Vec<Market>,
    pub financial_products: Vec<FinancialProduct>,
    pub execution_accesses: Vec<ExecutionAccess>,
}

pub struct ReferenceMmapSnapshotSetReader {
    manifest_path: PathBuf,
    catalog: MmapSnapshotReader,
    entities: MmapSnapshotReader,
    assets: MmapSnapshotReader,
    instruments: MmapSnapshotReader,
    listings: MmapSnapshotReader,
    markets: MmapSnapshotReader,
    financial_products: MmapSnapshotReader,
    execution_accesses: MmapSnapshotReader,
}

impl ReferenceMmapSnapshotSetReader {
    pub fn open(root: impl AsRef<std::path::Path>) -> ContractResult<Self> {
        let root = root.as_ref();
        let reader = |name: &str, view_key: &str| {
            MmapSnapshotReader::open(
                root.join(name),
                view_key,
                "reference",
                "reference.lifecycle",
            )
        };
        Ok(Self {
            manifest_path: root.join("reference.manifest"),
            catalog: reader("catalog.snapshot", "reference.catalog")?,
            entities: reader("entities.snapshot", "reference.entities")?,
            assets: reader("assets.snapshot", "reference.assets")?,
            instruments: reader("instruments.snapshot", "reference.instruments")?,
            listings: reader("listings.snapshot", "reference.listings")?,
            markets: reader("markets.snapshot", "reference.markets")?,
            financial_products: reader(
                "financial-products.snapshot",
                "reference.financial_products",
            )?,
            execution_accesses: reader(
                "execution-accesses.snapshot",
                "reference.execution_accesses",
            )?,
        })
    }

    pub fn read(&self) -> ContractResult<ReferenceSnapshotSet> {
        let manifest: SnapshotManifest =
            serde_json::from_slice(&std::fs::read(&self.manifest_path).map_err(|error| {
                ContractError::Transport(format!(
                    "read Reference snapshot manifest {}: {error}",
                    self.manifest_path.display()
                ))
            })?)
            .map_err(|error| {
                ContractError::Invalid(format!("decode Reference snapshot manifest: {error}"))
            })?;
        manifest.validate()?;

        let catalog_payload = read_envelope(&self.catalog)?;
        let catalog_root = checked_catalog(&catalog_payload.payload)?;
        let catalog_header = catalog_root.header();
        let metadata = SnapshotMetadata::from_header(catalog_header);
        validate_metadata(&metadata, &manifest)?;
        let mut catalog = decode_catalog(catalog_root.payload());
        catalog.generation = metadata.generation;
        catalog.event_sequence = metadata.event_sequence;

        let entities = decode_collection_view(
            &read_envelope(&self.entities)?,
            "reference.entities",
            CollectionKind::Entities,
            &manifest,
        )?;
        let assets = decode_collection_view(
            &read_envelope(&self.assets)?,
            "reference.assets",
            CollectionKind::Assets,
            &manifest,
        )?;
        let instruments = decode_collection_view(
            &read_envelope(&self.instruments)?,
            "reference.instruments",
            CollectionKind::Instruments,
            &manifest,
        )?;
        let listings = decode_collection_view(
            &read_envelope(&self.listings)?,
            "reference.listings",
            CollectionKind::Listings,
            &manifest,
        )?;
        let markets = decode_markets_view(&read_envelope(&self.markets)?, &manifest)?;
        let financial_products = decode_collection_view(
            &read_envelope(&self.financial_products)?,
            "reference.financial_products",
            CollectionKind::FinancialProducts,
            &manifest,
        )?;
        let execution_accesses = decode_collection_view(
            &read_envelope(&self.execution_accesses)?,
            "reference.execution_accesses",
            CollectionKind::ExecutionAccesses,
            &manifest,
        )?;

        if catalog.entities.len() != entities.len()
            || catalog.assets.len() != assets.len()
            || catalog.instruments.len() != instruments.len()
            || catalog.listings.len() != listings.len()
            || catalog.markets.len() != markets.len()
            || catalog.financial_products.len() != financial_products.len()
            || catalog.execution_accesses.len() != execution_accesses.len()
        {
            return Err(ContractError::Invalid(
                "Reference snapshot views disagree with catalog counts".into(),
            ));
        }
        Ok(ReferenceSnapshotSet {
            generation: metadata.generation,
            event_sequence: metadata.event_sequence,
            catalog,
            entities,
            assets,
            instruments,
            listings,
            markets,
            financial_products,
            execution_accesses,
        })
    }
}

#[derive(serde::Deserialize)]
struct SnapshotManifest {
    schema_version: u16,
    generation: u64,
    event_sequence: u64,
    views: Vec<String>,
}

impl SnapshotManifest {
    fn validate(&self) -> ContractResult<()> {
        if self.schema_version != 1 {
            return Err(ContractError::Invalid(format!(
                "unsupported Reference snapshot manifest schema version {}",
                self.schema_version
            )));
        }
        let mut views = self.views.clone();
        views.sort();
        let mut expected = vec![
            "reference.assets",
            "reference.catalog",
            "reference.entities",
            "reference.execution_accesses",
            "reference.financial_products",
            "reference.instruments",
            "reference.listings",
            "reference.markets",
        ];
        expected.sort();
        if views != expected {
            return Err(ContractError::Invalid(
                "Reference snapshot manifest does not advertise exactly eight views".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Copy)]
struct SnapshotMetadata {
    generation: u64,
    event_sequence: u64,
}

impl SnapshotMetadata {
    fn from_header(
        header: kairos_protocol::generated::kairos::common::v_1::SnapshotHeader<'_>,
    ) -> Self {
        Self {
            generation: header.generation(),
            event_sequence: header.event_sequence(),
        }
    }
}

fn validate_metadata(
    metadata: &SnapshotMetadata,
    manifest: &SnapshotManifest,
) -> ContractResult<()> {
    if metadata.generation != manifest.generation
        || metadata.event_sequence != manifest.event_sequence
    {
        return Err(ContractError::Invalid(
            "Reference snapshot manifest watermark does not match catalog view".into(),
        ));
    }
    Ok(())
}

fn read_envelope(reader: &MmapSnapshotReader) -> ContractResult<SnapshotEnvelope> {
    reader
        .read()
        .map_err(|error| ContractError::Transport(error.to_string()))
}

fn checked_catalog(
    payload: &[u8],
) -> ContractResult<kairos_protocol::generated::kairos::reference::v_1::CatalogSnapshot<'_>> {
    if !catalog_snapshot_buffer_has_identifier(payload) {
        return Err(ContractError::Invalid(
            "Reference catalog snapshot has an invalid file identifier".into(),
        ));
    }
    root_as_catalog_snapshot(payload).map_err(|error| {
        ContractError::Invalid(format!("decode Reference catalog snapshot: {error}"))
    })
}

#[derive(Clone, Copy)]
enum CollectionKind {
    Entities,
    Assets,
    Instruments,
    Listings,
    FinancialProducts,
    ExecutionAccesses,
}

fn decode_collection_view<T>(
    envelope: &SnapshotEnvelope,
    view_key: &str,
    kind: CollectionKind,
    manifest: &SnapshotManifest,
) -> ContractResult<Vec<T>>
where
    T: CollectionDecode,
{
    let payload = envelope.payload.as_slice();
    if !reference_collections_snapshot_buffer_has_identifier(payload) {
        return Err(ContractError::Invalid(format!(
            "Reference {view_key} snapshot has an invalid file identifier"
        )));
    }
    let root = root_as_reference_collections_snapshot(payload).map_err(|error| {
        ContractError::Invalid(format!("decode Reference {view_key} snapshot: {error}"))
    })?;
    let header = root.header();
    let metadata = SnapshotMetadata::from_header(header);
    validate_metadata(&metadata, manifest)?;
    if header.view_key() != view_key {
        return Err(ContractError::Invalid(format!(
            "Reference snapshot view key is {}, expected {view_key}",
            header.view_key()
        )));
    }
    T::decode(root.payload(), kind)
}

fn decode_markets_view(
    envelope: &SnapshotEnvelope,
    manifest: &SnapshotManifest,
) -> ContractResult<Vec<Market>> {
    let payload = envelope.payload.as_slice();
    if !markets_snapshot_buffer_has_identifier(payload) {
        return Err(ContractError::Invalid(
            "Reference markets snapshot has an invalid file identifier".into(),
        ));
    }
    let root = root_as_markets_snapshot(payload).map_err(|error| {
        ContractError::Invalid(format!("decode Reference markets snapshot: {error}"))
    })?;
    let header = root.header();
    let metadata = SnapshotMetadata::from_header(header);
    validate_metadata(&metadata, manifest)?;
    if header.view_key() != "reference.markets" {
        return Err(ContractError::Invalid(format!(
            "Reference markets snapshot view key is {}",
            header.view_key()
        )));
    }
    Ok(root
        .payload()
        .markets()
        .map(|values| values.iter().map(decode_market).collect())
        .unwrap_or_default())
}

trait CollectionDecode: Sized {
    fn decode(
        payload: kairos_protocol::generated::kairos::reference::v_1::ReferenceCollections<'_>,
        kind: CollectionKind,
    ) -> ContractResult<Vec<Self>>;
}

impl CollectionDecode for Entity {
    fn decode(
        payload: kairos_protocol::generated::kairos::reference::v_1::ReferenceCollections<'_>,
        kind: CollectionKind,
    ) -> ContractResult<Vec<Self>> {
        if !matches!(kind, CollectionKind::Entities) {
            return Err(ContractError::Invalid(
                "Reference collection kind mismatch".into(),
            ));
        }
        Ok(payload
            .entities()
            .map(|values| {
                values
                    .iter()
                    .map(|value| Entity {
                        entity_id: value.entity_id().to_owned(),
                        entity_type: value.entity_type().to_owned(),
                        name: value.name().to_owned(),
                        status: value.status().unwrap_or_default().to_owned(),
                    })
                    .collect()
            })
            .unwrap_or_default())
    }
}

impl CollectionDecode for Asset {
    fn decode(
        payload: kairos_protocol::generated::kairos::reference::v_1::ReferenceCollections<'_>,
        kind: CollectionKind,
    ) -> ContractResult<Vec<Self>> {
        if !matches!(kind, CollectionKind::Assets) {
            return Err(ContractError::Invalid(
                "Reference collection kind mismatch".into(),
            ));
        }
        Ok(payload
            .assets()
            .map(|values| {
                values
                    .iter()
                    .map(|value| Asset {
                        asset_id: value.asset_id().to_owned(),
                        code: value.code().to_owned(),
                        name: value.name().map(str::to_owned),
                        asset_class: value.asset_class().unwrap_or_default().to_owned(),
                        status: value.status().to_owned(),
                    })
                    .collect()
            })
            .unwrap_or_default())
    }
}

impl CollectionDecode for Instrument {
    fn decode(
        payload: kairos_protocol::generated::kairos::reference::v_1::ReferenceCollections<'_>,
        kind: CollectionKind,
    ) -> ContractResult<Vec<Self>> {
        if !matches!(kind, CollectionKind::Instruments) {
            return Err(ContractError::Invalid(
                "Reference collection kind mismatch".into(),
            ));
        }
        Ok(payload
            .instruments()
            .map(|values| values.iter().map(decode_instrument).collect())
            .unwrap_or_default())
    }
}

impl CollectionDecode for Listing {
    fn decode(
        payload: kairos_protocol::generated::kairos::reference::v_1::ReferenceCollections<'_>,
        kind: CollectionKind,
    ) -> ContractResult<Vec<Self>> {
        if !matches!(kind, CollectionKind::Listings) {
            return Err(ContractError::Invalid(
                "Reference collection kind mismatch".into(),
            ));
        }
        Ok(payload
            .listings()
            .map(|values| values.iter().map(decode_listing).collect())
            .unwrap_or_default())
    }
}

impl CollectionDecode for FinancialProduct {
    fn decode(
        payload: kairos_protocol::generated::kairos::reference::v_1::ReferenceCollections<'_>,
        kind: CollectionKind,
    ) -> ContractResult<Vec<Self>> {
        if !matches!(kind, CollectionKind::FinancialProducts) {
            return Err(ContractError::Invalid(
                "Reference collection kind mismatch".into(),
            ));
        }
        Ok(payload
            .financial_products()
            .map(|values| values.iter().map(decode_financial_product).collect())
            .unwrap_or_default())
    }
}

impl CollectionDecode for ExecutionAccess {
    fn decode(
        payload: kairos_protocol::generated::kairos::reference::v_1::ReferenceCollections<'_>,
        kind: CollectionKind,
    ) -> ContractResult<Vec<Self>> {
        if !matches!(kind, CollectionKind::ExecutionAccesses) {
            return Err(ContractError::Invalid(
                "Reference collection kind mismatch".into(),
            ));
        }
        Ok(payload
            .execution_accesses()
            .map(|values| values.iter().map(decode_execution_access).collect())
            .unwrap_or_default())
    }
}

fn decode_market(value: kairos_protocol::generated::kairos::reference::v_1::Market<'_>) -> Market {
    Market {
        market_id: value.market_id().to_owned(),
        market_key: value.market_key().to_owned(),
        instrument_id: value.instrument_id().to_owned(),
        listing_id: value.listing_id().to_owned(),
        exchange_id: value.exchange_id().to_owned(),
        market_type: value.market_type().to_owned(),
        asset_type: value.asset_type().map(str::to_owned),
        underlying_instrument_id: value.underlying_instrument_id().map(str::to_owned),
        source_symbol: value.source_symbol().to_owned(),
        base_asset_id: value.base_asset_id().map(str::to_owned),
        quote_asset_id: value.quote_asset_id().map(str::to_owned),
        status: value.status().to_owned(),
        price_tick: value.price_tick().map(decimal_string),
        quantity_tick: value.quantity_tick().map(decimal_string),
        price_precision: value.price_precision(),
        quantity_precision: value.quantity_precision(),
        minimum_quantity: value.minimum_quantity().map(decimal_string),
        minimum_notional: value.minimum_notional().map(decimal_string),
        contract_size: value.contract_size().map(decimal_string),
        effective_from_unix_nanos: value.effective_from_unix_nanos(),
        effective_to_unix_nanos: nonzero_option(value.effective_to_unix_nanos()),
    }
}

fn decode_instrument(
    value: kairos_protocol::generated::kairos::reference::v_1::Instrument<'_>,
) -> Instrument {
    Instrument {
        instrument_id: value.instrument_id().to_owned(),
        symbol: value.symbol().to_owned(),
        name: value.name().map(str::to_owned),
        instrument_type: value.instrument_type().to_owned(),
        product_family: value.product_family().map(str::to_owned),
        underlying_instrument_id: value.underlying_instrument_id().map(str::to_owned),
        expiry_unix_nanos: nonzero_option(value.expiry_unix_nanos()),
        strike: value.strike().map(decimal_string),
        option_right: value.option_right().map(str::to_owned),
        issuer_id: value.issuer_id().map(str::to_owned),
        share_class: value.share_class().map(str::to_owned),
        primary_currency_asset_id: value.primary_currency_asset_id().map(str::to_owned),
        status: value.status().to_owned(),
    }
}

fn decode_listing(
    value: kairos_protocol::generated::kairos::reference::v_1::Listing<'_>,
) -> Listing {
    Listing {
        listing_id: value.listing_id().to_owned(),
        instrument_id: value.instrument_id().to_owned(),
        exchange_id: value.exchange_id().to_owned(),
        exchange_symbol: value.exchange_symbol().to_owned(),
        status: value.status().to_owned(),
        effective_from_unix_nanos: value.effective_from_unix_nanos(),
        effective_to_unix_nanos: nonzero_option(value.effective_to_unix_nanos()),
    }
}

fn decode_financial_product(
    value: kairos_protocol::generated::kairos::reference::v_1::FinancialProduct<'_>,
) -> FinancialProduct {
    FinancialProduct {
        product_id: value.product_id().to_owned(),
        product_type: value.product_type().to_owned(),
        name: value.name().to_owned(),
        asset_id: value.asset_id().to_owned(),
        provider_product_id: value.provider_product_id().to_owned(),
        provider_id: value.provider_id().map(str::to_owned),
        issuer_id: value.issuer_id().map(str::to_owned),
        currency_asset_id: value.currency_asset_id().map(str::to_owned),
        min_amount: value.min_amount().map(decimal_string),
        max_amount: value.max_amount().map(decimal_string),
        apr: value.apr().map(decimal_string),
        lock_period_days: value.lock_period_days(),
        maturity_at_unix_nanos: nonzero_option(value.maturity_at_unix_nanos()),
        status: value.status().to_owned(),
        effective_from_unix_nanos: value.effective_from_unix_nanos(),
        effective_to_unix_nanos: nonzero_option(value.effective_to_unix_nanos()),
    }
}

fn decode_execution_access(
    value: kairos_protocol::generated::kairos::reference::v_1::ExecutionAccess<'_>,
) -> ExecutionAccess {
    ExecutionAccess {
        access_id: value.access_id().to_owned(),
        market_id: value.market_id().to_owned(),
        provider_id: value.provider_id().to_owned(),
        product_family: value.product_family().to_owned(),
        provider_symbol: value.provider_symbol().to_owned(),
        settlement_asset_id: value.settlement_asset_id().map(str::to_owned),
        status: value.status().to_owned(),
        effective_from_unix_nanos: value.effective_from_unix_nanos(),
        effective_to_unix_nanos: nonzero_option(value.effective_to_unix_nanos()),
    }
}

fn decode_catalog(
    payload: kairos_protocol::generated::kairos::reference::v_1::Catalog<'_>,
) -> ReferenceCatalog {
    let entities = payload
        .entities()
        .map(|values| {
            values
                .iter()
                .map(|value| {
                    let entity = Entity {
                        entity_id: value.entity_id().to_owned(),
                        entity_type: value.entity_type().to_owned(),
                        name: value.name().to_owned(),
                        status: value.status().unwrap_or_default().to_owned(),
                    };
                    (entity.entity_id.clone(), entity)
                })
                .collect()
        })
        .unwrap_or_default();
    let assets = payload
        .assets()
        .map(|values| {
            values
                .iter()
                .map(|value| {
                    let asset = Asset {
                        asset_id: value.asset_id().to_owned(),
                        code: value.code().to_owned(),
                        name: value.name().map(str::to_owned),
                        asset_class: value.asset_class().unwrap_or_default().to_owned(),
                        status: value.status().to_owned(),
                    };
                    (asset.asset_id.clone(), asset)
                })
                .collect()
        })
        .unwrap_or_default();
    let instruments = payload
        .instruments()
        .map(|values| {
            values
                .iter()
                .map(|value| {
                    let instrument = decode_instrument(value);
                    (instrument.instrument_id.clone(), instrument)
                })
                .collect()
        })
        .unwrap_or_default();
    let listings = payload
        .listings()
        .map(|values| {
            values
                .iter()
                .map(|value| {
                    let listing = decode_listing(value);
                    (listing.listing_id.clone(), listing)
                })
                .collect()
        })
        .unwrap_or_default();
    let markets = payload
        .markets()
        .map(|values| {
            values
                .iter()
                .map(|value| {
                    let market = decode_market(value);
                    (market.market_id.clone(), market)
                })
                .collect()
        })
        .unwrap_or_default();
    let financial_products = payload
        .financial_products()
        .map(|values| {
            values
                .iter()
                .map(|value| {
                    let product = decode_financial_product(value);
                    (product.product_id.clone(), product)
                })
                .collect()
        })
        .unwrap_or_default();
    let execution_accesses = payload
        .execution_accesses()
        .map(|values| {
            values
                .iter()
                .map(|value| {
                    let access = decode_execution_access(value);
                    (access.access_id.clone(), access)
                })
                .collect()
        })
        .unwrap_or_default();
    ReferenceCatalog {
        entities,
        assets,
        instruments,
        listings,
        markets,
        financial_products,
        execution_accesses,
        lifecycle_events: Vec::new(),
        generation: 0,
        event_sequence: 0,
    }
}

pub struct ReferenceMmapMarketsReader {
    inner: MmapSnapshotReader,
}

impl ReferenceMmapMarketsReader {
    pub fn open(
        path: impl AsRef<std::path::Path>,
        producer_id: impl Into<String>,
        event_stream_id: impl Into<String>,
    ) -> ContractResult<Self> {
        Ok(Self {
            inner: MmapSnapshotReader::open(
                path,
                "reference.markets",
                producer_id,
                event_stream_id,
            )?,
        })
    }

    pub fn read(&self) -> ContractResult<ReferenceMarketsSnapshot> {
        let envelope = self
            .inner
            .read()
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        if !markets_snapshot_buffer_has_identifier(&envelope.payload) {
            return Err(ContractError::Invalid(
                "Reference markets snapshot has an invalid file identifier".into(),
            ));
        }
        let root = root_as_markets_snapshot(&envelope.payload).map_err(|error| {
            ContractError::Invalid(format!("decode Reference markets snapshot: {error}"))
        })?;
        let header = root.header();
        if header.view_key() != "reference.markets" {
            return Err(ContractError::Invalid(format!(
                "Reference markets snapshot has unexpected view key {}",
                header.view_key()
            )));
        }
        let markets = root
            .payload()
            .markets()
            .map(|values| {
                values
                    .iter()
                    .map(|market| ReferenceMarket {
                        market_id: market.market_id().to_owned(),
                        source_id: None,
                        market_key: market.market_key().to_owned(),
                        instrument_id: market.instrument_id().to_owned(),
                        listing_id: market.listing_id().to_owned(),
                        exchange_id: market.exchange_id().to_owned(),
                        market_type: market.market_type().to_owned(),
                        asset_type: market.asset_type().map(str::to_owned),
                        source_symbol: market.source_symbol().to_owned(),
                        base_asset_id: market.base_asset_id().map(str::to_owned),
                        quote_asset_id: market.quote_asset_id().map(str::to_owned),
                        underlying_instrument_id: market
                            .underlying_instrument_id()
                            .map(str::to_owned),
                        status: market.status().to_owned(),
                        price_tick: market.price_tick().map(decimal_string),
                        quantity_tick: market.quantity_tick().map(decimal_string),
                        minimum_quantity: market.minimum_quantity().map(decimal_string),
                        minimum_notional: market.minimum_notional().map(decimal_string),
                        price_precision: market.price_precision(),
                        quantity_precision: market.quantity_precision(),
                        contract_size: market.contract_size().map(decimal_string),
                        effective_from_unix_nanos: market.effective_from_unix_nanos(),
                        effective_to_unix_nanos: nonzero_option(market.effective_to_unix_nanos()),
                    })
                    .collect()
            })
            .unwrap_or_default();
        Ok(ReferenceMarketsSnapshot {
            view_key: header.view_key().to_owned(),
            generation: header.generation(),
            event_sequence: header.event_sequence(),
            markets,
        })
    }
}

fn nonzero_option(value: u64) -> Option<u64> {
    (value != 0).then_some(value)
}

fn decimal_string(value: &kairos_protocol::generated::kairos::common::v_1::Decimal64) -> String {
    let mantissa = value.mantissa();
    let scale = value.scale() as usize;
    let sign = if mantissa < 0 { "-" } else { "" };
    let digits = mantissa.unsigned_abs().to_string();
    if scale == 0 {
        return format!("{sign}{digits}");
    }
    let padded = format!("{digits:0>width$}", width = scale + 1);
    format!(
        "{sign}{}.{}",
        &padded[..padded.len() - scale],
        &padded[padded.len() - scale..]
    )
}

impl ReferenceMmapSnapshotWriter {
    pub fn create(config: ReferenceMmapSnapshotConfig) -> ContractResult<Self> {
        let manifest_path = config.catalog_path.with_file_name("reference.manifest");
        // The individual view files are truncated and recreated below. Do
        // not leave an older manifest advertising a generation that no longer
        // describes the newly opened view set.
        let _ = std::fs::remove_file(&manifest_path);
        Ok(Self {
            catalog: MmapSnapshotPublisher::create(config.catalog_path, config.slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            entities: MmapSnapshotPublisher::create(config.entities_path, config.slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            assets: MmapSnapshotPublisher::create(config.assets_path, config.slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            instruments: MmapSnapshotPublisher::create(config.instruments_path, config.slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            listings: MmapSnapshotPublisher::create(config.listings_path, config.slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            markets: MmapSnapshotPublisher::create(config.markets_path, config.slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            financial_products: MmapSnapshotPublisher::create(
                config.financial_products_path,
                config.slot_size,
            )
            .map_err(|error| ContractError::Transport(error.to_string()))?,
            execution_accesses: MmapSnapshotPublisher::create(
                config.execution_accesses_path,
                config.slot_size,
            )
            .map_err(|error| ContractError::Transport(error.to_string()))?,
            encoder: FlatbuffersSnapshotEncoder::with_identity(
                config.actor_id,
                config.event_stream_id,
                config.identity,
            ),
            manifest_path,
            last_generation: None,
        })
    }

    pub fn publish(&mut self, catalog: &ReferenceCatalog) -> ContractResult<()> {
        if self.last_generation == Some(catalog.generation) {
            return Ok(());
        }
        let catalog_payload = self.encoder.encode_catalog(catalog)?;
        let entities_payload = self.encoder.encode_collection(catalog, "entities")?;
        let assets_payload = self.encoder.encode_collection(catalog, "assets")?;
        let instruments_payload = self.encoder.encode_collection(catalog, "instruments")?;
        let listings_payload = self.encoder.encode_collection(catalog, "listings")?;
        let markets_payload = self.encoder.encode_markets(catalog)?;
        let financial_products_payload = self
            .encoder
            .encode_collection(catalog, "financial_products")?;
        let execution_accesses_payload = self
            .encoder
            .encode_collection(catalog, "execution_accesses")?;
        let payload_bytes = catalog_payload.len()
            + entities_payload.len()
            + assets_payload.len()
            + instruments_payload.len()
            + listings_payload.len()
            + markets_payload.len()
            + financial_products_payload.len()
            + execution_accesses_payload.len();
        let started = std::time::Instant::now();
        let snapshot = |view_key: &str, payload: Vec<u8>| SnapshotEnvelope {
            view_key: view_key.to_string(),
            producer_id: self.encoder.actor_id.clone(),
            event_stream_id: self.encoder.event_stream_id.clone(),
            generation: catalog.generation,
            event_sequence: catalog.event_sequence,
            published_at_unix_nanos: unix_nanos(),
            payload,
        };
        self.catalog
            .publish(&snapshot("reference.catalog", catalog_payload))
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        self.entities
            .publish(&snapshot("reference.entities", entities_payload))
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        self.assets
            .publish(&snapshot("reference.assets", assets_payload))
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        self.instruments
            .publish(&snapshot("reference.instruments", instruments_payload))
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        self.listings
            .publish(&snapshot("reference.listings", listings_payload))
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        self.markets
            .publish(&snapshot("reference.markets", markets_payload))
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        self.financial_products
            .publish(&snapshot(
                "reference.financial_products",
                financial_products_payload,
            ))
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        self.execution_accesses
            .publish(&snapshot(
                "reference.execution_accesses",
                execution_accesses_payload,
            ))
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        let manifest = serde_json::json!({
            "schema_version": 1,
            "generation": catalog.generation,
            "event_sequence": catalog.event_sequence,
            "views": [
                "reference.catalog",
                "reference.entities",
                "reference.assets",
                "reference.instruments",
                "reference.listings",
                "reference.markets",
                "reference.financial_products",
                "reference.execution_accesses"
            ]
        });
        let temporary = self
            .manifest_path
            .with_extension(format!("manifest.tmp.{}", std::process::id()));
        let payload = serde_json::to_vec(&manifest)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        let file = std::fs::File::create(&temporary)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        use std::io::Write;
        let mut file = file;
        file.write_all(&payload)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        file.sync_all()
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        std::fs::rename(&temporary, &self.manifest_path)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        if let Some(parent) = self.manifest_path.parent() {
            std::fs::File::open(parent)
                .and_then(|directory| directory.sync_all())
                .map_err(|error| ContractError::Transport(error.to_string()))?;
        }
        self.last_generation = Some(catalog.generation);
        tracing::info!(
            event = "reference_snapshot_published",
            component = "reference",
            generation = catalog.generation,
            event_sequence = catalog.event_sequence,
            payload_bytes,
            publication_latency_ms = started.elapsed().as_millis() as u64,
            "reference snapshot views published"
        );
        Ok(())
    }
}

fn unix_nanos() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::{
        ReferenceMmapMarketsReader, ReferenceMmapSnapshotConfig, ReferenceMmapSnapshotSetReader,
        ReferenceMmapSnapshotWriter,
    };
    use crate::model::{Market, ReferenceCatalog};
    use kairos_protocol::InstanceIdentity;
    use std::collections::BTreeMap;

    #[test]
    fn manifest_is_committed_after_all_reference_views() {
        let directory = tempfile::tempdir().unwrap();
        let path = |name: &str| directory.path().join(name);
        let mut writer = ReferenceMmapSnapshotWriter::create(ReferenceMmapSnapshotConfig {
            catalog_path: path("catalog.snapshot"),
            entities_path: path("entities.snapshot"),
            assets_path: path("assets.snapshot"),
            instruments_path: path("instruments.snapshot"),
            listings_path: path("listings.snapshot"),
            markets_path: path("markets.snapshot"),
            financial_products_path: path("financial-products.snapshot"),
            execution_accesses_path: path("execution-accesses.snapshot"),
            slot_size: 64 * 1024,
            actor_id: "reference-test".into(),
            event_stream_id: "reference.lifecycle".into(),
            identity: InstanceIdentity::default(),
        })
        .unwrap();
        writer.publish(&ReferenceCatalog::default()).unwrap();
        let manifest: serde_json::Value = serde_json::from_slice(
            &std::fs::read(directory.path().join("reference.manifest")).unwrap(),
        )
        .unwrap();
        assert_eq!(manifest["generation"], 0);
        assert_eq!(manifest["event_sequence"], 0);
        assert_eq!(manifest["views"].as_array().unwrap().len(), 8);
        let snapshot = ReferenceMmapSnapshotSetReader::open(directory.path())
            .unwrap()
            .read()
            .unwrap();
        assert_eq!(snapshot.generation, 0);
        assert_eq!(snapshot.event_sequence, 0);
        assert!(snapshot.entities.is_empty());
    }

    #[test]
    fn markets_reader_decodes_committed_current_state() {
        let directory = tempfile::tempdir().unwrap();
        let path = |name: &str| directory.path().join(name);
        let config = ReferenceMmapSnapshotConfig {
            catalog_path: path("catalog.snapshot"),
            entities_path: path("entities.snapshot"),
            assets_path: path("assets.snapshot"),
            instruments_path: path("instruments.snapshot"),
            listings_path: path("listings.snapshot"),
            markets_path: path("markets.snapshot"),
            financial_products_path: path("financial-products.snapshot"),
            execution_accesses_path: path("execution-accesses.snapshot"),
            slot_size: 64 * 1024,
            actor_id: "reference-test".into(),
            event_stream_id: "reference.lifecycle".into(),
            identity: InstanceIdentity::default(),
        };
        let mut writer = ReferenceMmapSnapshotWriter::create(config).unwrap();
        let catalog = ReferenceCatalog {
            generation: 7,
            event_sequence: 9,
            markets: BTreeMap::from([(
                "market:btc-usdt".into(),
                Market {
                    market_id: "market:btc-usdt".into(),
                    market_key: "binance:spot:BTCUSDT".into(),
                    instrument_id: "instrument:btc-usdt".into(),
                    listing_id: "listing:btc-usdt".into(),
                    exchange_id: "exchange:binance".into(),
                    market_type: "spot".into(),
                    source_symbol: "BTCUSDT".into(),
                    status: "active".into(),
                    asset_type: Some("crypto".into()),
                    underlying_instrument_id: Some("instrument:btc".into()),
                    price_precision: 2,
                    quantity_precision: 5,
                    ..Market::default()
                },
            )]),
            ..ReferenceCatalog::default()
        };
        writer.publish(&catalog).unwrap();

        let snapshot = ReferenceMmapMarketsReader::open(
            path("markets.snapshot"),
            "reference-test",
            "reference.lifecycle",
        )
        .unwrap()
        .read()
        .unwrap();
        assert_eq!(snapshot.generation, 7);
        assert_eq!(snapshot.event_sequence, 9);
        assert_eq!(snapshot.markets[0].market_id, "market:btc-usdt");
        assert_eq!(snapshot.markets[0].price_precision, 2);
        assert_eq!(snapshot.markets[0].asset_type.as_deref(), Some("crypto"));
        assert_eq!(
            snapshot.markets[0].underlying_instrument_id.as_deref(),
            Some("instrument:btc")
        );
    }

    #[test]
    fn snapshot_set_reader_rejects_manifest_watermark_mismatch() {
        let directory = tempfile::tempdir().unwrap();
        let path = |name: &str| directory.path().join(name);
        let mut writer = ReferenceMmapSnapshotWriter::create(ReferenceMmapSnapshotConfig {
            catalog_path: path("catalog.snapshot"),
            entities_path: path("entities.snapshot"),
            assets_path: path("assets.snapshot"),
            instruments_path: path("instruments.snapshot"),
            listings_path: path("listings.snapshot"),
            markets_path: path("markets.snapshot"),
            financial_products_path: path("financial-products.snapshot"),
            execution_accesses_path: path("execution-accesses.snapshot"),
            slot_size: 64 * 1024,
            actor_id: "reference-test".into(),
            event_stream_id: "reference.lifecycle".into(),
            identity: InstanceIdentity::default(),
        })
        .unwrap();
        writer.publish(&ReferenceCatalog::default()).unwrap();
        std::fs::write(
            directory.path().join("reference.manifest"),
            br#"{"schema_version":1,"generation":9,"event_sequence":0,"views":["reference.catalog","reference.entities","reference.assets","reference.instruments","reference.listings","reference.markets","reference.financial_products","reference.execution_accesses"]}"#,
        )
        .unwrap();
        let error = ReferenceMmapSnapshotSetReader::open(directory.path())
            .unwrap()
            .read()
            .unwrap_err()
            .to_string();
        assert!(error.contains("watermark"));
    }
}
