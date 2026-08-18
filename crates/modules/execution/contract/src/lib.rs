//! v2 public cross-process contract for Execution.

pub mod control;
pub mod encode;
pub mod error;
pub mod event;
pub mod transport;
pub mod view;

pub use control::{
    CancelOrderRequest, CommandEnvelope, ExecutionCommandStatus, ExecutionControlClient,
    ExecutionControlError, ExecutionControlResponse, ExecutionHealthResponse,
    ExecutionReconcileResponse, ExecutionRestRequest, ExecutionRestResponse,
    ExecutionRouteCandidateResponse, ExecutionRouteHealth, ExecutionRoutesQuery,
    ExecutionRoutesResponse, ReconcileExecutionRequest, ReplaceOrderRequest, SubmitIntentRequest,
};
pub use encode::{event_metadata, view_metadata, EncodeContext};
pub use error::{ContractError, ContractResult};
pub use event::{
    ExecutionEvent, ExecutionEventFrame, ExecutionEventPublisher, ExecutionEventStream,
};
pub use kairos_transport::AeronEndpoint;
pub use view::{
    execution_view_path, ExecutionViewKey, ExecutionViewKind, ExecutionViewPublisher,
    ExecutionViewReader, ViewFrame, ViewMetadata,
};

use std::path::PathBuf;

pub struct ExecutionClient {
    control: ExecutionControlClient,
    control_socket: PathBuf,
    view_root: PathBuf,
    events: AeronEndpoint,
}

pub struct ExecutionEndpoint {
    pub control_socket: PathBuf,
    pub view_root: PathBuf,
    pub events: AeronEndpoint,
}

impl ExecutionClient {
    pub fn connect(endpoint: ExecutionEndpoint) -> Self {
        Self {
            control: ExecutionControlClient::connect(endpoint.control_socket.clone()),
            control_socket: endpoint.control_socket,
            view_root: endpoint.view_root,
            events: endpoint.events,
        }
    }
    pub fn control(&self) -> &ExecutionControlClient {
        &self.control
    }
    pub fn events(&self, capacity: usize) -> ContractResult<ExecutionEventStream> {
        ExecutionEventStream::connect(&self.events, capacity)
    }
    pub fn view(&self, key: ExecutionViewKey) -> ContractResult<view::ExecutionViewReader> {
        view::ExecutionViewReader::open(&self.view_root, key)
    }
    pub fn control_socket(&self) -> &std::path::Path {
        &self.control_socket
    }
}
