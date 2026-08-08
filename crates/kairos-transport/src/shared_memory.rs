//! Cross-process reader for the double-slot mmap snapshot layout.

use std::fs::{File, OpenOptions};
use std::io;
use std::path::Path;

use memmap2::{Mmap, MmapMut, MmapOptions};
use serde::Serialize;

use kairos_protocol::generated::kairos::market::v_1::{
    market_data_snapshot_buffer_has_identifier, root_as_market_data_snapshot,
};

const MAGIC: &[u8; 4] = b"KSS1";
const FORMAT_VERSION: u16 = 1;
const HEADER_SIZE: usize = 64;
const SLOT_COUNT: u16 = 2;
const ACTIVE_OFFSET: usize = 12;
const SLOT_LENGTH_OFFSET: usize = 24;
const SLOT_GENERATION_OFFSET: usize = 32;

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct SnapshotMarketData {
    pub generation: u64,
    pub snapshot_id: String,
    pub view_key: String,
    pub owner_actor_id: String,
    pub workspace_id: Option<String>,
    pub launch_id: Option<String>,
    pub instance_id: Option<String>,
    pub version: u64,
    pub item_count: usize,
    pub first_instrument_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SharedSnapshotPayload {
    pub generation: u64,
    pub payload: Vec<u8>,
}

pub struct SharedSnapshotReader {
    mmap: Mmap,
    slot_size: usize,
}

/// Single-writer side of the double-slot snapshot layout.
pub struct SharedSnapshotWriter {
    file: File,
    mmap: MmapMut,
    slot_size: usize,
}

impl SharedSnapshotWriter {
    pub fn create(path: impl AsRef<Path>, slot_size: usize) -> io::Result<Self> {
        if slot_size == 0 {
            return Err(invalid_data("shared snapshot slot size must be positive"));
        }
        let file = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .truncate(true)
            .open(path)?;
        file.set_len((HEADER_SIZE + SLOT_COUNT as usize * slot_size) as u64)?;
        let mut mmap = unsafe { MmapOptions::new().map_mut(&file)? };
        mmap[0..4].copy_from_slice(MAGIC);
        mmap[4..6].copy_from_slice(&FORMAT_VERSION.to_le_bytes());
        mmap[6..8].copy_from_slice(&SLOT_COUNT.to_le_bytes());
        mmap[8..12].copy_from_slice(&(slot_size as u32).to_le_bytes());
        mmap[ACTIVE_OFFSET] = 0;
        mmap.flush()?;
        Ok(Self {
            file,
            mmap,
            slot_size,
        })
    }

    pub fn publish(&mut self, generation: u64, payload: &[u8]) -> io::Result<()> {
        if payload.is_empty() || payload.len() > self.slot_size {
            return Err(invalid_data(
                "shared snapshot payload is empty or too large",
            ));
        }
        let active = self.mmap[ACTIVE_OFFSET] as usize;
        if active >= SLOT_COUNT as usize {
            return Err(invalid_data("shared snapshot active slot is invalid"));
        }
        let inactive = 1 - active;
        let start = HEADER_SIZE + inactive * self.slot_size;
        self.mmap[start..start + payload.len()].copy_from_slice(payload);
        self.mmap.flush_range(start, payload.len())?;
        let length_offset = SLOT_LENGTH_OFFSET + inactive * 4;
        self.mmap[length_offset..length_offset + 4]
            .copy_from_slice(&(payload.len() as u32).to_le_bytes());
        let generation_offset = SLOT_GENERATION_OFFSET + inactive * 8;
        self.mmap[generation_offset..generation_offset + 8]
            .copy_from_slice(&generation.to_le_bytes());
        self.mmap[16..24].copy_from_slice(&generation.to_le_bytes());
        self.mmap[ACTIVE_OFFSET] = inactive as u8;
        self.mmap.flush_range(12, 52)?;
        let _ = &self.file;
        Ok(())
    }
}

impl SharedSnapshotReader {
    pub fn open(path: impl AsRef<Path>) -> io::Result<Self> {
        let file = File::open(path)?;
        let mmap = unsafe { MmapOptions::new().map(&file)? };
        if mmap.len() < HEADER_SIZE {
            return Err(invalid_data(
                "shared snapshot file is smaller than its header",
            ));
        }
        if &mmap[0..4] != MAGIC {
            return Err(invalid_data("shared snapshot magic is invalid"));
        }
        if read_u16(&mmap, 4)? != FORMAT_VERSION || read_u16(&mmap, 6)? != SLOT_COUNT {
            return Err(invalid_data("shared snapshot format is unsupported"));
        }
        let slot_size = read_u32(&mmap, 8)? as usize;
        if slot_size == 0 || mmap.len() < HEADER_SIZE + SLOT_COUNT as usize * slot_size {
            return Err(invalid_data("shared snapshot slot layout is invalid"));
        }
        Ok(Self { mmap, slot_size })
    }

    pub fn read_market_data(&self) -> Result<SnapshotMarketData, String> {
        let snapshot = self.read_payload()?;
        decode_market_data(&snapshot.payload, snapshot.generation)
    }

    pub fn read_payload(&self) -> Result<SharedSnapshotPayload, String> {
        for _ in 0..8 {
            let active = self.mmap[ACTIVE_OFFSET] as usize;
            if active >= SLOT_COUNT as usize {
                return Err("shared snapshot active slot is invalid".into());
            }
            let length = read_u32(&self.mmap, SLOT_LENGTH_OFFSET + active * 4)
                .map_err(|error| error.to_string())? as usize;
            let generation = read_u64(&self.mmap, SLOT_GENERATION_OFFSET + active * 8)
                .map_err(|error| error.to_string())?;
            if length == 0 || length > self.slot_size {
                return Err("shared snapshot active slot is empty or too large".into());
            }
            let start = HEADER_SIZE + active * self.slot_size;
            let payload = self.mmap[start..start + length].to_vec();
            let active_after = self.mmap[ACTIVE_OFFSET] as usize;
            let generation_after = read_u64(&self.mmap, SLOT_GENERATION_OFFSET + active * 8)
                .map_err(|error| error.to_string())?;
            if active == active_after && generation == generation_after {
                return Ok(SharedSnapshotPayload {
                    generation,
                    payload,
                });
            }
        }
        Err("shared snapshot changed while being read".into())
    }
}

fn decode_market_data(payload: &[u8], generation: u64) -> Result<SnapshotMarketData, String> {
    if !market_data_snapshot_buffer_has_identifier(payload) {
        return Err("shared snapshot payload has an invalid market data identifier".into());
    }
    let snapshot = root_as_market_data_snapshot(payload)
        .map_err(|error| format!("invalid MarketDataSnapshot: {error}"))?;
    let header = snapshot.header();
    let data = snapshot.payload();
    let item_count = data.quotes().map_or(0, |quotes| quotes.len());
    let first_instrument_id = data
        .quotes()
        .and_then(|items| (items.len() > 0).then(|| items.get(0).instrument_id().to_owned()));
    Ok(SnapshotMarketData {
        generation,
        snapshot_id: header.snapshot_id().to_owned(),
        view_key: header.view_key().to_owned(),
        owner_actor_id: header.owner_actor_id().to_owned(),
        workspace_id: header.workspace_id().map(str::to_owned),
        launch_id: header.launch_id().map(str::to_owned),
        instance_id: header.instance_id().map(str::to_owned),
        version: header.version(),
        item_count,
        first_instrument_id,
    })
}

fn read_u16(bytes: &[u8], offset: usize) -> io::Result<u16> {
    let value = bytes
        .get(offset..offset + 2)
        .ok_or_else(|| invalid_data("shared snapshot header is truncated"))?;
    Ok(u16::from_le_bytes([value[0], value[1]]))
}

fn read_u32(bytes: &[u8], offset: usize) -> io::Result<u32> {
    let value = bytes
        .get(offset..offset + 4)
        .ok_or_else(|| invalid_data("shared snapshot header is truncated"))?;
    Ok(u32::from_le_bytes([value[0], value[1], value[2], value[3]]))
}

fn read_u64(bytes: &[u8], offset: usize) -> io::Result<u64> {
    let value = bytes
        .get(offset..offset + 8)
        .ok_or_else(|| invalid_data("shared snapshot header is truncated"))?;
    Ok(u64::from_le_bytes([
        value[0], value[1], value[2], value[3], value[4], value[5], value[6], value[7],
    ]))
}

fn invalid_data(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod tests {
    use super::{SharedSnapshotReader, SharedSnapshotWriter};

    #[test]
    fn reads_arbitrary_payloads_without_knowing_the_protocol() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("snapshot.bin");
        let mut writer = SharedSnapshotWriter::create(&path, 128).unwrap();
        writer.publish(7, b"protocol-payload").unwrap();

        let reader = SharedSnapshotReader::open(&path).unwrap();
        let payload = reader.read_payload().unwrap();
        assert_eq!(payload.generation, 7);
        assert_eq!(payload.payload, b"protocol-payload");
    }
}
