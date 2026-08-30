mod app;
mod command;
mod connected;
mod error;
mod process;
mod result;

pub use app::{AccountApplication, AccountRuntimeMode};
pub use command::{MarkToMarket, ReconcileAccount, RefreshAccount};
pub use connected::{
    AccountCurrentResult as ConnectedAccountCurrentResult, ConnectedAccountApplication,
    ConnectedAccountOutput,
};
pub use error::AccountError;
pub use result::{
    AccountBusinessChange, AccountBusinessEvent, AccountCurrentView, AccountDifference,
    AccountFactProvenance, AccountRefreshIssue, AccountRefreshReport, AccountSegmentCompleteness,
    AccountSegmentFreshness, AccountSegmentSyncLifecycle, AccountSegmentSyncMode,
    AccountSegmentView,
};

// These are the package-owned request, result, and state-view types accepted
// by Account use cases.  The domain module itself stays private so callers do
// not acquire a second entry point into Account behavior.
pub use crate::domain::{
    Account, AccountDomainError, AccountEvent, AccountFill, AccountModel, AccountObservedFill,
    AccountSegment, AccountSnapshot, AccountState, AccountStatus, Accounts, ApplyOutcome, AssetId,
    Balance, EarnAccruedReward, EarnHolding, EarnHoldingLiquidity, EarnHoldingState,
    EarnHoldingsSnapshot, ExternalAccountIdentity, FillId, InstrumentId, MarginMode, Money,
    OpenOrder, OrderSide, Position, PositionMode, SegmentKey, SignedQuantity,
    SimulatedCapitalMutation, SimulatedCapitalMutationKind, SnapshotKind,
};

kairos_account_contract::account_control_rpc_conflux_actor! {
    pub trait AccountRpcActor;
    service AccountRpcService;
}
