//! Direct, callback-driven Aeron polling shared by owner PyO3 bindings.

use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

use crate::lease::{EventLease, with_event_lease};

/// A non-queued subscription used by owner contract bindings.
///
/// Unlike the legacy Python subscription, this object does not create a
/// worker, a `Vec<u8>` channel, or Python bytes. The caller's thread polls the
/// Aeron term buffer and synchronously consumes every borrowed frame.
pub struct DirectAeronSubscription {
    creator_pid: u32,
    aeron_dir: Option<String>,
    channel: String,
    stream_id: i32,
    max_payload_len: usize,
    subscription: Mutex<Option<kairos_transport::AeronByteSubscription>>,
    closed: AtomicBool,
    polling: AtomicBool,
    epoch: AtomicU64,
}

impl DirectAeronSubscription {
    pub fn connect(
        aeron_dir: Option<&str>,
        channel: &str,
        stream_id: i32,
        max_payload_len: usize,
    ) -> PyResult<Self> {
        if channel.is_empty() || channel.as_bytes().contains(&0) {
            return Err(PyValueError::new_err(
                "Aeron channel is empty or contains NUL",
            ));
        }
        if stream_id <= 0 {
            return Err(PyValueError::new_err("Aeron stream_id must be positive"));
        }
        if max_payload_len == 0 || max_payload_len > u32::MAX as usize {
            return Err(PyValueError::new_err(
                "max_payload_len must be in the u32 framing range",
            ));
        }
        Ok(Self {
            creator_pid: std::process::id(),
            aeron_dir: aeron_dir.map(str::to_owned),
            channel: channel.to_owned(),
            stream_id,
            max_payload_len,
            subscription: Mutex::new(None),
            closed: AtomicBool::new(false),
            polling: AtomicBool::new(false),
            epoch: AtomicU64::new(0),
        })
    }

    /// Poll and synchronously visit complete messages without owning a frame.
    pub fn poll_visit(
        &self,
        py: Python<'_>,
        fragment_limit: i32,
        mut visitor: impl FnMut(Python<'_>, std::sync::Arc<EventLease>) -> PyResult<()>,
    ) -> PyResult<usize> {
        if std::process::id() != self.creator_pid {
            return Err(PyRuntimeError::new_err(
                "direct Aeron subscription belongs to another process",
            ));
        }
        if fragment_limit <= 0 {
            return Err(PyValueError::new_err("fragment_limit must be positive"));
        }
        if self.closed.load(Ordering::Acquire) {
            return Err(PyRuntimeError::new_err(
                "direct Aeron subscription is closed",
            ));
        }
        if self.polling.swap(true, Ordering::AcqRel) {
            return Err(PyRuntimeError::new_err("only one active poll is allowed"));
        }
        let _poll_guard = PollGuard(&self.polling);
        let mut subscription = self
            .subscription
            .lock()
            .map_err(|_| PyRuntimeError::new_err("direct subscription mutex is poisoned"))?;
        if subscription.is_none() {
            *subscription = Some(
                kairos_transport::AeronByteSubscription::connect_with_capacity(
                    self.aeron_dir.as_deref(),
                    &self.channel,
                    self.stream_id,
                    self.max_payload_len,
                )
                .map_err(|error| PyRuntimeError::new_err(error.to_string()))?,
            );
        }
        let subscription = subscription
            .as_mut()
            .expect("lazy subscription was initialized above");
        let mut total = 0;
        // Aeron's fragment callback cannot signal "break". Poll one fragment
        // at a time so a Python exception never consumes later messages in
        // the same batch. Fragmented messages are still reassembled by the
        // transport assembler across these calls.
        for _ in 0..fragment_limit {
            let mut callback_error = None;
            let count = subscription
                .poll_with(1, |frame| {
                    let epoch = self.epoch.fetch_add(1, Ordering::Relaxed) + 1;
                    let result = with_event_lease(frame, epoch, |lease| visitor(py, lease));
                    if let Err(error) = result {
                        callback_error = Some(error);
                    }
                })
                .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
            total += count;
            if let Some(error) = callback_error {
                return Err(error);
            }
            if count == 0 {
                break;
            }
        }
        Ok(total)
    }

    pub fn close(&self) -> PyResult<()> {
        if std::process::id() != self.creator_pid {
            return Err(PyRuntimeError::new_err(
                "direct Aeron subscription belongs to another process",
            ));
        }
        self.closed.store(true, Ordering::Release);
        self.subscription
            .lock()
            .map_err(|_| PyRuntimeError::new_err("direct subscription mutex is poisoned"))?
            .take();
        Ok(())
    }
}

struct PollGuard<'a>(&'a AtomicBool);

impl Drop for PollGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}
