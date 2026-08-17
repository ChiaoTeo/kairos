use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, SyncSender, TryRecvError, TrySendError};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::errors;

const MAX_QUEUE_CAPACITY: usize = 1_048_576;
const CLOSE_TIMEOUT: Duration = Duration::from_secs(2);

#[pyclass(frozen, module = "kairospy._native_transport")]
#[derive(Clone)]
pub struct StreamSpec {
    #[pyo3(get)]
    channel: String,
    #[pyo3(get)]
    stream_id: i32,
    #[pyo3(get)]
    max_payload_len: usize,
}

#[pymethods]
impl StreamSpec {
    #[new]
    fn new(
        py: Python<'_>,
        channel: String,
        stream_id: i32,
        max_payload_len: usize,
    ) -> PyResult<Self> {
        if channel.is_empty() || channel.len() > 16 * 1024 || channel.as_bytes().contains(&0) {
            return Err(errors::with_code(
                py,
                PyErr::new::<errors::ConfigurationError, _>(
                    "Aeron channel is empty, too long, or contains NUL",
                ),
                "invalid_channel",
            ));
        }
        if stream_id <= 0 {
            return Err(errors::with_code(
                py,
                PyErr::new::<errors::ConfigurationError, _>("Aeron stream_id must be positive"),
                "invalid_stream_id",
            ));
        }
        if max_payload_len == 0 || max_payload_len > u32::MAX as usize {
            return Err(errors::with_code(
                py,
                PyErr::new::<errors::ConfigurationError, _>(
                    "max_payload_len must be in the u32 framing range",
                ),
                "invalid_payload_limit",
            ));
        }
        Ok(Self {
            channel,
            stream_id,
            max_payload_len,
        })
    }
}

pub struct AeronSubscription {
    creator_pid: u32,
    receiver: Arc<Mutex<Receiver<Vec<u8>>>>,
    worker: Mutex<Option<JoinHandle<()>>>,
    done: Mutex<Receiver<()>>,
    stop: Arc<AtomicBool>,
    overflow: Arc<AtomicBool>,
    failed: Arc<Mutex<Option<String>>>,
    closed: AtomicBool,
    polling: AtomicBool,
}

#[pyclass(name = "AeronSubscription", module = "kairospy._native_transport")]
pub struct PyAeronSubscription {
    inner: AeronSubscription,
}

#[pymethods]
impl PyAeronSubscription {
    #[new]
    #[pyo3(signature = (spec, aeron_dir=None, queue_capacity=1024))]
    fn new(
        py: Python<'_>,
        spec: StreamSpec,
        aeron_dir: Option<String>,
        queue_capacity: usize,
    ) -> PyResult<Self> {
        if queue_capacity == 0 || queue_capacity > MAX_QUEUE_CAPACITY {
            return Err(errors::with_code(
                py,
                PyErr::new::<errors::ConfigurationError, _>(
                    "queue_capacity is outside the supported range",
                ),
                "invalid_queue_capacity",
            ));
        }
        let (sender, receiver) = mpsc::sync_channel(queue_capacity);
        let (ready_sender, ready_receiver) = mpsc::sync_channel(1);
        let (done_sender, done_receiver) = mpsc::sync_channel(1);
        let stop = Arc::new(AtomicBool::new(false));
        let overflow = Arc::new(AtomicBool::new(false));
        let failed = Arc::new(Mutex::new(None));
        let worker = spawn_worker(
            spec,
            aeron_dir,
            sender,
            ready_sender,
            done_sender,
            Arc::clone(&stop),
            Arc::clone(&overflow),
            Arc::clone(&failed),
        )
        .map_err(|message| {
            errors::with_code(
                py,
                PyErr::new::<errors::WorkerExitedError, _>(message),
                "worker_spawn_failed",
            )
        })?;
        let ready = py.detach(move || ready_receiver.recv());
        match ready {
            Ok(Ok(())) => {}
            Ok(Err(message)) => {
                return Err(errors::with_code(
                    py,
                    PyErr::new::<errors::DriverUnavailableError, _>(message),
                    "driver_unavailable",
                ));
            }
            Err(_) => {
                return Err(errors::with_code(
                    py,
                    PyErr::new::<errors::WorkerExitedError, _>(
                        "Aeron worker exited before readiness",
                    ),
                    "worker_exited",
                ));
            }
        }
        Ok(Self {
            inner: AeronSubscription {
                creator_pid: std::process::id(),
                receiver: Arc::new(Mutex::new(receiver)),
                worker: Mutex::new(Some(worker)),
                done: Mutex::new(done_receiver),
                stop,
                overflow,
                failed,
                closed: AtomicBool::new(false),
                polling: AtomicBool::new(false),
            },
        })
    }

