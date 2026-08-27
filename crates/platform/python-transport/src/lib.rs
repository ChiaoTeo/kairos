mod aeron;
mod errors;

use pyo3::prelude::*;

#[pyclass(frozen, module = "kairospy._native_transport")]
struct NativeBuildInfo {
    #[pyo3(get)]
    api_version: u32,
    #[pyo3(get)]
    transport_fingerprint: String,
    #[pyo3(get)]
    package_version: String,
}

#[pyfunction]
fn build_info() -> NativeBuildInfo {
    NativeBuildInfo {
        api_version: 1,
        transport_fingerprint: kairos_transport::TRANSPORT_FINGERPRINT.to_owned(),
        package_version: env!("CARGO_PKG_VERSION").to_owned(),
    }
}

#[pymodule]
fn _native_transport(module: &Bound<'_, PyModule>) -> PyResult<()> {
    errors::register(module)?;
    module.add_class::<NativeBuildInfo>()?;
    module.add_class::<aeron::StreamSpec>()?;
    module.add_class::<aeron::PyAeronSubscription>()?;
    module.add_function(wrap_pyfunction!(build_info, module)?)?;
    Ok(())
}
