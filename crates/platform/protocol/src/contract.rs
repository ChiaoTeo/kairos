use std::path::{Path, PathBuf};

use kairos_transport::AeronEndpoint;

/// Common endpoint holder for typed module contract clients.
///
/// Family-named contract clients such as `RiskClient` or `AccountClient` wrap
/// this type. Their names carry the business boundary; this type keeps the
/// protocol shape uniform.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContractClient {
    pub control: kairos_workspace::JsonRpcControlClient,
    pub view_root: Option<PathBuf>,
    pub aeron_endpoint: Option<AeronEndpoint>,
}

impl ContractClient {
    pub fn new(
        control_socket: impl Into<PathBuf>,
        view_root: Option<impl Into<PathBuf>>,
        aeron_endpoint: Option<AeronEndpoint>,
    ) -> Self {
        Self {
            control: kairos_workspace::JsonRpcControlClient::new(control_socket.into()),
            view_root: view_root.map(Into::into),
            aeron_endpoint,
        }
    }

    pub fn control_only(control_socket: impl Into<PathBuf>) -> Self {
        Self::new(control_socket, None::<PathBuf>, None)
    }

    pub fn connect(endpoint: Self) -> Self {
        endpoint
    }

    pub fn with_view_root(mut self, view_root: impl Into<PathBuf>) -> Self {
        self.view_root = Some(view_root.into());
        self
    }

    pub fn with_aeron_endpoint(mut self, aeron_endpoint: AeronEndpoint) -> Self {
        self.aeron_endpoint = Some(aeron_endpoint);
        self
    }

    pub fn control(&self) -> &kairos_workspace::JsonRpcControlClient {
        &self.control
    }

    pub fn control_socket_path(&self) -> &Path {
        self.control.socket_path()
    }

    pub fn require_view_root(&self) -> Result<&Path, MissingContractEndpoint> {
        self.view_root
            .as_deref()
            .ok_or(MissingContractEndpoint::ViewRoot)
    }

    pub fn require_aeron_endpoint(&self) -> Result<&AeronEndpoint, MissingContractEndpoint> {
        self.aeron_endpoint
            .as_ref()
            .ok_or(MissingContractEndpoint::AeronEndpoint)
    }
}

/// Distinguishes an owner operation rejection from transport or wire failure.
#[must_use]
pub trait ControlCallError: std::fmt::Display {
    fn is_operation_rejection(&self) -> bool;
}

impl ControlCallError for jsonrpsee::core::ClientError {
    fn is_operation_rejection(&self) -> bool {
        matches!(self, Self::Call(_))
    }
}

#[must_use]
pub fn is_control_rejection(error: &impl ControlCallError) -> bool {
    error.is_operation_rejection()
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum MissingContractEndpoint {
    #[error("contract client has no view root")]
    ViewRoot,
    #[error("contract client has no Aeron endpoint")]
    AeronEndpoint,
}

#[cfg(test)]
mod tests {
    use jsonrpsee::types::ErrorObjectOwned;

    use super::{ControlCallError, is_control_rejection};

    #[test]
    fn only_json_rpc_error_responses_are_operation_rejections() {
        let rejected = jsonrpsee::core::ClientError::Call(ErrorObjectOwned::owned(
            -32000, "rejected", None::<()>,
        ));
        assert!(is_control_rejection(&rejected));
        assert!(!jsonrpsee::core::ClientError::Custom("offline".into()).is_operation_rejection());
    }
}
