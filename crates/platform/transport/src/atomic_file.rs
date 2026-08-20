//! Atomic full-replacement snapshot files.
//!
//! This is a public View transport, not module persistence. Each publication
//! writes one versioned envelope to a sibling temporary file, flushes it, and
//! atomically renames it over the visible path.

use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use crate::{SnapshotEnvelopeMetadata, SnapshotError, SnapshotPublishReceipt};

const MAGIC: &[u8; 4] = b"KFV1";
const VERSION: u16 = 1;
const HEADER_LEN: usize = 56;

pub struct AtomicFileSnapshotStorage {
    path: PathBuf,
    max_payload_len: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AtomicFileSnapshot {
    pub metadata: SnapshotEnvelopeMetadata,
    pub payload: Vec<u8>,
}

impl AtomicFileSnapshotStorage {
    pub fn create(path: impl AsRef<Path>, max_payload_len: usize) -> Result<Self, SnapshotError> {
        if max_payload_len == 0 {
            return Err(SnapshotError::Configuration(
                "atomic file snapshot capacity must be positive",
            ));
        }
        let path = path.as_ref().to_path_buf();
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        Ok(Self {
            path,
            max_payload_len,
        })
    }

    pub fn publish(
        &mut self,
        metadata: SnapshotEnvelopeMetadata,
        payload: &[u8],
    ) -> Result<SnapshotPublishReceipt, SnapshotError> {
        if payload.len() > self.max_payload_len {
            return Err(SnapshotError::PayloadTooLarge {
                limit: self.max_payload_len,
                actual: payload.len(),
            });
        }
        let payload_len =
            u32::try_from(payload.len()).map_err(|_| SnapshotError::PayloadTooLarge {
                limit: u32::MAX as usize,
                actual: payload.len(),
            })?;
        let temporary = temporary_path(&self.path);
        let mut file = File::create(&temporary)?;
        file.write_all(MAGIC)?;
        file.write_all(&VERSION.to_be_bytes())?;
        file.write_all(&(HEADER_LEN as u16).to_be_bytes())?;
        for value in [
            metadata.resource_epoch,
            metadata.producer_incarnation,
            metadata.generation,
            metadata.applied_event_sequence,
            metadata.published_at_unix_nanos,
        ] {
            file.write_all(&value.to_be_bytes())?;
        }
        file.write_all(&payload_len.to_be_bytes())?;
        file.write_all(&crc32fast::hash(payload).to_be_bytes())?;
        file.write_all(payload)?;
        file.sync_all()?;
        fs::rename(&temporary, &self.path)?;
        if let Some(parent) = self.path.parent() {
            File::open(parent)?.sync_all()?;
        }
        Ok(SnapshotPublishReceipt {
            resource_epoch: metadata.resource_epoch,
            producer_incarnation: metadata.producer_incarnation,
            generation: metadata.generation,
            applied_event_sequence: metadata.applied_event_sequence,
        })
    }
}

pub fn read_atomic_file_snapshot(
    path: impl AsRef<Path>,
) -> Result<AtomicFileSnapshot, SnapshotError> {
    let mut bytes = Vec::new();
    File::open(path)?.read_to_end(&mut bytes)?;
    if bytes.len() < HEADER_LEN || bytes.get(..4) != Some(MAGIC) {
        return Err(SnapshotError::Corrupt(
            "invalid atomic file snapshot envelope",
        ));
    }
    let version = u16::from_be_bytes(bytes[4..6].try_into().expect("fixed field"));
    if version != VERSION {
        return Err(SnapshotError::UnsupportedVersion(version));
    }
    let header_len = u16::from_be_bytes(bytes[6..8].try_into().expect("fixed field")) as usize;
    if header_len != HEADER_LEN {
        return Err(SnapshotError::Corrupt(
            "invalid atomic file snapshot header length",
        ));
    }
    let metadata = SnapshotEnvelopeMetadata {
        resource_epoch: read_u64(&bytes, 8),
        producer_incarnation: read_u64(&bytes, 16),
        generation: read_u64(&bytes, 24),
        applied_event_sequence: read_u64(&bytes, 32),
        published_at_unix_nanos: read_u64(&bytes, 40),
    };
    let payload_len = u32::from_be_bytes(bytes[48..52].try_into().expect("fixed field")) as usize;
    let expected = u32::from_be_bytes(bytes[52..56].try_into().expect("fixed field"));
    let payload = bytes
        .get(HEADER_LEN..HEADER_LEN.saturating_add(payload_len))
        .ok_or(SnapshotError::Corrupt(
            "truncated atomic file snapshot payload",
        ))?;
    if bytes.len() != HEADER_LEN + payload_len {
        return Err(SnapshotError::Corrupt(
            "atomic file snapshot has trailing bytes",
        ));
    }
    let actual = crc32fast::hash(payload);
    if actual != expected {
        return Err(SnapshotError::ChecksumMismatch { expected, actual });
    }
    Ok(AtomicFileSnapshot {
        metadata,
        payload: payload.to_vec(),
    })
}

fn read_u64(bytes: &[u8], offset: usize) -> u64 {
    u64::from_be_bytes(
        bytes[offset..offset + 8]
            .try_into()
            .expect("validated header"),
    )
}

fn temporary_path(path: &Path) -> PathBuf {
    let suffix = format!("tmp-{}", std::process::id());
    path.with_extension(suffix)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn publication_is_a_complete_versioned_atomic_file_view() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("risk.latest.view");
        let metadata = SnapshotEnvelopeMetadata {
            resource_epoch: 3,
            producer_incarnation: 4,
            generation: 5,
            applied_event_sequence: 6,
            published_at_unix_nanos: 7,
        };
        let mut storage = AtomicFileSnapshotStorage::create(&path, 128).unwrap();
        storage.publish(metadata, b"first").unwrap();
        storage.publish(metadata, b"replacement").unwrap();

        let snapshot = read_atomic_file_snapshot(&path).unwrap();
        assert_eq!(snapshot.metadata, metadata);
        assert_eq!(snapshot.payload, b"replacement");
        assert!(!temporary_path(&path).exists());
    }
}
