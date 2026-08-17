use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use kairos_transport::SharedSnapshotReader;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::errors;

enum ReadFailure {
    Closed,
    Snapshot(kairos_transport::SnapshotError),
}

#[pyclass(frozen, module = "kairospy._native_transport")]
pub struct SnapshotFrame {
    #[pyo3(get)]
    pub envelope_version: u16,
    #[pyo3(get)]
    pub resource_epoch: u64,
    #[pyo3(get)]
    pub producer_incarnation: u64,
    #[pyo3(get)]
    pub generation: u64,
    #[pyo3(get)]
    pub applied_event_sequence: u64,
    #[pyo3(get)]
    pub published_at_unix_nanos: u64,
    payload: Vec<u8>,
}

#[pymethods]
impl SnapshotFrame {
    #[getter]
    fn payload<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, &self.payload)
    }
}

#[pyclass(module = "kairospy._native_transport")]
pub struct SnapshotReader {
    creator_pid: u32,
    reader: Arc<Mutex<Option<SharedSnapshotReader>>>,
}

#[pymethods]
impl SnapshotReader {
    #[new]
    fn new(py: Python<'_>, path: PathBuf) -> PyResult<Self> {
        let opened = std::panic::catch_unwind(|| SharedSnapshotReader::open(&path))
            .map_err(|_| errors::internal_panic(py))?
            .map_err(|error| errors::snapshot(py, error))?;
        Ok(Self {
            creator_pid: std::process::id(),
            reader: Arc::new(Mutex::new(Some(opened))),
        })
    }

    fn read(&self, py: Python<'_>) -> PyResult<SnapshotFrame> {
        self.ensure_process(py)?;
        let reader = Arc::clone(&self.reader);
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            py.detach(move || {
                let guard = reader.lock().map_err(|_| ReadFailure::Closed)?;
                let reader = guard.as_ref().ok_or(ReadFailure::Closed)?;
                reader.read_payload().map_err(ReadFailure::Snapshot)
            })
        }))
        .map_err(|_| errors::internal_panic(py))?;
        let frame = match result {
            Ok(frame) => frame,
            Err(ReadFailure::Snapshot(error)) => return Err(errors::snapshot(py, error)),
            Err(ReadFailure::Closed) => return Err(errors::closed(py)),
        };
        Ok(SnapshotFrame {
            envelope_version: frame.envelope_version,
            resource_epoch: frame.resource_epoch,
            producer_incarnation: frame.producer_incarnation,
            generation: frame.generation,
            applied_event_sequence: frame.applied_event_sequence,
            published_at_unix_nanos: frame.published_at_unix_nanos,
            payload: frame.payload,
        })
    }

    fn close(&self, py: Python<'_>) -> PyResult<()> {
        self.ensure_process(py)?;
        let mut guard = self.reader.lock().map_err(|_| errors::internal_panic(py))?;
        guard.take();
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

impl SnapshotReader {
    fn ensure_process(&self, py: Python<'_>) -> PyResult<()> {
        if std::process::id() != self.creator_pid {
            return Err(errors::forked(py));
        }
        Ok(())
    }
}
