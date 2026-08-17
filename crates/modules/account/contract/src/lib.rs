//! v2 public cross-process contract for Account.
//!
//! The contract crate owns transport framing, generated-root selection and
//! encode/decode capabilities. It does not expose provider payloads or the
//! Account service's mutable state model.

pub mod control;
pub mod encode;
pub mod error;
pub mod event;
pub mod transport;
pub mod view;

pub use control::{AccountContractClient, DecimalValue, Health, SimulatedSettlement};
pub use control::{AccountControlClient, AccountControlResponse};
pub use encode::{
    event_metadata, view_metadata, BalanceEncoder, EncodeContext, ObservedOrderEncoder,
    PositionEncoder, StatusEncoder, ValuationEncoder,
};
pub use error::{ContractError, ContractResult};
pub use event::{AccountEvent, AccountEventFrame, AccountEventStream};
pub use transport::AccountUdsTransport;
pub use view::{
    decode_account_current, AccountViewKey, AccountViewKind, AccountViewPublisher, ViewFrame,
    ViewMetadata,
};

use std::path::PathBuf;

/// Unified public entry point. Control, events and views remain separate
/// capabilities underneath this facade.
pub struct AccountClient {
    control: AccountControlClient,
    control_socket: PathBuf,
    view_root: PathBuf,
    aeron_dir: Option<String>,
    aeron_channel: String,
    event_stream_id: i32,
}

pub struct AccountEndpoint {
    pub control_socket: PathBuf,
    pub view_root: PathBuf,
    pub aeron_dir: Option<String>,
    pub aeron_channel: String,
    pub event_stream_id: i32,
}

impl AccountClient {
    pub fn connect(endpoint: AccountEndpoint) -> Self {
        Self {
            control: AccountControlClient::connect(endpoint.control_socket.clone()),
            control_socket: endpoint.control_socket,
            view_root: endpoint.view_root,
            aeron_dir: endpoint.aeron_dir,
            aeron_channel: endpoint.aeron_channel,
            event_stream_id: endpoint.event_stream_id,
        }
    }

    pub fn control(&self) -> &AccountControlClient {
        &self.control
    }

    pub fn events(&self, capacity: usize) -> ContractResult<AccountEventStream> {
        AccountEventStream::connect(
            self.aeron_dir.as_deref(),
            &self.aeron_channel,
            self.event_stream_id,
            capacity,
        )
    }

    pub fn view(&self, key: AccountViewKey) -> ContractResult<view::AccountViewReader> {
        view::AccountViewReader::open(&self.view_root, key)
    }

    pub fn control_socket(&self) -> &std::path::Path {
        &self.control_socket
    }
}
