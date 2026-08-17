use kairos_transport::SnapshotError;
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
    SnapshotNotInitializedError,
    NativeTransportError
);
create_exception!(
    _native_transport,
    UnsupportedEnvelopeVersionError,
    NativeTransportError
);
create_exception!(
    _native_transport,
    CorruptSnapshotError,
    NativeTransportError
);
create_exception!(
    _native_transport,
    ConcurrentChangeError,
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
            "SnapshotNotInitializedError",
            py.get_type::<SnapshotNotInitializedError>(),
        ),
        (
            "UnsupportedEnvelopeVersionError",
            py.get_type::<UnsupportedEnvelopeVersionError>(),
        ),
        (
            "CorruptSnapshotError",
            py.get_type::<CorruptSnapshotError>(),
        ),
        (
            "ConcurrentChangeError",
            py.get_type::<ConcurrentChangeError>(),
        ),
        (
            "ResourceChangedError",
            py.get_type::<ResourceChangedError>(),
        ),
        ("ClosedError", py.get_type::<ClosedError>()),
        ("ForkedProcessError", py.get_type::<ForkedProcessError>()),
        ("WorkerExitedError", py.get_type::<WorkerExitedError>()),
        ("QueueOverflowError", py.get_type::<QueueOverflowError>()),
    ] {
        module.add(name, exception)?;
    }
    Ok(())
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

pub fn snapshot(py: Python<'_>, error: SnapshotError) -> PyErr {
    let code = error.code();
    let message = error.to_string();
    let py_error = match error {
        SnapshotError::Configuration(_) | SnapshotError::WriterLeaseHeld(_) => {
            PyErr::new::<ConfigurationError, _>(message)
        }
        SnapshotError::NotInitialized => PyErr::new::<SnapshotNotInitializedError, _>(message),
        SnapshotError::UnsupportedVersion(_) => {
            PyErr::new::<UnsupportedEnvelopeVersionError, _>(message)
        }
        SnapshotError::Corrupt(_) | SnapshotError::ChecksumMismatch { .. } => {
            PyErr::new::<CorruptSnapshotError, _>(message)
        }
        SnapshotError::PayloadTooLarge { .. } => PyErr::new::<PayloadTooLargeError, _>(message),
        SnapshotError::ConcurrentChange => PyErr::new::<ConcurrentChangeError, _>(message),
        SnapshotError::ResourceChanged => PyErr::new::<ResourceChangedError, _>(message),
        SnapshotError::Io(_) | SnapshotError::CommitOverflow => {
            PyErr::new::<NativeTransportError, _>(message)
        }
    };
    with_code(py, py_error, code)
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
