//! Aeron publication service for Reference snapshots.

use kairos_transport::AeronBytePublisher;

use crate::application::protocol::PublishedSnapshots;
use crate::domain::{ReferenceCatalog, ReferenceError, ReferenceResult};
use crate::services::publication::encoder::FlatbuffersSnapshotEncoder;

/// Publishes the three Reference read models through Aeron.
pub struct AeronSnapshotWriter {
    catalog: AeronBytePublisher,
    markets: AeronBytePublisher,
    lifecycle: AeronBytePublisher,
    changes: AeronBytePublisher,
    encoder: FlatbuffersSnapshotEncoder,
}

impl AeronSnapshotWriter {
    pub fn connect(
        aeron_dir: Option<&str>,
        channel: &str,
        catalog_stream_id: i32,
        markets_stream_id: i32,
        lifecycle_stream_id: i32,
        changes_stream_id: i32,
        actor_id: impl Into<String>,
        event_stream_id: impl Into<String>,
    ) -> ReferenceResult<Self> {
        let catalog = AeronBytePublisher::connect(aeron_dir, channel, catalog_stream_id)
            .map_err(ReferenceError::Publication)?;
        let markets = AeronBytePublisher::connect(aeron_dir, channel, markets_stream_id)
            .map_err(ReferenceError::Publication)?;
        let lifecycle = AeronBytePublisher::connect(aeron_dir, channel, lifecycle_stream_id)
            .map_err(ReferenceError::Publication)?;
        let changes = AeronBytePublisher::connect(aeron_dir, channel, changes_stream_id)
            .map_err(ReferenceError::Publication)?;
        Ok(Self {
            catalog,
            markets,
            lifecycle,
            changes,
            encoder: FlatbuffersSnapshotEncoder::new(actor_id, event_stream_id),
        })
    }
}

impl AeronSnapshotWriter {
    pub fn publish(&mut self, catalog: &ReferenceCatalog) -> ReferenceResult<PublishedSnapshots> {
        let snapshots = PublishedSnapshots {
            catalog: self.encoder.encode_catalog(catalog)?,
            markets: self.encoder.encode_markets(catalog)?,
            lifecycle: self.encoder.encode_lifecycle(catalog)?,
        };
        self.catalog
            .publish(&snapshots.catalog)
            .map_err(ReferenceError::Publication)?;
        self.markets
            .publish(&snapshots.markets)
            .map_err(ReferenceError::Publication)?;
        self.lifecycle
            .publish(&snapshots.lifecycle)
            .map_err(ReferenceError::Publication)?;
        Ok(snapshots)
    }

    pub fn publish_change(
        &mut self,
        catalog: &ReferenceCatalog,
        events: &[crate::domain::LifecycleEvent],
    ) -> ReferenceResult<()> {
        if events.is_empty() {
            return Ok(());
        }
        let payload = self.encoder.encode_change(catalog, events)?;
        self.changes
            .publish(&payload)
            .map_err(ReferenceError::Publication)
    }
}
