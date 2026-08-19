//! Versioned, process-safe double-slot mmap snapshot envelope.

use std::fs::{File, OpenOptions};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};
use std::{fmt, io};

use fs2::FileExt;
use memmap2::{Mmap, MmapMut, MmapOptions};

const MAGIC: &[u8; 4] = b"KSS1";
const VERSION_V1: u16 = 1;
pub const SNAPSHOT_ENVELOPE_VERSION: u16 = 2;
const SLOT_COUNT: usize = 2;
const V1_HEADER_SIZE: usize = 64;
const V2_HEADER_SIZE: usize = 256;
const SLOT_METADATA_SIZE: usize = 64;
const ACTIVE_COMMIT_OFFSET: usize = 40;
const SLOT_METADATA_OFFSET: usize = 64;
const TRANSACTION_OFFSET: usize = 0;
const LENGTH_OFFSET: usize = 8;
const CHECKSUM_OFFSET: usize = 12;
const RESOURCE_EPOCH_OFFSET: usize = 16;
const INCARNATION_OFFSET: usize = 24;
const GENERATION_OFFSET: usize = 32;
const WATERMARK_OFFSET: usize = 40;
const PUBLISHED_AT_OFFSET: usize = 48;
const READ_RETRIES: usize = 16;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SnapshotEnvelopeMetadata {
    pub resource_epoch: u64,
    pub producer_incarnation: u64,
    pub generation: u64,
    pub applied_event_sequence: u64,
    pub published_at_unix_nanos: u64,
}

impl SnapshotEnvelopeMetadata {
    pub fn validate(self) -> Result<Self, SnapshotError> {
        if self.resource_epoch == 0 {
            return Err(SnapshotError::Configuration(
                "resource_epoch must be positive",
            ));
        }
        if self.producer_incarnation == 0 {
            return Err(SnapshotError::Configuration(
                "producer_incarnation must be positive",
            ));
        }
        if self.generation == 0 {
            return Err(SnapshotError::Configuration("generation must be positive"));
        }
        if self.published_at_unix_nanos == 0 {
            return Err(SnapshotError::Configuration(
                "published_at_unix_nanos must be positive",
            ));
        }
        Ok(self)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SharedSnapshotPayload {
    pub envelope_version: u16,
    pub resource_epoch: u64,
    pub producer_incarnation: u64,
    pub generation: u64,
    pub applied_event_sequence: u64,
    pub published_at_unix_nanos: u64,
    pub payload: Vec<u8>,
}

#[derive(Debug)]
pub enum SnapshotError {
    Io(io::Error),
    Configuration(&'static str),
    WriterLeaseHeld(PathBuf),
    NotInitialized,
    UnsupportedVersion(u16),
    Corrupt(&'static str),
    ChecksumMismatch { expected: u32, actual: u32 },
    PayloadTooLarge { limit: usize, actual: usize },
    ConcurrentChange,
    ResourceChanged,
    CommitOverflow,
}

impl SnapshotError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::Io(_) => "io",
            Self::Configuration(_) => "configuration",
            Self::WriterLeaseHeld(_) => "writer_lease_held",
            Self::NotInitialized => "snapshot_not_initialized",
            Self::UnsupportedVersion(_) => "unsupported_envelope_version",
            Self::Corrupt(_) | Self::ChecksumMismatch { .. } => "corrupt_snapshot",
            Self::PayloadTooLarge { .. } => "payload_too_large",
            Self::ConcurrentChange => "concurrent_change",
            Self::ResourceChanged => "resource_changed",
            Self::CommitOverflow => "commit_overflow",
        }
    }
}

impl fmt::Display for SnapshotError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(error) => write!(formatter, "snapshot I/O failed: {error}"),
            Self::Configuration(message) | Self::Corrupt(message) => formatter.write_str(message),
            Self::WriterLeaseHeld(path) => {
                write!(
                    formatter,
                    "snapshot writer lease is already held: {}",
                    path.display()
                )
            },
            Self::NotInitialized => formatter.write_str("snapshot is not initialized"),
            Self::UnsupportedVersion(version) => {
                write!(formatter, "unsupported snapshot envelope version {version}")
            },
            Self::ChecksumMismatch { expected, actual } => write!(
                formatter,
                "snapshot checksum mismatch: expected {expected:#010x}, got {actual:#010x}"
            ),
            Self::PayloadTooLarge { limit, actual } => {
                write!(
                    formatter,
                    "snapshot payload size {actual} exceeds capacity {limit}"
                )
            },
            Self::ConcurrentChange => formatter.write_str("snapshot changed during read"),
            Self::ResourceChanged => formatter.write_str("snapshot resource changed"),
            Self::CommitOverflow => formatter.write_str("snapshot commit counter overflowed"),
        }
    }
}

