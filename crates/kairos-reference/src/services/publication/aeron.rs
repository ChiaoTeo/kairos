//! Aeron publication service for Reference snapshots.

use std::ffi::CString;
use std::sync::{Arc, Mutex};

use aeron::aeron::Aeron;
use aeron::concurrent::atomic_buffer::{AlignedBuffer, AtomicBuffer};
use aeron::context::Context;
use aeron::publication::Publication;

use crate::application::protocol::{PublishedSnapshots, SnapshotPublisher};
use crate::domain::{ReferenceCatalog, ReferenceError, ReferenceResult};
use crate::services::publication::encoder::FlatbuffersSnapshotEncoder;

/// Publishes the three Reference read models through Aeron.
pub struct AeronSnapshotPublisher {
    _aeron: Aeron,
    catalog: Arc<Mutex<Publication>>,
    markets: Arc<Mutex<Publication>>,
    lifecycle: Arc<Mutex<Publication>>,
    changes: Arc<Mutex<Publication>>,
    catalog_buffer: AlignedBuffer,
    markets_buffer: AlignedBuffer,
    lifecycle_buffer: AlignedBuffer,
    changes_buffer: AlignedBuffer,
    encoder: FlatbuffersSnapshotEncoder,
}

impl AeronSnapshotPublisher {
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
        let mut context = Context::new();
        if let Some(directory) = aeron_dir {
            context.set_aeron_dir(directory.to_string());
        }
        let mut aeron = Aeron::new(context)
            .map_err(|error| ReferenceError::Publication(format!("connect Aeron: {error:?}")))?;
        let channel = CString::new(channel)
            .map_err(|error| ReferenceError::Publication(error.to_string()))?;
        let catalog_id = aeron
            .add_publication(channel.clone(), catalog_stream_id)
            .map_err(|error| {
                ReferenceError::Publication(format!("add catalog publication: {error:?}"))
            })?;
        let markets_id = aeron
            .add_publication(channel.clone(), markets_stream_id)
            .map_err(|error| {
                ReferenceError::Publication(format!("add markets publication: {error:?}"))
            })?;
        let lifecycle_id = aeron
            .add_publication(channel.clone(), lifecycle_stream_id)
            .map_err(|error| {
                ReferenceError::Publication(format!("add lifecycle publication: {error:?}"))
            })?;
        let changes_id = aeron
            .add_publication(channel.clone(), changes_stream_id)
            .map_err(|error| {
                ReferenceError::Publication(format!("add changes publication: {error:?}"))
            })?;
        let catalog = wait_for_publication(&mut aeron, catalog_id)?;
        let markets = wait_for_publication(&mut aeron, markets_id)?;
        let lifecycle = wait_for_publication(&mut aeron, lifecycle_id)?;
        let changes = wait_for_publication(&mut aeron, changes_id)?;
        Ok(Self {
            _aeron: aeron,
            catalog,
            markets,
            lifecycle,
            changes,
            catalog_buffer: AlignedBuffer::with_capacity(4 * 1024 * 1024),
            markets_buffer: AlignedBuffer::with_capacity(4 * 1024 * 1024),
            lifecycle_buffer: AlignedBuffer::with_capacity(4 * 1024 * 1024),
            changes_buffer: AlignedBuffer::with_capacity(4 * 1024 * 1024),
            encoder: FlatbuffersSnapshotEncoder::new(actor_id, event_stream_id),
        })
    }
}

impl SnapshotPublisher for AeronSnapshotPublisher {
    fn publish(&mut self, catalog: &ReferenceCatalog) -> ReferenceResult<PublishedSnapshots> {
        let snapshots = PublishedSnapshots {
            catalog: self.encoder.encode_catalog(catalog)?,
            markets: self.encoder.encode_markets(catalog)?,
            lifecycle: self.encoder.encode_lifecycle(catalog)?,
        };
        offer_bytes(&self.catalog, &self.catalog_buffer, &snapshots.catalog)?;
        offer_bytes(&self.markets, &self.markets_buffer, &snapshots.markets)?;
        offer_bytes(
            &self.lifecycle,
            &self.lifecycle_buffer,
            &snapshots.lifecycle,
        )?;
        Ok(snapshots)
    }

    fn publish_change(
        &mut self,
        catalog: &ReferenceCatalog,
        events: &[crate::domain::LifecycleEvent],
    ) -> ReferenceResult<()> {
        if events.is_empty() {
            return Ok(());
        }
        let payload = self.encoder.encode_change(catalog, events)?;
        offer_bytes(&self.changes, &self.changes_buffer, &payload)
    }
}

fn wait_for_publication(
    aeron: &mut Aeron,
    registration_id: i64,
) -> ReferenceResult<Arc<Mutex<Publication>>> {
    for _ in 0..10_000 {
        if let Ok(publication) = aeron.find_publication(registration_id) {
            return Ok(publication);
        }
        std::thread::yield_now();
    }
    Err(ReferenceError::Publication(format!(
        "Aeron publication {registration_id} was not acknowledged"
    )))
}

fn offer_bytes(
    publication: &Arc<Mutex<Publication>>,
    buffer: &AlignedBuffer,
    bytes: &[u8],
) -> ReferenceResult<()> {
    const BUFFER_CAPACITY: usize = 4 * 1024 * 1024;
    if bytes.len() > BUFFER_CAPACITY {
        return Err(ReferenceError::Publication(format!(
            "snapshot size {} exceeds Aeron buffer {BUFFER_CAPACITY}",
            bytes.len()
        )));
    }
    let atomic = AtomicBuffer::from_aligned(buffer);
    atomic.put_bytes(0, bytes);
    for _ in 0..10_000 {
        match publication
            .lock()
            .expect("Aeron publication mutex poisoned")
            .offer_part(atomic, 0, bytes.len() as i32)
        {
            Ok(_) => return Ok(()),
            Err(_) => std::thread::yield_now(),
        }
    }
    Err(ReferenceError::Publication(
        "Aeron publication remained back-pressured".into(),
    ))
}
