use kairos_indexed_view::StoreError;
use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;

create_exception!(_native_transport, NativeTransportError, PyException);
create_exception!(_native_transport, ConfigurationError, NativeTransportError);
create_exception!(
    _native_transport,
    DriverUnavailableError,
    NativeTransportError
);
create_exception!(_native_transport, TimeoutError, NativeTransportError);
create_exception!(
    _native_transport,
    PayloadTooLargeError,
    NativeTransportError
);
create_exception!(
    _native_transport,
    ResourceChangedError,
    NativeTransportError
);
create_exception!(_native_transport, ClosedError, NativeTransportError);
create_exception!(_native_transport, ForkedProcessError, NativeTransportError);
create_exception!(_native_transport, WorkerExitedError, NativeTransportError);
create_exception!(_native_transport, QueueOverflowError, NativeTransportError);
create_exception!(_native_transport, IndexedViewError, NativeTransportError);
create_exception!(_native_transport, CorruptIndexedViewError, IndexedViewError);

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    let py = module.py();
    for (name, exception) in [
        (
            "NativeTransportError",
            py.get_type::<NativeTransportError>(),
        ),
        ("ConfigurationError", py.get_type::<ConfigurationError>()),
        (
            "DriverUnavailableError",
            py.get_type::<DriverUnavailableError>(),
        ),
        ("TimeoutError", py.get_type::<TimeoutError>()),
        (
            "PayloadTooLargeError",
            py.get_type::<PayloadTooLargeError>(),
        ),
        (
            "ResourceChangedError",
            py.get_type::<ResourceChangedError>(),
        ),
        ("ClosedError", py.get_type::<ClosedError>()),
        ("ForkedProcessError", py.get_type::<ForkedProcessError>()),
        ("WorkerExitedError", py.get_type::<WorkerExitedError>()),
        ("QueueOverflowError", py.get_type::<QueueOverflowError>()),
        ("IndexedViewError", py.get_type::<IndexedViewError>()),
        (
            "CorruptIndexedViewError",
            py.get_type::<CorruptIndexedViewError>(),
        ),
    ] {
        module.add(name, exception)?;
    }
    Ok(())
}

pub fn indexed_view(py: Python<'_>, error: StoreError) -> PyErr {
    let (py_error, code) = match error {
        StoreError::InvalidIdentity(_)
        | StoreError::InvalidSchema(_)
        | StoreError::InvalidPath(_)
        | StoreError::UnknownDatabase(_) => (
            PyErr::new::<ConfigurationError, _>(error.to_string()),
            "indexed_view_configuration",
        ),
        StoreError::MetadataMismatch(_) => (
            PyErr::new::<ResourceChangedError, _>(error.to_string()),
            "indexed_view_metadata_mismatch",
        ),
        StoreError::CorruptMetadata(_) => (
            PyErr::new::<CorruptIndexedViewError, _>(error.to_string()),
            "corrupt_indexed_view",
        ),
        StoreError::Storage(_) | StoreError::Io(_) => (
            PyErr::new::<IndexedViewError, _>(error.to_string()),
            "indexed_view_storage",
        ),
    };
    with_code(py, py_error, code)
}

pub fn closed(py: Python<'_>) -> PyErr {
    with_code(
        py,
        PyErr::new::<ClosedError, _>("snapshot reader is closed"),
        "closed",
    )
}

pub fn forked(py: Python<'_>) -> PyErr {
    with_code(
        py,
        PyErr::new::<ForkedProcessError, _>("native transport object belongs to another process"),
        "forked_process",
    )
}

pub fn internal_panic(py: Python<'_>) -> PyErr {
    with_code(
        py,
        PyErr::new::<NativeTransportError, _>("native transport operation panicked"),
        "internal_panic",
    )
}

pub fn with_code(py: Python<'_>, error: PyErr, code: &str) -> PyErr {
    let _ = error.value(py).setattr("code", code);
    error
}