impl std::error::Error for SnapshotError {}

impl From<io::Error> for SnapshotError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

pub struct SharedSnapshotWriter {
    _lease: File,
    file: File,
    mmap: MmapMut,
    slot_capacity: usize,
}

pub struct SharedSnapshotReader {
    path: PathBuf,
    state: Mutex<ReaderState>,
}

struct ReaderState {
    mmap: Mmap,
    identity: FileIdentity,
    layout: Layout,
}

#[derive(Debug, Clone, Copy)]
enum Layout {
    V1 { slot_capacity: usize },
    V2 { slot_capacity: usize },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct FileIdentity {
    #[cfg(unix)]
    device: u64,
    #[cfg(unix)]
    inode: u64,
    len: u64,
}

impl SharedSnapshotWriter {
    pub fn create(path: impl AsRef<Path>, slot_capacity: usize) -> Result<Self, SnapshotError> {
        let path = path.as_ref();
        if slot_capacity == 0 || slot_capacity > u32::MAX as usize {
            return Err(SnapshotError::Configuration(
                "snapshot slot capacity must be in the u32 range",
            ));
        }
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let lease_path = writer_lease_path(path);
        let lease = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .open(&lease_path)?;
        lease
            .try_lock_exclusive()
            .map_err(|_| SnapshotError::WriterLeaseHeld(lease_path))?;

        if !existing_v2_matches(path, slot_capacity)? {
            replace_with_empty_v2(path, slot_capacity)?;
        }
        let file = OpenOptions::new().read(true).write(true).open(path)?;
        let mmap = unsafe { MmapOptions::new().map_mut(&file)? };
        validate_v2(&mmap)?;
        Ok(Self {
            _lease: lease,
            file,
            mmap,
            slot_capacity,
        })
    }

