//! v2 public cross-process contract for Account.
//!
//! The contract crate owns transport framing, generated-root selection and
//! encode/decode capabilities. It does not expose provider payloads or the
//! Account service's mutable state model.

extern crate self as kairos_account_contract;

use std::path::Path;

pub mod control;
pub mod encode;
pub mod error;
pub mod event;
pub mod view;

pub use control::{
    AccountCommandOutcome, AccountCommandStatus, AccountControlError, AccountControlRpcClient,
    AccountControlRpcServer, AccountHealthStatus, AccountRefreshResponse, AccountRefreshStatus,
    AccountSegmentsRequest, AdvanceAccountTimeRequest, AdvanceAccountTimeResponse, DecimalValue,
    Health, MarkToMarketRequest, SimulatedCapitalMutation, SimulatedCapitalMutationKind,
    SimulatedCapitalMutationQuery, SimulatedCapitalMutationStatus,
    SimulatedCapitalMutationStatusResponse, SimulatedSettlement,
};
pub use encode::{EncodeContext, event_metadata};
pub use error::{ContractError, ContractResult};
pub use event::*;
pub type AccountConnection = kairos_protocol::ContractClient;

pub use kairos_transport::AeronEndpoint;
pub const ACCOUNT_EVENT_STREAM_ID: i32 = kairos_transport::stream_ids::ACCOUNT_EVENTS;
pub const DEFAULT_AERON_CHANNEL: &str = kairos_transport::DEFAULT_CHANNEL;
pub const CONTRACT_FINGERPRINT: &str = "kairos.account.contract.v2";
pub use view::{
    ACCOUNT_BALANCES_DATABASE, ACCOUNT_COLLATERAL_DATABASE, ACCOUNT_EARN_HOLDINGS_DATABASE,
    ACCOUNT_MAP_SIZE, ACCOUNT_OBSERVED_ORDERS_DATABASE, ACCOUNT_POSITIONS_DATABASE,
    ACCOUNT_RESOURCE_EPOCH, ACCOUNT_SEGMENTS_DATABASE, ACCOUNT_VALUATIONS_DATABASE,
    AccountIndexedSnapshot, AccountIndexedView, AccountIndexedViewValue,
    AccountIndexedViewValueRef, account_indexed_environment_path, account_indexed_identity,
    account_indexed_key, account_indexed_schema_set,
};

/// Unified public entry point. Control, events and views remain separate
/// capabilities underneath this facade.
#[derive(Clone)]
pub struct AccountClient {
    inner: kairos_protocol::ContractClient,
}

impl AccountClient {
    pub fn connect(connection: AccountConnection) -> Self {
        Self { inner: connection }
    }

    pub fn control(&self) -> impl AccountControlRpcClient + '_ {
        self.inner.control()
    }

    pub fn events(&self, capacity: usize) -> ContractResult<AccountEventStream> {
        AccountEventStream::connect(
            self.inner
                .require_aeron_endpoint()
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            capacity,
        )
    }

    pub fn indexed_current(
        &self,
        identity: &kairos_primitives::runtime::InstanceIdentity,
        account_id: kairos_primitives::account::AccountId,
    ) -> ContractResult<AccountIndexedView> {
        AccountIndexedView::open(self.require_view_root()?, identity, account_id)
    }

    fn require_view_root(&self) -> ContractResult<&Path> {
        self.inner
            .require_view_root()
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}
