//! Cross-process provider quota ledger backed by atomics in an mmap region.
//!
//! The ledger contains technical provider counters only. It is not a Risk
//! budget, connection registry, command proxy, or secret store. File locking
//! is used only while creating/validating immutable slot metadata; every
//! request reservation is a lock-free compare-and-swap on its slot.

use std::fs::{File, OpenOptions};
use std::io;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};

use fs2::FileExt;
use memmap2::{MmapMut, MmapOptions};
use sha2::{Digest, Sha256};

const MAGIC: &[u8; 4] = b"KQL1";
const FORMAT_VERSION: u16 = 1;
const HEADER_SIZE: usize = 64;
const SLOT_SIZE: usize = 128;
const SLOT_COUNT: usize = 256;
const SLOT_STATE: usize = 0;
const SLOT_KEY_HASH: usize = 8;
const SLOT_LIMIT: usize = 40;
const SLOT_RESERVE: usize = 48;
const SLOT_WINDOW_MILLIS: usize = 56;
const SLOT_WINDOW: usize = 64;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum SharedQuotaPriority {
    Reserved,
    Ordinary,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct SharedQuotaExhausted {
    pub retry_after_millis: u64,
}

pub(crate) struct SharedFixedWindowQuota {
    _file: File,
    mmap: MmapMut,
    slot_offset: usize,
    limit: u32,
    reserve: u32,
    window_millis: u64,
}

impl SharedFixedWindowQuota {
    pub(crate) fn open_or_register(
        path: impl AsRef<Path>,
        scope_key: &str,
        limit: u32,
        reserve: u32,
        window_millis: u64,
    ) -> io::Result<Self> {
        if scope_key.trim().is_empty() || limit == 0 || reserve >= limit || window_millis == 0 {
            return Err(invalid_input("shared quota slot configuration is invalid"));
        }
        if window_millis > u32::MAX as u64 {
            return Err(invalid_input("shared quota window is too large"));
        }
        if let Some(parent) = path.as_ref().parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut options = OpenOptions::new();
        options.create(true).read(true).write(true).truncate(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let file = options.open(path)?;
        file.lock_exclusive()?;
        let result = (|| {
            let expected_len = HEADER_SIZE + SLOT_COUNT * SLOT_SIZE;
            if file.metadata()?.len() == 0 {
                file.set_len(expected_len as u64)?;
                let mut mmap = unsafe { MmapOptions::new().map_mut(&file)? };
                mmap.fill(0);
                mmap[0..4].copy_from_slice(MAGIC);
                mmap[4..6].copy_from_slice(&FORMAT_VERSION.to_le_bytes());
                mmap[6..8].copy_from_slice(&(HEADER_SIZE as u16).to_le_bytes());
                mmap[8..12].copy_from_slice(&(SLOT_COUNT as u32).to_le_bytes());
                mmap[12..16].copy_from_slice(&(SLOT_SIZE as u32).to_le_bytes());
                mmap.flush()?;
            }
            let mut mmap = unsafe { MmapOptions::new().map_mut(&file)? };
            validate_header(&mmap)?;
            let key_hash: [u8; 32] = Sha256::digest(scope_key.as_bytes()).into();
            let mut empty = None;
            let mut found = None;
            for index in 0..SLOT_COUNT {
                let offset = HEADER_SIZE + index * SLOT_SIZE;
                let state = unsafe { atomic_at(mmap.as_ptr(), offset + SLOT_STATE) }
                    .load(Ordering::Acquire);
                if state == 0 {
                    empty.get_or_insert(offset);
                } else if state == 1
                    && mmap[offset + SLOT_KEY_HASH..offset + SLOT_KEY_HASH + 32] == key_hash
                {
                    found = Some(offset);
                    break;
                }
            }
            let slot_offset = if let Some(offset) = found {
                validate_slot(&mmap, offset, limit, reserve, window_millis)?;
                offset
            } else {
                let offset = empty.ok_or_else(|| invalid_data("shared quota ledger is full"))?;
                mmap[offset + SLOT_KEY_HASH..offset + SLOT_KEY_HASH + 32]
                    .copy_from_slice(&key_hash);
                write_u64(&mut mmap, offset + SLOT_LIMIT, limit as u64)?;
                write_u64(&mut mmap, offset + SLOT_RESERVE, reserve as u64)?;
                write_u64(&mut mmap, offset + SLOT_WINDOW_MILLIS, window_millis)?;
                unsafe { atomic_at(mmap.as_ptr(), offset + SLOT_WINDOW) }
                    .store(0, Ordering::Release);
                unsafe { atomic_at(mmap.as_ptr(), offset + SLOT_STATE) }
                    .store(1, Ordering::Release);
                mmap.flush_range(offset, SLOT_SIZE)?;
                offset
            };
            Ok(Self {
                _file: file.try_clone()?,
                mmap,
                slot_offset,
                limit,
                reserve,
                window_millis,
            })
        })();
        let unlock = FileExt::unlock(&file);
        match (result, unlock) {
            (Ok(value), Ok(())) => Ok(value),
            (Err(error), _) | (Ok(_), Err(error)) => Err(error),
        }
    }

    pub(crate) fn acquire(
        &self,
        weight: u32,
        priority: SharedQuotaPriority,
        now_millis: u64,
    ) -> Result<(), SharedQuotaExhausted> {
        let window_id = now_millis / self.window_millis;
        if window_id > u32::MAX as u64 {
            return Err(SharedQuotaExhausted {
                retry_after_millis: self.window_millis,
            });
        }
        let counter = unsafe { atomic_at(self.mmap.as_ptr(), self.slot_offset + SLOT_WINDOW) };
        loop {
            let current = counter.load(Ordering::Acquire);
            let current_window = current >> 32;
            let current_used = if current_window == window_id {
                current as u32
            } else {
                0
            };
            let limit = match priority {
                SharedQuotaPriority::Reserved => self.limit,
                SharedQuotaPriority::Ordinary => self.limit.saturating_sub(self.reserve),
            };
            let Some(next_used) = current_used.checked_add(weight) else {
                return Err(self.exhausted(now_millis, window_id));
            };
            if next_used > limit {
                return Err(self.exhausted(now_millis, window_id));
            }
            let next = (window_id << 32) | u64::from(next_used);
            if counter
                .compare_exchange_weak(current, next, Ordering::AcqRel, Ordering::Acquire)
                .is_ok()
            {
                return Ok(());
            }
        }
    }

    /// Raise the local shared counter to at least the provider-observed count.
    /// A stale/lower observation can never refund capacity.
    pub(crate) fn observe(&self, used_weight: u32, now_millis: u64) {
        let window_id = now_millis / self.window_millis;
        if window_id > u32::MAX as u64 {
            return;
        }
        let counter = unsafe { atomic_at(self.mmap.as_ptr(), self.slot_offset + SLOT_WINDOW) };
        loop {
            let current = counter.load(Ordering::Acquire);
            let current_window = current >> 32;
            let current_used = if current_window == window_id {
                current as u32
            } else {
                0
            };
            if current_window == window_id && current_used >= used_weight {
                return;
            }
            let next = (window_id << 32) | u64::from(used_weight);
            if counter
                .compare_exchange_weak(current, next, Ordering::AcqRel, Ordering::Acquire)
                .is_ok()
            {
                return;
            }
        }
    }

    #[cfg(test)]
    fn used_at(&self, now_millis: u64) -> u32 {
        let current = unsafe { atomic_at(self.mmap.as_ptr(), self.slot_offset + SLOT_WINDOW) }
            .load(Ordering::Acquire);
        if current >> 32 == now_millis / self.window_millis {
            current as u32
        } else {
            0
        }
    }

    fn exhausted(&self, now_millis: u64, window_id: u64) -> SharedQuotaExhausted {
        SharedQuotaExhausted {
            retry_after_millis: window_id
                .saturating_add(1)
                .saturating_mul(self.window_millis)
                .saturating_sub(now_millis),
        }
    }
}

fn validate_header(mmap: &[u8]) -> io::Result<()> {
    if mmap.len() != HEADER_SIZE + SLOT_COUNT * SLOT_SIZE
        || &mmap[0..4] != MAGIC
        || read_u16(mmap, 4)? != FORMAT_VERSION
        || read_u16(mmap, 6)? != HEADER_SIZE as u16
        || read_u32(mmap, 8)? != SLOT_COUNT as u32
        || read_u32(mmap, 12)? != SLOT_SIZE as u32
    {
        return Err(invalid_data("shared quota ledger header is invalid"));
    }
    Ok(())
}

fn validate_slot(
    mmap: &[u8],
    offset: usize,
    limit: u32,
    reserve: u32,
    window_millis: u64,
) -> io::Result<()> {
    if read_u64(mmap, offset + SLOT_LIMIT)? != limit as u64
        || read_u64(mmap, offset + SLOT_RESERVE)? != reserve as u64
        || read_u64(mmap, offset + SLOT_WINDOW_MILLIS)? != window_millis
    {
        return Err(invalid_data(
            "shared quota slot already exists with different limits",
        ));
    }
    Ok(())
}

unsafe fn atomic_at(base: *const u8, offset: usize) -> &'static AtomicU64 {
    debug_assert_eq!(offset % std::mem::align_of::<AtomicU64>(), 0);
    &*(base.add(offset) as *const AtomicU64)
}

fn write_u64(bytes: &mut [u8], offset: usize, value: u64) -> io::Result<()> {
    bytes
        .get_mut(offset..offset + 8)
        .ok_or_else(|| invalid_data("shared quota slot is truncated"))?
        .copy_from_slice(&value.to_le_bytes());
    Ok(())
}

fn read_u16(bytes: &[u8], offset: usize) -> io::Result<u16> {
    let value = bytes
        .get(offset..offset + 2)
        .ok_or_else(|| invalid_data("shared quota ledger is truncated"))?;
    Ok(u16::from_le_bytes([value[0], value[1]]))
}

fn read_u32(bytes: &[u8], offset: usize) -> io::Result<u32> {
    let value = bytes
        .get(offset..offset + 4)
        .ok_or_else(|| invalid_data("shared quota ledger is truncated"))?;
    Ok(u32::from_le_bytes([value[0], value[1], value[2], value[3]]))
}

fn read_u64(bytes: &[u8], offset: usize) -> io::Result<u64> {
    let value = bytes
        .get(offset..offset + 8)
        .ok_or_else(|| invalid_data("shared quota ledger is truncated"))?;
    Ok(u64::from_le_bytes(
        value.try_into().expect("eight-byte quota value"),
    ))
}

fn invalid_input(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message)
}