    pub fn publish_with_metadata(
        &mut self,
        metadata: SnapshotEnvelopeMetadata,
        payload: &[u8],
    ) -> Result<(), SnapshotError> {
        let metadata = metadata.validate()?;
        if payload.is_empty() {
            return Err(SnapshotError::Configuration(
                "snapshot payload must not be empty",
            ));
        }
        if payload.len() > self.slot_capacity {
            return Err(SnapshotError::PayloadTooLarge {
                limit: self.slot_capacity,
                actual: payload.len(),
            });
        }
        let active_commit = atomic_u64(&self.mmap, ACTIVE_COMMIT_OFFSET).load(Ordering::Acquire);
        let next_commit = (active_commit >> 1)
            .checked_add(1)
            .ok_or(SnapshotError::CommitOverflow)?;
        if next_commit > (u64::MAX >> 1) {
            return Err(SnapshotError::CommitOverflow);
        }
        let active = (active_commit & 1) as usize;
        let inactive = 1 - active;
        let slot_offset = slot_metadata_offset(inactive);
        let current_transaction =
            atomic_u64(&self.mmap, slot_offset + TRANSACTION_OFFSET).load(Ordering::Acquire);
        let writing = if current_transaction & 1 == 0 {
            current_transaction
                .checked_add(1)
                .ok_or(SnapshotError::CommitOverflow)?
        } else {
            current_transaction
        };
        atomic_u64(&self.mmap, slot_offset + TRANSACTION_OFFSET).store(writing, Ordering::Release);

        let payload_offset = payload_offset(inactive, self.slot_capacity);
        self.mmap[payload_offset..payload_offset + payload.len()].copy_from_slice(payload);
        write_u32(
            &mut self.mmap,
            slot_offset + LENGTH_OFFSET,
            payload.len() as u32,
        )?;
        write_u32(
            &mut self.mmap,
            slot_offset + CHECKSUM_OFFSET,
            crc32fast::hash(payload),
        )?;
        write_u64(
            &mut self.mmap,
            slot_offset + RESOURCE_EPOCH_OFFSET,
            metadata.resource_epoch,
        )?;
        write_u64(
            &mut self.mmap,
            slot_offset + INCARNATION_OFFSET,
            metadata.producer_incarnation,
        )?;
        write_u64(
            &mut self.mmap,
            slot_offset + GENERATION_OFFSET,
            metadata.generation,
        )?;
        write_u64(
            &mut self.mmap,
            slot_offset + WATERMARK_OFFSET,
            metadata.applied_event_sequence,
        )?;
        write_u64(
            &mut self.mmap,
            slot_offset + PUBLISHED_AT_OFFSET,
            metadata.published_at_unix_nanos,
        )?;
        atomic_u64(&self.mmap, slot_offset + TRANSACTION_OFFSET)
            .store(writing + 1, Ordering::Release);
        atomic_u64(&self.mmap, ACTIVE_COMMIT_OFFSET)
            .store((next_commit << 1) | inactive as u64, Ordering::Release);
        let _ = &self.file;
        Ok(())
    }
}

impl SharedSnapshotReader {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, SnapshotError> {
        let path = path.as_ref().to_owned();
        let state = open_reader_state(&path)?;
        Ok(Self {
            path,
            state: Mutex::new(state),
        })
    }

    pub fn read_payload(&self) -> Result<SharedSnapshotPayload, SnapshotError> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| SnapshotError::Corrupt("snapshot reader lock poisoned"))?;
        let current_identity = file_identity(&std::fs::metadata(&self.path)?)?;
        if current_identity != state.identity {
            *state = open_reader_state(&self.path).map_err(|error| match error {
                SnapshotError::Io(_) => SnapshotError::ResourceChanged,
                other => other,
            })?;
        }
        match state.layout {
            Layout::V1 { slot_capacity } => read_v1(&state.mmap, slot_capacity),
            Layout::V2 { slot_capacity } => read_v2(&state.mmap, slot_capacity),
        }
    }
}

fn read_v2(mmap: &[u8], slot_capacity: usize) -> Result<SharedSnapshotPayload, SnapshotError> {
    for _ in 0..READ_RETRIES {
        let commit = atomic_u64(mmap, ACTIVE_COMMIT_OFFSET).load(Ordering::Acquire);
        if commit == 0 {
            return Err(SnapshotError::NotInitialized);
        }
        let active = (commit & 1) as usize;
        let slot_offset = slot_metadata_offset(active);
        let transaction =
            atomic_u64(mmap, slot_offset + TRANSACTION_OFFSET).load(Ordering::Acquire);
        if transaction & 1 == 1 {
            std::hint::spin_loop();
            continue;
        }
        let length = read_u32(mmap, slot_offset + LENGTH_OFFSET)? as usize;
        if length == 0 || length > slot_capacity {
            return Err(SnapshotError::Corrupt("snapshot payload length is invalid"));
        }
        let expected_checksum = read_u32(mmap, slot_offset + CHECKSUM_OFFSET)?;
        let resource_epoch = read_u64(mmap, slot_offset + RESOURCE_EPOCH_OFFSET)?;
        let producer_incarnation = read_u64(mmap, slot_offset + INCARNATION_OFFSET)?;
        let generation = read_u64(mmap, slot_offset + GENERATION_OFFSET)?;
        let applied_event_sequence = read_u64(mmap, slot_offset + WATERMARK_OFFSET)?;
        let published_at_unix_nanos = read_u64(mmap, slot_offset + PUBLISHED_AT_OFFSET)?;
        let start = payload_offset(active, slot_capacity);
        let payload = mmap[start..start + length].to_vec();
        let actual_checksum = crc32fast::hash(&payload);
        let transaction_after =
            atomic_u64(mmap, slot_offset + TRANSACTION_OFFSET).load(Ordering::Acquire);
        let commit_after = atomic_u64(mmap, ACTIVE_COMMIT_OFFSET).load(Ordering::Acquire);
        if transaction != transaction_after || commit != commit_after {
            std::hint::spin_loop();
            continue;
        }
        if actual_checksum != expected_checksum {
            return Err(SnapshotError::ChecksumMismatch {
                expected: expected_checksum,
                actual: actual_checksum,
            });
        }
        return Ok(SharedSnapshotPayload {
            envelope_version: SNAPSHOT_ENVELOPE_VERSION,
            resource_epoch,
            producer_incarnation,
            generation,
            applied_event_sequence,
            published_at_unix_nanos,
            payload,
        });
    }
    Err(SnapshotError::ConcurrentChange)
}

