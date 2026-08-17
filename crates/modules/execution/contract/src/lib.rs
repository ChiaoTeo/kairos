//! v2 public cross-process contract for Execution.

pub mod control;
pub mod encode;
pub mod error;
pub mod event;
pub mod transport;
pub mod view;

pub use control::{ExecutionControlClient, ExecutionControlResponse};
pub use encode::{event_metadata, view_metadata, EncodeContext};
pub use error::{ContractError, ContractResult};
pub use event::{ExecutionEvent, ExecutionEventFrame, ExecutionEventStream};
pub use view::{
    ExecutionViewKey, ExecutionViewKind, ExecutionViewPublisher, ExecutionViewReader, ViewFrame,
    ViewMetadata,
};

use std::path::PathBuf;

pub struct ExecutionClient {
    control: ExecutionControlClient,
    control_socket: PathBuf,
    view_root: PathBuf,
    aeron_dir: Option<String>,
    aeron_channel: String,
    event_stream_id: i32,
}

pub struct ExecutionEndpoint {
    pub control_socket: PathBuf,
    pub view_root: PathBuf,
    pub aeron_dir: Option<String>,
    pub aeron_channel: String,
    pub event_stream_id: i32,
}

impl ExecutionClient {
    pub fn connect(endpoint: ExecutionEndpoint) -> Self {
        Self {
            control: ExecutionControlClient::connect(endpoint.control_socket.clone()),
            control_socket: endpoint.control_socket,
            view_root: endpoint.view_root,
            aeron_dir: endpoint.aeron_dir,
            aeron_channel: endpoint.aeron_channel,
            event_stream_id: endpoint.event_stream_id,
        }
    }
    pub fn control(&self) -> &ExecutionControlClient {
        &self.control
    }
    pub fn events(&self, capacity: usize) -> ContractResult<ExecutionEventStream> {
        ExecutionEventStream::connect(
            self.aeron_dir.as_deref(),
            &self.aeron_channel,
            self.event_stream_id,
            capacity,
        )
    }
    pub fn view(&self, key: ExecutionViewKey) -> ContractResult<view::ExecutionViewReader> {
        view::ExecutionViewReader::open(&self.view_root, key)
    }
    pub fn control_socket(&self) -> &std::path::Path {
        &self.control_socket
    }
}
