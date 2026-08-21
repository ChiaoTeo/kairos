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
pub use encode::{
    BalanceEncoder, EncodeContext, ObservedOrderEncoder, PositionEncoder, StatusEncoder,
    ValuationEncoder, event_metadata, view_metadata,
};
pub use error::{ContractError, ContractResult};
pub use event::{AccountEvent, AccountEventFrame, AccountEventPublisher, AccountEventStream};
pub type AccountConnection = kairos_protocol::ContractClient;

pub use kairos_transport::AeronEndpoint;
use kairos_transport::SnapshotEnvelopeMetadata;
pub use view::{
    AccountViewKey, AccountViewKind, AccountViewPublisher, ViewFrame, ViewMetadata,
    account_view_path, decode_account_current,
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

    pub fn account_current(
        &self,
        account_runtime_id: impl Into<String>,
        account_id: impl Into<String>,
    ) -> ContractResult<AccountCurrent> {
        AccountCurrent::open(
            self.require_view_root()?,
            AccountViewKey::new(account_runtime_id, account_id, AccountViewKind::Current)?,
        )
    }

    pub fn observed_orders(
        &self,
        account_runtime_id: impl Into<String>,
        account_id: impl Into<String>,
    ) -> ContractResult<ObservedOrders> {
        ObservedOrders::open(
            self.require_view_root()?,
            AccountViewKey::new(
                account_runtime_id,
                account_id,
                AccountViewKind::ObservedOrders,
            )?,
        )
    }

    fn require_view_root(&self) -> ContractResult<&Path> {
        self.inner
            .require_view_root()
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}

pub struct AccountCurrent {
    reader: view::AccountViewReader,
}

impl AccountCurrent {
    fn open(root: &Path, key: AccountViewKey) -> ContractResult<Self> {
        Ok(Self {
            reader: view::AccountViewReader::open(root, key)?,
        })
    }

    pub fn read(&self) -> ContractResult<AccountCurrentSnapshot> {
        Ok(AccountCurrentSnapshot {
            frame: self.reader.read()?,
        })
    }

    pub fn key(&self) -> &AccountViewKey {
        self.reader.key()
    }
}

pub struct AccountCurrentSnapshot {
    frame: ViewFrame,
}

impl AccountCurrentSnapshot {
    pub fn generation(&self) -> u64 {
        self.frame.generation()
    }

    pub fn envelope_metadata(&self) -> SnapshotEnvelopeMetadata {
        self.frame.envelope_metadata()
    }

    pub fn view(&self) -> ContractResult<view::AccountCurrentView<'_>> {
        self.frame.account_current()
    }
}

pub struct ObservedOrders {
    reader: view::AccountViewReader,
}

impl ObservedOrders {
    fn open(root: &Path, key: AccountViewKey) -> ContractResult<Self> {
        Ok(Self {
            reader: view::AccountViewReader::open(root, key)?,
        })
    }

    pub fn read(&self) -> ContractResult<ObservedOrdersSnapshot> {
        Ok(ObservedOrdersSnapshot {
            frame: self.reader.read()?,
        })
    }

    pub fn key(&self) -> &AccountViewKey {
        self.reader.key()
    }
}

pub struct ObservedOrdersSnapshot {
    frame: ViewFrame,
}

impl ObservedOrdersSnapshot {
    pub fn generation(&self) -> u64 {
        self.frame.generation()
    }

    pub fn envelope_metadata(&self) -> SnapshotEnvelopeMetadata {
        self.frame.envelope_metadata()
    }

    pub fn view(&self) -> ContractResult<view::ObservedOrdersCurrentView<'_>> {
        self.frame.observed_orders()
    }
}