fn read_v1(mmap: &[u8], slot_capacity: usize) -> Result<SharedSnapshotPayload, SnapshotError> {
    for _ in 0..8 {
        let active = *mmap
            .get(12)
            .ok_or(SnapshotError::Corrupt("v1 active slot is truncated"))?
            as usize;
        if active >= SLOT_COUNT {
            return Err(SnapshotError::Corrupt("v1 active slot is invalid"));
        }
        let length = read_u32(mmap, 24 + active * 4)? as usize;
        let generation = read_u64(mmap, 32 + active * 8)?;
        if length == 0 || length > slot_capacity {
            return Err(SnapshotError::NotInitialized);
        }
        let start = V1_HEADER_SIZE + active * slot_capacity;
        let payload = mmap[start..start + length].to_vec();
        if mmap[12] as usize == active && read_u64(mmap, 32 + active * 8)? == generation {
            return Ok(SharedSnapshotPayload {
                envelope_version: VERSION_V1,
                resource_epoch: 0,
                producer_incarnation: 0,
                generation,
                applied_event_sequence: 0,
                published_at_unix_nanos: 0,
                payload,
            });
        }
    }
    Err(SnapshotError::ConcurrentChange)
}

fn existing_v2_matches(path: &Path, slot_capacity: usize) -> Result<bool, SnapshotError> {
    if !path.exists() {
        return Ok(false);
    }
    let file = File::open(path)?;
    let mmap = unsafe { MmapOptions::new().map(&file)? };
    if mmap.len() < 6 || &mmap[0..4] != MAGIC || read_u16(&mmap, 4)? != SNAPSHOT_ENVELOPE_VERSION {
        return Ok(false);
    }
    validate_v2(&mmap)?;
    Ok(read_u64(&mmap, 16)? as usize == slot_capacity)
}

fn replace_with_empty_v2(path: &Path, slot_capacity: usize) -> Result<(), SnapshotError> {
    let temporary =
        path.with_extension(format!("kss2.{}.{}.tmp", std::process::id(), unix_nanos()));
    let file = OpenOptions::new()
        .create_new(true)
        .read(true)
        .write(true)
        .open(&temporary)?;
    file.set_len((V2_HEADER_SIZE + SLOT_COUNT * slot_capacity) as u64)?;
    let mut mmap = unsafe { MmapOptions::new().map_mut(&file)? };
    mmap[0..4].copy_from_slice(MAGIC);
    write_u16(&mut mmap, 4, SNAPSHOT_ENVELOPE_VERSION)?;
    write_u16(&mut mmap, 6, SLOT_COUNT as u16)?;
    write_u32(&mut mmap, 8, V2_HEADER_SIZE as u32)?;
    write_u32(&mut mmap, 12, SLOT_METADATA_SIZE as u32)?;
    write_u64(&mut mmap, 16, slot_capacity as u64)?;
    mmap.flush()?;
    file.sync_all()?;
    std::fs::rename(&temporary, path)?;
    Ok(())
}