fn invalid_data(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod tests {
    use super::{SharedFixedWindowQuota, SharedQuotaPriority};

    #[test]
    fn independent_mappings_atomically_share_one_scope() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("provider-quota.mmap");
        let first = SharedFixedWindowQuota::open_or_register(
            &path,
            "binance:live:egress:primary:request-weight-1m",
            10,
            2,
            60_000,
        )
        .unwrap();
        let second = SharedFixedWindowQuota::open_or_register(
            &path,
            "binance:live:egress:primary:request-weight-1m",
            10,
            2,
            60_000,
        )
        .unwrap();

        first
            .acquire(5, SharedQuotaPriority::Ordinary, 60_000)
            .unwrap();
        second
            .acquire(3, SharedQuotaPriority::Ordinary, 60_001)
            .unwrap();
        assert!(second
            .acquire(1, SharedQuotaPriority::Ordinary, 60_002)
            .is_err());
        first
            .acquire(2, SharedQuotaPriority::Reserved, 60_003)
            .unwrap();
        assert_eq!(second.used_at(60_004), 10);
    }

    #[test]
    fn separate_process_reservation_is_visible_in_parent_mapping() {
        const CHILD_PATH: &str = "KAIROS_SHARED_QUOTA_CHILD_PATH";
        const SCOPE: &str = "binance:live:egress:cross-process:request-weight-1m";
        if let Some(path) = std::env::var_os(CHILD_PATH) {
            let quota =
                SharedFixedWindowQuota::open_or_register(path, SCOPE, 10, 2, 60_000).unwrap();
            quota
                .acquire(5, SharedQuotaPriority::Ordinary, 60_000)
                .unwrap();
            return;
        }

        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("provider-quota.mmap");
        let parent = SharedFixedWindowQuota::open_or_register(&path, SCOPE, 10, 2, 60_000).unwrap();
        let status = std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "services::quota::tests::separate_process_reservation_is_visible_in_parent_mapping",
                "--nocapture",
            ])
            .env(CHILD_PATH, &path)
            .status()
            .unwrap();
        assert!(status.success());
        assert_eq!(parent.used_at(60_001), 5);
        parent
            .acquire(3, SharedQuotaPriority::Ordinary, 60_002)
            .unwrap();
        assert!(parent
            .acquire(1, SharedQuotaPriority::Ordinary, 60_003)
            .is_err());
    }

    #[test]
    fn provider_observation_never_refunds_shared_capacity() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("provider-quota.mmap");
        let quota = SharedFixedWindowQuota::open_or_register(
            &path,
            "binance:test:egress:primary:request-weight-1m",
            20,
            2,
            60_000,
        )
        .unwrap();
        quota
            .acquire(8, SharedQuotaPriority::Ordinary, 120_000)
            .unwrap();
        quota.observe(3, 120_001);
        assert_eq!(quota.used_at(120_002), 8);
        quota.observe(12, 120_003);
        assert_eq!(quota.used_at(120_004), 12);
    }
}
