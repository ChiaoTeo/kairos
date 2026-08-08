//! Fixed-capacity, in-place FlatBuffer state regions.
//!
//! The region owns no business schema. A business module creates a
//! preallocated FlatBuffer once and mutates its existing fields/slots through
//! the payload passed to `update`. The outer runtime header provides the
//! cross-process seqlock and state watermark.

use memmap2::{Mmap, MmapMut, MmapOptions};
use std::fs::{File, OpenOptions};
use std::io;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};

const MAGIC: &[u8; 4] = b"KMF1";
const FORMAT_VERSION: u16 = 1;
const HEADER_SIZE: usize = 64;
const CAPACITY_OFFSET: usize = 8;
const EPOCH_OFFSET: usize = 16;
const EVENT_SEQUENCE_OFFSET: usize = 24;
const GENERATION_OFFSET: usize = 32;
const PAYLOAD_LENGTH_OFFSET: usize = 40;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MutableSnapshot {
    pub event_sequence: u64,
    pub generation: u64,
    pub payload: Vec<u8>,
}

pub struct MutableFlatbufferWriter {
    _file: File,
    mmap: MmapMut,
    capacity: usize,
}

pub struct MutableFlatbufferReader {
    mmap: Mmap,
    capacity: usize,
    payload_length: usize,
}

impl MutableFlatbufferWriter {
    pub fn create(
        path: impl AsRef<Path>,
        capacity: usize,
        initial_payload: &[u8],
    ) -> io::Result<Self> {
        if capacity == 0 || initial_payload.is_empty() || initial_payload.len() > capacity {
            return Err(invalid_data(
                "mutable FlatBuffer capacity or initial payload is invalid",
            ));
        }
        let file = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .truncate(true)
            .open(path)?;
        file.set_len((HEADER_SIZE + capacity) as u64)?;
        let mut mmap = unsafe { MmapOptions::new().map_mut(&file)? };
        mmap.fill(0);
        mmap[0..4].copy_from_slice(MAGIC);
        mmap[4..6].copy_from_slice(&FORMAT_VERSION.to_le_bytes());
        mmap[6..8].copy_from_slice(&(HEADER_SIZE as u16).to_le_bytes());
        write_u64(&mut mmap, CAPACITY_OFFSET, capacity as u64)?;
        write_u64(&mut mmap, EPOCH_OFFSET, 0)?;
        write_u64(&mut mmap, EVENT_SEQUENCE_OFFSET, 0)?;
        write_u64(&mut mmap, GENERATION_OFFSET, 0)?;
        write_u64(
            &mut mmap,
            PAYLOAD_LENGTH_OFFSET,
            initial_payload.len() as u64,
        )?;
        mmap[HEADER_SIZE..HEADER_SIZE + initial_payload.len()].copy_from_slice(initial_payload);
        mmap.flush()?;
        Ok(Self {
            _file: file,
            mmap,
            capacity,
        })
    }

    pub fn capacity(&self) -> usize {
        self.capacity
    }

    pub fn update<F>(
        &mut self,
        event_sequence: u64,
        generation: u64,
        update: F,
    ) -> Result<(), String>
    where
        F: FnOnce(&mut [u8]) -> Result<(), String>,
    {
        let previous = unsafe { atomic_fetch_add(self.mmap.as_mut_ptr(), EPOCH_OFFSET, 1) };
        debug_assert_eq!(previous % 2, 0);
        let result = update(&mut self.mmap[HEADER_SIZE..HEADER_SIZE + self.capacity]);
        if let Err(error) = result {
            unsafe {
                atomic_store(
                    self.mmap.as_mut_ptr(),
                    EPOCH_OFFSET,
                    previous,
                    Ordering::SeqCst,
                )
            };
            return Err(error);
        }
        unsafe {
            atomic_store(
                self.mmap.as_mut_ptr(),
                EVENT_SEQUENCE_OFFSET,
                event_sequence,
                Ordering::Release,
            );
            atomic_store(
                self.mmap.as_mut_ptr(),
                GENERATION_OFFSET,
                generation,
                Ordering::Release,
            );
            atomic_store(
                self.mmap.as_mut_ptr(),
                EPOCH_OFFSET,
                previous + 2,
                Ordering::Release,
            );
        }
        self.mmap
            .flush_range(HEADER_SIZE, self.capacity)
            .map_err(|error| error.to_string())?;
        self.mmap
            .flush_range(EPOCH_OFFSET, 32)
            .map_err(|error| error.to_string())?;
        Ok(())
    }
}

impl MutableFlatbufferReader {
    pub fn open(path: impl AsRef<Path>) -> io::Result<Self> {
        let file = File::open(path)?;
        let mmap = unsafe { MmapOptions::new().map(&file)? };
        if mmap.len() < HEADER_SIZE || &mmap[0..4] != MAGIC {
            return Err(invalid_data("mutable FlatBuffer header is invalid"));
        }
        if read_u16(&mmap, 4)? != FORMAT_VERSION || read_u16(&mmap, 6)? != HEADER_SIZE as u16 {
            return Err(invalid_data("mutable FlatBuffer format is unsupported"));
        }
        let capacity = read_u64(&mmap, CAPACITY_OFFSET)? as usize;
        let payload_length = read_u64(&mmap, PAYLOAD_LENGTH_OFFSET)? as usize;
        if capacity == 0
            || payload_length == 0
            || payload_length > capacity
            || mmap.len() < HEADER_SIZE + capacity
        {
            return Err(invalid_data("mutable FlatBuffer capacity is invalid"));
        }
        Ok(Self {
            mmap,
            capacity,
            payload_length,
        })
    }