fn open_reader_state(path: &Path) -> Result<ReaderState, SnapshotError> {
    let file = File::open(path)?;
    let identity = file_identity(&file.metadata()?)?;
    let mmap = unsafe { MmapOptions::new().map(&file)? };
    if mmap.len() < 6 || &mmap[0..4] != MAGIC {
        return Err(SnapshotError::Corrupt("snapshot magic is invalid"));
    }
    let version = read_u16(&mmap, 4)?;
    let layout = match version {
        VERSION_V1 => {
            if mmap.len() < V1_HEADER_SIZE || read_u16(&mmap, 6)? as usize != SLOT_COUNT {
                return Err(SnapshotError::Corrupt("v1 snapshot header is invalid"));
            }
            let slot_capacity = read_u32(&mmap, 8)? as usize;
            if slot_capacity == 0 || mmap.len() != V1_HEADER_SIZE + SLOT_COUNT * slot_capacity {
                return Err(SnapshotError::Corrupt("v1 snapshot layout is invalid"));
            }
            Layout::V1 { slot_capacity }
        },
        SNAPSHOT_ENVELOPE_VERSION => Layout::V2 {
            slot_capacity: validate_v2(&mmap)?,
        },
        other => return Err(SnapshotError::UnsupportedVersion(other)),
    };
    Ok(ReaderState {
        mmap,
        identity,
        layout,
    })
}

fn validate_v2(bytes: &[u8]) -> Result<usize, SnapshotError> {
    if bytes.len() < V2_HEADER_SIZE
        || &bytes[0..4] != MAGIC
        || read_u16(bytes, 4)? != SNAPSHOT_ENVELOPE_VERSION
        || read_u16(bytes, 6)? as usize != SLOT_COUNT
        || read_u32(bytes, 8)? as usize != V2_HEADER_SIZE
        || read_u32(bytes, 12)? as usize != SLOT_METADATA_SIZE
    {
        return Err(SnapshotError::Corrupt("v2 snapshot header is invalid"));
    }
    let slot_capacity = usize::try_from(read_u64(bytes, 16)?)
        .map_err(|_| SnapshotError::Corrupt("snapshot capacity overflows usize"))?;
    if slot_capacity == 0 || bytes.len() != V2_HEADER_SIZE + SLOT_COUNT * slot_capacity {
        return Err(SnapshotError::Corrupt("v2 snapshot layout is invalid"));
    }
    Ok(slot_capacity)
}

fn writer_lease_path(path: &Path) -> PathBuf {
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("snapshot");
    path.with_file_name(format!("{name}.writer.lock"))
}

fn slot_metadata_offset(slot: usize) -> usize {
    SLOT_METADATA_OFFSET + slot * SLOT_METADATA_SIZE
}

fn payload_offset(slot: usize, slot_capacity: usize) -> usize {
    V2_HEADER_SIZE + slot * slot_capacity
}

fn atomic_u64(bytes: &[u8], offset: usize) -> &AtomicU64 {
    assert_eq!(
        (bytes.as_ptr() as usize + offset) % std::mem::align_of::<AtomicU64>(),
        0
    );
    unsafe { &*(bytes.as_ptr().add(offset).cast::<AtomicU64>()) }
}

fn read_u16(bytes: &[u8], offset: usize) -> Result<u16, SnapshotError> {
    let value = bytes
        .get(offset..offset + 2)
        .ok_or(SnapshotError::Corrupt("snapshot header is truncated"))?;
    Ok(u16::from_le_bytes([value[0], value[1]]))
}

fn read_u32(bytes: &[u8], offset: usize) -> Result<u32, SnapshotError> {
    let value = bytes
        .get(offset..offset + 4)
        .ok_or(SnapshotError::Corrupt("snapshot header is truncated"))?;
    Ok(u32::from_le_bytes(
        value.try_into().expect("checked length"),
    ))
}

