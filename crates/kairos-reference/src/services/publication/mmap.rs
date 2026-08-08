//! File-backed mmap publication for Reference read models.

use std::path::Path;

use kairos_protocol::InstanceIdentity;
use kairos_transport::SharedSnapshotWriter;

use crate::domain::{ReferenceCatalog, ReferenceError, ReferenceResult};
use crate::services::publication::encoder::FlatbuffersSnapshotEncoder;

pub struct MmapReferenceSnapshotWriter {
    catalog: SharedSnapshotWriter,
    markets: SharedSnapshotWriter,
    lifecycle: SharedSnapshotWriter,
    encoder: FlatbuffersSnapshotEncoder,
}

impl MmapReferenceSnapshotWriter {
    pub fn create(
        catalog_path: impl AsRef<Path>,
        markets_path: impl AsRef<Path>,
        lifecycle_path: impl AsRef<Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        event_stream_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> ReferenceResult<Self> {
        Ok(Self {
            catalog: SharedSnapshotWriter::create(catalog_path, slot_size)
                .map_err(|error| ReferenceError::Publication(error.to_string()))?,
            markets: SharedSnapshotWriter::create(markets_path, slot_size)
                .map_err(|error| ReferenceError::Publication(error.to_string()))?,
            lifecycle: SharedSnapshotWriter::create(lifecycle_path, slot_size)
                .map_err(|error| ReferenceError::Publication(error.to_string()))?,
            encoder: FlatbuffersSnapshotEncoder::with_identity(actor_id, event_stream_id, identity),
        })
    }

    pub fn publish(&mut self, catalog: &ReferenceCatalog) -> ReferenceResult<()> {
        let catalog_payload = self.encoder.encode_catalog(catalog)?;
        let markets_payload = self.encoder.encode_markets(catalog)?;
        let lifecycle_payload = self.encoder.encode_lifecycle(catalog)?;
        self.catalog
            .publish(catalog.generation, &catalog_payload)
            .map_err(|error| ReferenceError::Publication(error.to_string()))?;
        self.markets
            .publish(catalog.generation, &markets_payload)
            .map_err(|error| ReferenceError::Publication(error.to_string()))?;
        self.lifecycle
            .publish(catalog.generation, &lifecycle_payload)
            .map_err(|error| ReferenceError::Publication(error.to_string()))?;
        Ok(())
    }
}