    pub fn capacity(&self) -> usize {
        self.capacity
    }

    pub fn read_consistent(&self) -> Result<MutableSnapshot, String> {
        for _ in 0..16 {
            let epoch_before =
                unsafe { atomic_load(self.mmap.as_ptr(), EPOCH_OFFSET, Ordering::Acquire) };
            if epoch_before % 2 != 0 {
                std::thread::yield_now();
                continue;
            }
            let event_sequence = unsafe {
                atomic_load(self.mmap.as_ptr(), EVENT_SEQUENCE_OFFSET, Ordering::Acquire)
            };
            let generation =
                unsafe { atomic_load(self.mmap.as_ptr(), GENERATION_OFFSET, Ordering::Acquire) };
            let payload = self.mmap[HEADER_SIZE..HEADER_SIZE + self.payload_length].to_vec();
            let epoch_after =
                unsafe { atomic_load(self.mmap.as_ptr(), EPOCH_OFFSET, Ordering::Acquire) };
            if epoch_before == epoch_after && epoch_after % 2 == 0 {
                return Ok(MutableSnapshot {
                    event_sequence,
                    generation,
                    payload,
                });
            }
            std::thread::yield_now();
        }
        Err("mutable FlatBuffer changed while being read".into())
    }
}

unsafe fn atomic_ptr(base: *const u8, offset: usize) -> *const AtomicU64 {
    debug_assert_eq!(offset % std::mem::align_of::<AtomicU64>(), 0);
    // Callers guarantee that the mapped region is alive, aligned, and large
    // enough for an AtomicU64 at this offset. The header offsets are fixed
    // above and the mmap base is page aligned.
    base.add(offset) as *const AtomicU64
}

unsafe fn atomic_load(base: *const u8, offset: usize, ordering: Ordering) -> u64 {
    (&*atomic_ptr(base, offset)).load(ordering)
}

unsafe fn atomic_fetch_add(base: *mut u8, offset: usize, value: u64) -> u64 {
    (&*(atomic_ptr(base, offset))).fetch_add(value, Ordering::SeqCst)
}

unsafe fn atomic_store(base: *mut u8, offset: usize, value: u64, ordering: Ordering) {
    (&*(atomic_ptr(base, offset))).store(value, ordering);
}

fn write_u64(bytes: &mut [u8], offset: usize, value: u64) -> io::Result<()> {
    let target = bytes
        .get_mut(offset..offset + 8)
        .ok_or_else(|| invalid_data("mutable FlatBuffer header is truncated"))?;
    target.copy_from_slice(&value.to_le_bytes());
    Ok(())
}

fn read_u16(bytes: &[u8], offset: usize) -> io::Result<u16> {
    let target = bytes
        .get(offset..offset + 2)
        .ok_or_else(|| invalid_data("mutable FlatBuffer header is truncated"))?;
    Ok(u16::from_le_bytes([target[0], target[1]]))
}

fn read_u64(bytes: &[u8], offset: usize) -> io::Result<u64> {
    let target = bytes
        .get(offset..offset + 8)
        .ok_or_else(|| invalid_data("mutable FlatBuffer header is truncated"))?;
    Ok(u64::from_le_bytes([
        target[0], target[1], target[2], target[3], target[4], target[5], target[6], target[7],
    ]))
}

fn invalid_data(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod tests {
    use super::{MutableFlatbufferReader, MutableFlatbufferWriter};

    #[test]
    fn updates_fixed_capacity_payload_without_replacing_region() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("market.mutable");
        let mut writer = MutableFlatbufferWriter::create(&path, 64, b"initial-state").unwrap();
        let reader = MutableFlatbufferReader::open(&path).unwrap();
        let region_address = reader.mmap.as_ptr();
        writer
            .update(7, 3, |payload| {
                payload[..7].copy_from_slice(b"updated");
                Ok(())
            })
            .unwrap();
        let state = reader.read_consistent().unwrap();
        assert_eq!(state.event_sequence, 7);
        assert_eq!(state.generation, 3);
        assert_eq!(&state.payload[..7], b"updated");
        assert_eq!(reader.mmap.as_ptr(), region_address);
    }

    #[test]
    fn failed_update_does_not_leave_writer_epoch_open() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("market.mutable");
        let mut writer = MutableFlatbufferWriter::create(&path, 64, b"initial-state").unwrap();
        let reader = MutableFlatbufferReader::open(&path).unwrap();
        let result = writer.update(1, 1, |_payload| Err("reject".into()));
        assert_eq!(result, Err("reject".into()));
        assert_eq!(reader.read_consistent().unwrap().event_sequence, 0);
    }
}