    #[pyo3(signature = (max_frames=64, timeout_ms=10))]
    fn poll<'py>(
        &self,
        py: Python<'py>,
        max_frames: usize,
        timeout_ms: u64,
    ) -> PyResult<Vec<Bound<'py, PyBytes>>> {
        self.inner.ensure_usable(py)?;
        if max_frames == 0 || max_frames > 65_536 || timeout_ms > 60_000 {
            return Err(errors::with_code(
                py,
                PyErr::new::<errors::ConfigurationError, _>(
                    "poll limit or timeout is outside the supported range",
                ),
                "invalid_poll_request",
            ));
        }
        if self.inner.polling.swap(true, Ordering::AcqRel) {
            return Err(errors::with_code(
                py,
                PyErr::new::<errors::NativeTransportError, _>("only one active poll is allowed"),
                "concurrent_poll",
            ));
        }
        let _poll_guard = PollGuard(&self.inner.polling);
        if self.inner.overflow.load(Ordering::Acquire) {
            return Err(errors::with_code(
                py,
                PyErr::new::<errors::QueueOverflowError, _>(
                    "Aeron subscription queue overflowed; recreate and resync",
                ),
                "queue_overflow",
            ));
        }
        if let Some(message) = self.inner.failure() {
            return Err(errors::with_code(
                py,
                PyErr::new::<errors::WorkerExitedError, _>(message),
                "worker_exited",
            ));
        }
        let receiver = Arc::clone(&self.inner.receiver);
        let timeout = Duration::from_millis(timeout_ms);
        let frames = py.detach(move || drain(receiver, max_frames, timeout));
        if self.inner.overflow.load(Ordering::Acquire) {
            return Err(errors::with_code(
                py,
                PyErr::new::<errors::QueueOverflowError, _>(
                    "Aeron subscription queue overflowed; recreate and resync",
                ),
                "queue_overflow",
            ));
        }
        frames
            .map_err(|message| {
                errors::with_code(
                    py,
                    PyErr::new::<errors::WorkerExitedError, _>(message),
                    "worker_exited",
                )
            })?
            .into_iter()
            .map(|frame| Ok(PyBytes::new(py, &frame)))
            .collect()
    }

    fn close(&self, py: Python<'_>) -> PyResult<()> {
        self.inner.ensure_process(py)?;
        if self.inner.closed.swap(true, Ordering::AcqRel) {
            return Ok(());
        }
        self.inner.stop.store(true, Ordering::Release);
        let completed = py.detach(|| {
            self.inner
                .done
                .lock()
                .ok()
                .and_then(|receiver| receiver.recv_timeout(CLOSE_TIMEOUT).ok())
                .is_some()
        });
        let worker = self
            .inner
            .worker
            .lock()
            .ok()
            .and_then(|mut value| value.take());
        if !completed {
            drop(worker);
            return Err(errors::with_code(
                py,
                PyErr::new::<errors::WorkerExitedError, _>(
                    "Aeron worker did not stop before close deadline",
                ),
                "close_timeout",
            ));
        }
        if worker.is_some_and(|worker| worker.join().is_err()) {
            return Err(errors::with_code(
                py,
                PyErr::new::<errors::WorkerExitedError, _>("Aeron worker panicked"),
                "worker_panic",
            ));
        }
        Ok(())
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __exit__(
        &self,
        py: Python<'_>,
        _exception_type: &Bound<'_, PyAny>,
        _exception: &Bound<'_, PyAny>,
        _traceback: &Bound<'_, PyAny>,
    ) -> PyResult<bool> {
        self.close(py)?;
        Ok(false)
    }
}

