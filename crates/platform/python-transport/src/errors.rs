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
    ] {
        module.add(name, exception)?;
    }
    Ok(())
}
