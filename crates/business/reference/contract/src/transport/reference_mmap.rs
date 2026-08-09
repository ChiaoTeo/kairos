//! File-backed mmap publication for Reference read models.

use std::path::{Path, PathBuf};

use crate::encoding::FlatbuffersSnapshotEncoder;
use crate::model::ReferenceCatalog;
use crate::snapshot::{SnapshotEnvelope, SnapshotPublisher};
use crate::transport::MmapSnapshotPublisher;
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

impl ReferenceMmapSnapshotWriter {
    pub fn create(
        catalog_path: impl AsRef<Path>,
        entities_path: impl AsRef<Path>,
        assets_path: impl AsRef<Path>,
        instruments_path: impl AsRef<Path>,
        listings_path: impl AsRef<Path>,
        markets_path: impl AsRef<Path>,
        financial_products_path: impl AsRef<Path>,
        execution_accesses_path: impl AsRef<Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        event_stream_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> ContractResult<Self> {
        let manifest_path = catalog_path.as_ref().with_file_name("reference.manifest");
        // The individual view files are truncated and recreated below. Do
        // not leave an older manifest advertising a generation that no longer
        // describes the newly opened view set.
        let _ = std::fs::remove_file(&manifest_path);
        Ok(Self {
            catalog: MmapSnapshotPublisher::create(catalog_path, slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            entities: MmapSnapshotPublisher::create(entities_path, slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            assets: MmapSnapshotPublisher::create(assets_path, slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            instruments: MmapSnapshotPublisher::create(instruments_path, slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            listings: MmapSnapshotPublisher::create(listings_path, slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            markets: MmapSnapshotPublisher::create(markets_path, slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            financial_products: MmapSnapshotPublisher::create(financial_products_path, slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            execution_accesses: MmapSnapshotPublisher::create(execution_accesses_path, slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            encoder: FlatbuffersSnapshotEncoder::with_identity(actor_id, event_stream_id, identity),
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
    use super::ReferenceMmapSnapshotWriter;
    use crate::model::ReferenceCatalog;
    use kairos_protocol::InstanceIdentity;

    #[test]
    fn manifest_is_committed_after_all_reference_views() {
        let directory = tempfile::tempdir().unwrap();
        let path = |name: &str| directory.path().join(name);
        let mut writer = ReferenceMmapSnapshotWriter::create(
            path("catalog.snapshot"),
            path("entities.snapshot"),
            path("assets.snapshot"),
            path("instruments.snapshot"),
            path("listings.snapshot"),
            path("markets.snapshot"),
            path("financial-products.snapshot"),
            path("execution-accesses.snapshot"),
            64 * 1024,
            "reference-test",
            "reference.lifecycle",
            InstanceIdentity::default(),
        )
        .unwrap();
        writer.publish(&ReferenceCatalog::default()).unwrap();
        let manifest: serde_json::Value = serde_json::from_slice(
            &std::fs::read(directory.path().join("reference.manifest")).unwrap(),
        )
        .unwrap();
        assert_eq!(manifest["generation"], 0);
        assert_eq!(manifest["event_sequence"], 0);
        assert_eq!(manifest["views"].as_array().unwrap().len(), 8);
    }
}
