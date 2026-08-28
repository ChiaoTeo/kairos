//! Callback-scoped access to an Aeron frame borrowed by Python event views.

use std::ptr::NonNull;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::thread::{self, ThreadId};

static NEXT_LEASE_ID: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum EventLeaseError {
    #[error("live event view expired when its callback returned")]
    Expired,
    #[error("live event view was accessed from a thread other than its poll thread")]
    WrongThread,
    #[error("live event view requested frame range {offset}..{end} outside length {frame_len}")]
    OutOfBounds {
        offset: usize,
        end: usize,
        frame_len: usize,
    },
}

/// Runtime lease shared by one live event and all of its nested Python views.
///
/// The address is dereferenced only on the thread that created the lease and
/// while `active` is true. `with_event_lease` is the sole constructor and
/// invalidates the lease before returning, including during unwinding.
pub struct EventLease {
    id: u64,
    poll_epoch: u64,
    owner_thread: ThreadId,
    frame: NonNull<u8>,
    frame_len: usize,
    active: AtomicBool,
}

// A lease may be retained by a Python object and moved between Python
// threads. Access on a non-owner thread is rejected before the pointer is
// dereferenced; invalidation is performed by the owner-thread scope guard.
unsafe impl Send for EventLease {}
unsafe impl Sync for EventLease {}

impl EventLease {
    pub const fn id(&self) -> u64 {
        self.id
    }

    pub const fn poll_epoch(&self) -> u64 {
        self.poll_epoch
    }

    pub const fn frame_len(&self) -> usize {
        self.frame_len
    }

    pub fn is_active(&self) -> bool {
        self.active.load(Ordering::Acquire)
    }

    /// Read the complete frame without permitting a borrow to escape.
    pub fn with_frame<R>(
        &self,
        read: impl for<'frame> FnOnce(&'frame [u8]) -> R,
    ) -> Result<R, EventLeaseError> {
        self.with_range(0, self.frame_len, read)
    }

    /// Read a checked frame range without permitting a borrow to escape.
    pub fn with_range<R>(
        &self,
        offset: usize,
        len: usize,
        read: impl for<'frame> FnOnce(&'frame [u8]) -> R,
    ) -> Result<R, EventLeaseError> {
        if thread::current().id() != self.owner_thread {
            return Err(EventLeaseError::WrongThread);
        }
        if !self.active.load(Ordering::Acquire) {
            return Err(EventLeaseError::Expired);
        }
        let end = offset
            .checked_add(len)
            .filter(|end| *end <= self.frame_len)
            .ok_or(EventLeaseError::OutOfBounds {
                offset,
                end: offset.saturating_add(len),
                frame_len: self.frame_len,
            })?;

        // SAFETY: construction is scoped to the callback that owns `frame`;
        // the owner-thread and active checks above must pass, and the checked
        // range lies within that frame. The HRTB callback prevents the slice
        // borrow from being returned by safe callers.
        let bytes = unsafe {
            std::slice::from_raw_parts(self.frame.as_ptr().add(offset).cast_const(), end - offset)
        };
        Ok(read(bytes))
    }

    fn invalidate(&self) {
        debug_assert_eq!(thread::current().id(), self.owner_thread);
        self.active.store(false, Ordering::Release);
    }
}

/// Run one synchronous event callback with access to its borrowed frame.
///
/// The returned `Arc` may be retained by Python, but all later access fails
/// deterministically because the scope guard invalidates it before this
/// function returns.
pub fn with_event_lease<R>(
    frame: &[u8],
    poll_epoch: u64,
    callback: impl FnOnce(Arc<EventLease>) -> R,
) -> R {
    let lease = Arc::new(EventLease {
        id: NEXT_LEASE_ID.fetch_add(1, Ordering::Relaxed),
        poll_epoch,
        owner_thread: thread::current().id(),
        frame: NonNull::new(frame.as_ptr().cast_mut())
            .expect("slice pointers are non-null, including for empty slices"),
        frame_len: frame.len(),
        active: AtomicBool::new(true),
    });
    let guard = EventLeaseGuard(Arc::clone(&lease));
    let result = callback(lease);
    drop(guard);
    result
}

struct EventLeaseGuard(Arc<EventLease>);

impl Drop for EventLeaseGuard {
    fn drop(&mut self) {
        self.0.invalidate();
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use super::{EventLeaseError, with_event_lease};

    #[test]
    fn callback_return_expires_retained_lease() {
        let retained = with_event_lease(b"event", 7, |lease| {
            assert_eq!(lease.with_frame(|frame| frame.len()).unwrap(), 5);
            assert_eq!(lease.poll_epoch(), 7);
            lease
        });
        assert_eq!(
            retained.with_frame(|frame| frame.len()),
            Err(EventLeaseError::Expired)
        );
    }

    #[test]
    fn callback_panic_still_expires_lease() {
        let retained = std::sync::Mutex::new(None);
        let result = std::panic::catch_unwind(|| {
            with_event_lease(b"event", 1, |lease| {
                *retained.lock().unwrap() = Some(lease);
                panic!("callback failed");
            });
        });
        assert!(result.is_err());
        let lease = retained.lock().unwrap().take().unwrap();
        assert_eq!(
            lease.with_frame(|frame| frame.len()),
            Err(EventLeaseError::Expired)
        );
    }

    #[test]
    fn another_thread_cannot_read_active_lease() {
        with_event_lease(b"event", 1, |lease| {
            let other = Arc::clone(&lease);
            assert_eq!(
                std::thread::spawn(move || other.with_frame(|frame| frame.len()))
                    .join()
                    .unwrap(),
                Err(EventLeaseError::WrongThread)
            );
            assert_eq!(lease.with_frame(|frame| frame.len()).unwrap(), 5);
        });
    }

    #[test]
    fn range_is_checked_before_dereference() {
        with_event_lease(b"event", 1, |lease| {
            assert_eq!(
                lease.with_range(1, 3, |frame| frame.to_vec()).unwrap(),
                b"ven"
            );
            assert_eq!(
                lease.with_range(4, 2, |frame| frame.len()),
                Err(EventLeaseError::OutOfBounds {
                    offset: 4,
                    end: 6,
                    frame_len: 5,
                })
            );
        });
    }
}