impl Drop for PyAeronSubscription {
    fn drop(&mut self) {
        self.inner.stop.store(true, Ordering::Release);
    }
}

impl AeronSubscription {
    fn ensure_process(&self, py: Python<'_>) -> PyResult<()> {
        if std::process::id() != self.creator_pid {
            return Err(errors::forked(py));
        }
        Ok(())
    }

    fn ensure_usable(&self, py: Python<'_>) -> PyResult<()> {
        self.ensure_process(py)?;
        if self.closed.load(Ordering::Acquire) {
            return Err(errors::closed(py));
        }
        Ok(())
    }

    fn failure(&self) -> Option<String> {
        self.failed.lock().ok().and_then(|value| value.clone())
    }
}

struct PollGuard<'a>(&'a AtomicBool);

impl Drop for PollGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

#[allow(clippy::too_many_arguments)]
fn spawn_worker(
    spec: StreamSpec,
    aeron_dir: Option<String>,
    sender: SyncSender<Vec<u8>>,
    ready: SyncSender<Result<(), String>>,
    done: SyncSender<()>,
    stop: Arc<AtomicBool>,
    overflow: Arc<AtomicBool>,
    failed: Arc<Mutex<Option<String>>>,
) -> Result<JoinHandle<()>, String> {
    std::thread::Builder::new()
        .name("python-aeron-subscription".into())
        .spawn(move || {
            let run = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let mut subscription =
                    match kairos_transport::AeronByteSubscription::connect_with_capacity(
                        aeron_dir.as_deref(),
                        &spec.channel,
                        spec.stream_id,
                        spec.max_payload_len,
                    ) {
                        Ok(subscription) => {
                            let _ = ready.send(Ok(()));
                            subscription
                        }
                        Err(message) => {
                            let _ = ready.send(Err(message.to_string()));
                            return;
                        }
                    };
                while !stop.load(Ordering::Acquire) {
                    match subscription.next_frame() {
                        Ok(Some(frame)) => match sender.try_send(frame) {
                            Ok(()) => {}
                            Err(TrySendError::Full(_)) => {
                                overflow.store(true, Ordering::Release);
                                break;
                            }
                            Err(TrySendError::Disconnected(_)) => break,
                        },
                        Ok(None) => std::thread::sleep(Duration::from_millis(1)),
                        Err(message) => {
                            if let Ok(mut value) = failed.lock() {
                                *value = Some(message.to_string());
                            }
                            break;
                        }
                    }
                }
            }));
            if run.is_err() {
                if let Ok(mut value) = failed.lock() {
                    *value = Some("Aeron worker panicked".into());
                }
            }
            let _ = done.send(());
        })
        .map_err(|error| error.to_string())
}

fn drain(
    receiver: Arc<Mutex<Receiver<Vec<u8>>>>,
    max_frames: usize,
    timeout: Duration,
) -> Result<Vec<Vec<u8>>, String> {
    let receiver = receiver
        .lock()
        .map_err(|_| "Aeron receiver lock poisoned".to_string())?;
    let deadline = Instant::now() + timeout;
    let mut frames = Vec::with_capacity(max_frames.min(64));
    while frames.len() < max_frames {
        let result = if frames.is_empty() {
            receiver.recv_timeout(deadline.saturating_duration_since(Instant::now()))
        } else {
            match receiver.try_recv() {
                Ok(frame) => {
                    frames.push(frame);
                    continue;
                }
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => return Ok(frames),
            }
        };
        match result {
            Ok(frame) => frames.push(frame),
            Err(RecvTimeoutError::Timeout) => break,
            Err(RecvTimeoutError::Disconnected) => break,
        }
    }
    Ok(frames)
}