fn read_u64(bytes: &[u8], offset: usize) -> Result<u64, SnapshotError> {
    let value = bytes
        .get(offset..offset + 8)
        .ok_or(SnapshotError::Corrupt("snapshot header is truncated"))?;
    Ok(u64::from_le_bytes(
        value.try_into().expect("checked length"),
    ))
}

fn write_u16(bytes: &mut [u8], offset: usize, value: u16) -> Result<(), SnapshotError> {
    bytes
        .get_mut(offset..offset + 2)
        .ok_or(SnapshotError::Corrupt("snapshot header is truncated"))?
        .copy_from_slice(&value.to_le_bytes());
    Ok(())
}

fn write_u32(bytes: &mut [u8], offset: usize, value: u32) -> Result<(), SnapshotError> {
    bytes
        .get_mut(offset..offset + 4)
        .ok_or(SnapshotError::Corrupt("snapshot header is truncated"))?
        .copy_from_slice(&value.to_le_bytes());
    Ok(())
}

fn write_u64(bytes: &mut [u8], offset: usize, value: u64) -> Result<(), SnapshotError> {
    bytes
        .get_mut(offset..offset + 8)
        .ok_or(SnapshotError::Corrupt("snapshot header is truncated"))?
        .copy_from_slice(&value.to_le_bytes());
    Ok(())
}

fn unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u64::MAX as u128) as u64
}

fn file_identity(metadata: &std::fs::Metadata) -> Result<FileIdentity, SnapshotError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        Ok(FileIdentity {
            device: metadata.dev(),
            inode: metadata.ino(),
            len: metadata.len(),
        })
    }
    #[cfg(not(unix))]
    {
        Ok(FileIdentity {
            len: metadata.len(),
        })
    }
}

#[cfg(test)]
mod tests {
    use std::process::Command;

    use super::*;

    fn metadata(generation: u64, incarnation: u64) -> SnapshotEnvelopeMetadata {
        SnapshotEnvelopeMetadata {
            resource_epoch: 2,
            producer_incarnation: incarnation,
            generation,
            applied_event_sequence: generation + 10,
            published_at_unix_nanos: 99,
        }
    }

