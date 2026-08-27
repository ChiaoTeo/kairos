use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use kairos_indexed_view::{
    EnvironmentOptions, IndexedViewIdentity, RebuildState, SchemaDescriptor, SchemaSet,
};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};

use crate::errors;

enum ReadFailure {
    Closed,
    Store(kairos_indexed_view::StoreError),
}

#[pyclass(frozen, module = "kairospy._native_transport")]
pub struct IndexedViewMetadata {
    #[pyo3(get)]
    pub format_version: u32,
    #[pyo3(get)]
    pub resource_epoch: u64,
    #[pyo3(get)]
    pub producer_incarnation: u64,
    #[pyo3(get)]
    pub applied_event_sequence: u64,
    #[pyo3(get)]
    pub committed_at_unix_nanos: u64,
    #[pyo3(get)]
    pub rebuild_state: String,
    #[pyo3(get)]
    pub diagnostic_code: Option<String>,
}

#[pyclass(module = "kairospy._native_transport")]
pub struct IndexedViewReader {
    creator_pid: u32,
    reader: Arc<Mutex<Option<kairos_indexed_view::IndexedViewReader>>>,
}

#[pymethods]
impl IndexedViewReader {
    #[new]
    #[pyo3(signature = (
        path,
        map_size,
        workspace_id,
        launch_id,
        instance_id,
        owner,
        publisher_resource_id,
        resource_epoch,
        schemas,
        producer_incarnation=1,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        py: Python<'_>,
        path: PathBuf,
        map_size: usize,
        workspace_id: String,
        launch_id: Option<String>,
        instance_id: Option<String>,
        owner: String,
        publisher_resource_id: String,
        resource_epoch: u64,
        schemas: Vec<(String, u32, String, u32)>,
        producer_incarnation: u64,
    ) -> PyResult<Self> {
        let options = EnvironmentOptions::new(path, map_size)
            .map_err(|error| errors::indexed_view(py, error))?;
        let schemas = schemas
            .into_iter()
            .map(|(database, key_version, value_schema, value_version)| {
                SchemaDescriptor::new(database, key_version, value_schema, value_version)
            })
            .collect::<Result<Vec<_>, _>>()
            .and_then(SchemaSet::new)
            .map_err(|error| errors::indexed_view(py, error))?;
        let identity = IndexedViewIdentity::new(
            workspace_id,
            launch_id,
            instance_id,
            owner,
            publisher_resource_id,
            resource_epoch,
            producer_incarnation,
            schemas,
        )
        .map_err(|error| errors::indexed_view(py, error))?;
        let opened = py
            .detach(|| kairos_indexed_view::IndexedViewReader::open(&options, identity))
            .map_err(|error| errors::indexed_view(py, error))?;
        Ok(Self {
            creator_pid: std::process::id(),
            reader: Arc::new(Mutex::new(Some(opened))),
        })
    }

    fn metadata(&self, py: Python<'_>) -> PyResult<IndexedViewMetadata> {
        let snapshot = self.with_reader(py, |reader| reader.metadata())?;
        Ok(metadata_value(snapshot))
    }

    fn get(&self, py: Python<'_>, database: String, key: Vec<u8>) -> PyResult<Option<Py<PyBytes>>> {
        self.with_reader(py, move |reader| {
            reader.with_value(&database, &key, |value| {
                Python::attach(|py| value.map(|value| PyBytes::new(py, value).unbind()))
            })
        })
    }

    fn value_snapshot(
        &self,
        py: Python<'_>,
        database: String,
        key: Vec<u8>,
    ) -> PyResult<(IndexedViewMetadata, Option<Py<PyBytes>>)> {
        self.with_reader(py, move |reader| {
            reader.with_value_snapshot(&database, &key, |metadata, value| {
                Python::attach(|py| {
                    (
                        metadata_value(metadata),
                        value.map(|value| PyBytes::new(py, value).unbind()),
                    )
                })
            })
        })
    }

    fn prefix(
        &self,
        py: Python<'_>,
        database: String,
        prefix: Vec<u8>,
        limit: usize,
    ) -> PyResult<Vec<(Py<PyBytes>, Py<PyBytes>)>> {
        self.with_reader(py, move |reader| {
            reader.map_prefix(&database, &prefix, limit, |key, value| {
                Python::attach(|py| {
                    (
                        PyBytes::new(py, key).unbind(),
                        PyBytes::new(py, value).unbind(),
                    )
                })
            })
        })
    }

    fn snapshot(
        &self,
        py: Python<'_>,
        requests: Vec<(String, Vec<u8>, usize)>,
    ) -> PyResult<(IndexedViewMetadata, Py<PyDict>)> {
        let snapshot = self.with_reader(py, move |reader| {
            let borrowed = requests
                .iter()
                .map(
                    |(database, prefix, limit)| kairos_indexed_view::PrefixRequest {
                        database,
                        prefix,
                        limit: *limit,
                    },
                )
                .collect::<Vec<_>>();
            reader.snapshot(&borrowed)
        })?;
        let rows = PyDict::new(py);
        for (database, values) in snapshot.rows {
            let values = values
                .into_iter()
                .map(|(key, value)| {
                    (
                        PyBytes::new(py, &key).unbind(),
                        PyBytes::new(py, &value).unbind(),
                    )
                })
                .collect::<Vec<_>>();
            rows.set_item(database, values)?;
        }
        Ok((metadata_value(snapshot.metadata), rows.unbind()))
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

fn metadata_value(snapshot: kairos_indexed_view::MetadataSnapshot) -> IndexedViewMetadata {
    let (rebuild_state, diagnostic_code) = match snapshot.rebuild_state {
        RebuildState::Building => ("building".to_owned(), None),
        RebuildState::Ready => ("ready".to_owned(), None),
        RebuildState::Failed { diagnostic_code } => ("failed".to_owned(), Some(diagnostic_code)),
    };
    IndexedViewMetadata {
        format_version: snapshot.format_version,
        resource_epoch: snapshot.resource_epoch,
        producer_incarnation: snapshot.producer_incarnation,
        applied_event_sequence: snapshot.applied_event_sequence,
        committed_at_unix_nanos: snapshot.committed_at_unix_nanos,
        rebuild_state,
        diagnostic_code,
    }
}

impl IndexedViewReader {
    fn with_reader<T: Send>(
        &self,
        py: Python<'_>,
        operation: impl FnOnce(
            &kairos_indexed_view::IndexedViewReader,
        ) -> Result<T, kairos_indexed_view::StoreError>
        + Send,
    ) -> PyResult<T> {
        self.ensure_process(py)?;
        let reader = Arc::clone(&self.reader);
        let result = py.detach(move || {
            let guard = reader.lock().map_err(|_| ReadFailure::Closed)?;
            let reader = guard.as_ref().ok_or(ReadFailure::Closed)?;
            operation(reader).map_err(ReadFailure::Store)
        });
        match result {
            Ok(value) => Ok(value),
            Err(ReadFailure::Closed) => Err(errors::closed(py)),
            Err(ReadFailure::Store(error)) => Err(errors::indexed_view(py, error)),
        }
    }

    fn ensure_process(&self, py: Python<'_>) -> PyResult<()> {
        if std::process::id() != self.creator_pid {
            return Err(errors::forked(py));
        }
        Ok(())
    }
}
