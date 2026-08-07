//! Composition shared by the one-shot CLI and the long-running server.

use std::path::Path;

use crate::application::protocol::ReferenceSource;
use crate::application::protocol::{PublishedSnapshots, SnapshotPublisher};
use crate::services::providers::{
    BinanceEquitySource, BinanceOptionsSource, BinanceSpotSource, HyperliquidSource,
    MassiveEquitySource, MassiveSource,
};
use crate::services::publication::AeronSnapshotPublisher;
use crate::services::storage::SqliteCatalogStore;
use crate::ReferenceApplication;
use crate::{domain::ReferenceCatalog, domain::ReferenceResult};

#[derive(Clone, Debug)]
pub struct ReferenceRuntimeConfig {
    pub provider: String,
    pub endpoint: String,
    pub database: std::path::PathBuf,
    pub api_key: String,
    pub binance_api_key: String,
    pub secret: String,
    pub underlying: String,
    pub aeron_dir: Option<String>,
    pub channel: String,
    pub catalog_stream: i32,
    pub markets_stream: i32,
    pub lifecycle_stream: i32,
    pub changes_stream: i32,
}

/// Build the same business application for both process modes.
///
/// Read-only CLI commands use `publish = false`, while refresh/publish and the
/// server use the real Aeron publisher. The provider and store are always the
/// production implementations; the disabled publisher is only for local
/// catalog inspection where no media driver is required.
pub fn build_application(
    config: &ReferenceRuntimeConfig,
    publish: bool,
) -> ReferenceResult<ReferenceApplication> {
    let source: Box<dyn ReferenceSource> = match config.provider.as_str() {
        "binance-spot" => Box::new(BinanceSpotSource::new(config.endpoint.clone())?),
        "binance-options" => Box::new(BinanceOptionsSource::new(config.endpoint.clone())?),
        "binance-equity" => Box::new(BinanceEquitySource::new(
            config.binance_api_key.clone(),
            config.secret.clone(),
        )?),
        "massive-options" => Box::new(MassiveSource::new(
            config.api_key.clone(),
            config.endpoint.clone(),
            config.underlying.clone(),
        )?),
        "massive" | "massive-equity" => Box::new(MassiveEquitySource::new(
            config.api_key.clone(),
            config.endpoint.clone(),
        )?),
        "hyperliquid" => Box::new(HyperliquidSource::new(config.endpoint.clone())?),
        value => {
            return Err(crate::domain::ReferenceError::Provider(format!(
                "unsupported reference provider: {value}"
            )))
        }
    };
    let store = SqliteCatalogStore::open(&config.database)?;
    let publisher: Box<dyn SnapshotPublisher> = if publish {
        Box::new(AeronSnapshotPublisher::connect(
            config.aeron_dir.as_deref(),
            &config.channel,
            config.catalog_stream,
            config.markets_stream,
            config.lifecycle_stream,
            config.changes_stream,
            "reference-actor",
            "reference.lifecycle",
        )?)
    } else {
        Box::new(DisabledPublisher)
    };
    ReferenceApplication::new("reference-actor", source, Box::new(store), publisher)
}

#[derive(Debug)]
struct DisabledPublisher;

impl SnapshotPublisher for DisabledPublisher {
    fn publish(&mut self, _catalog: &ReferenceCatalog) -> ReferenceResult<PublishedSnapshots> {
        Ok(PublishedSnapshots::default())
    }
}

pub fn ensure_database_parent(path: &Path) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    Ok(())
}