    #[test]
    fn publishes_v2_metadata_and_payload() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("snapshot.bin");
        let mut writer = SharedSnapshotWriter::create(&path, 128).unwrap();
        writer
            .publish_with_metadata(metadata(7, 3), b"protocol-payload")
            .unwrap();
        let frame = SharedSnapshotReader::open(&path)
            .unwrap()
            .read_payload()
            .unwrap();
        assert_eq!(frame.envelope_version, 2);
        assert_eq!(frame.resource_epoch, 2);
        assert_eq!(frame.producer_incarnation, 3);
        assert_eq!(frame.generation, 7);
        assert_eq!(frame.applied_event_sequence, 17);
        assert_eq!(frame.payload, b"protocol-payload");
    }

    #[test]
    fn refuses_a_second_writer_lease() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("snapshot.bin");
        let _writer = SharedSnapshotWriter::create(&path, 128).unwrap();
        assert!(matches!(
            SharedSnapshotWriter::create(&path, 128),
            Err(SnapshotError::WriterLeaseHeld(_))
        ));
    }

    #[test]
    fn detects_payload_corruption() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("snapshot.bin");
        let mut writer = SharedSnapshotWriter::create(&path, 128).unwrap();
        writer
            .publish_with_metadata(metadata(1, 1), b"first")
            .unwrap();
        let commit = atomic_u64(&writer.mmap, ACTIVE_COMMIT_OFFSET).load(Ordering::Acquire);
        let active = (commit & 1) as usize;
        writer.mmap[payload_offset(active, 128)] ^= 0xff;
        let error = SharedSnapshotReader::open(&path)
            .unwrap()
            .read_payload()
            .unwrap_err();
        assert!(matches!(error, SnapshotError::ChecksumMismatch { .. }));
    }

    #[test]
    fn reader_reopens_after_capacity_replacement() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("snapshot.bin");
        let mut first = SharedSnapshotWriter::create(&path, 64).unwrap();
        first
            .publish_with_metadata(metadata(1, 1), b"first")
            .unwrap();
        let reader = SharedSnapshotReader::open(&path).unwrap();
        drop(first);
        let mut second = SharedSnapshotWriter::create(&path, 128).unwrap();
        second
            .publish_with_metadata(metadata(2, 2), b"second")
            .unwrap();
        let frame = reader.read_payload().unwrap();
        assert_eq!(frame.producer_incarnation, 2);
        assert_eq!(frame.payload, b"second");
    }

    #[test]
    fn crash_fixture_child() {
        let Ok(stage) = std::env::var("KAIROS_KSS_CRASH_STAGE") else {
            return;
        };
        let path = PathBuf::from(std::env::var_os("KAIROS_KSS_CRASH_PATH").unwrap());
        let mut writer = SharedSnapshotWriter::create(path, 128).unwrap();
        let metadata = metadata(2, 2);
        let active_commit = atomic_u64(&writer.mmap, ACTIVE_COMMIT_OFFSET).load(Ordering::Acquire);
        let inactive = 1 - (active_commit & 1) as usize;
        let slot_offset = slot_metadata_offset(inactive);
        atomic_u64(&writer.mmap, slot_offset + TRANSACTION_OFFSET).store(1, Ordering::Release);
        let start = payload_offset(inactive, writer.slot_capacity);
        writer.mmap[start..start + 6].copy_from_slice(b"second");
        if stage == "payload" {
            std::process::exit(99);
        }
        write_u32(&mut writer.mmap, slot_offset + LENGTH_OFFSET, 6).unwrap();
        write_u32(
            &mut writer.mmap,
            slot_offset + CHECKSUM_OFFSET,
            crc32fast::hash(b"second"),
        )
        .unwrap();
        write_u64(&mut writer.mmap, slot_offset + RESOURCE_EPOCH_OFFSET, 2).unwrap();
        write_u64(&mut writer.mmap, slot_offset + INCARNATION_OFFSET, 2).unwrap();
        write_u64(&mut writer.mmap, slot_offset + GENERATION_OFFSET, 2).unwrap();
        write_u64(&mut writer.mmap, slot_offset + WATERMARK_OFFSET, 12).unwrap();
        write_u64(&mut writer.mmap, slot_offset + PUBLISHED_AT_OFFSET, 99).unwrap();
        atomic_u64(&writer.mmap, slot_offset + TRANSACTION_OFFSET).store(2, Ordering::Release);
        if stage == "metadata" {
            std::process::exit(99);
        }
        let next = ((active_commit >> 1) + 1) << 1 | inactive as u64;
        atomic_u64(&writer.mmap, ACTIVE_COMMIT_OFFSET).store(next, Ordering::Release);
        let _ = metadata;
        std::process::exit(99);
    }

    #[test]
    fn subprocess_crashes_never_expose_partial_replacement() {
        let directory = tempfile::tempdir().unwrap();
        for (stage, expected) in [
            ("payload", b"first".as_slice()),
            ("metadata", b"first".as_slice()),
            ("active", b"second".as_slice()),
        ] {
            let path = directory.path().join(format!("{stage}.bin"));
            let mut writer = SharedSnapshotWriter::create(&path, 128).unwrap();
            writer
                .publish_with_metadata(metadata(1, 1), b"first")
                .unwrap();
            drop(writer);
            let status = Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "shared_memory::tests::crash_fixture_child"])
                .env("KAIROS_KSS_CRASH_STAGE", stage)
                .env("KAIROS_KSS_CRASH_PATH", &path)
                .status()
                .unwrap();
            assert_eq!(status.code(), Some(99));
            let frame = SharedSnapshotReader::open(&path)
                .unwrap()
                .read_payload()
                .unwrap();
            assert_eq!(frame.payload, expected);
        }
    }
}
