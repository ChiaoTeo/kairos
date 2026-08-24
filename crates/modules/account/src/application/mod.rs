mod app;
mod cli;
mod command;
mod conflux;
mod connected;
mod error;
mod result;

pub use app::{AccountApplication, AccountRuntimeMode};
pub use cli::{
    AccountAdapterKind, AccountBalanceItem, AccountBalancesResult, AccountBrowseResult,
    AccountCredentialProbeRequest, AccountEarnHoldingItem, AccountEarnHoldingsResult,
    AccountEarnRewardItem, AccountFeeComponent, AccountFeeDiscount, AccountFeesResult,
    AccountListItem, AccountListResult, AccountLocalSnapshotResult, AccountModifyResult,
    AccountOpenOrderItem, AccountOpenOrdersResult, AccountOverviewCommercial,
    AccountOverviewConnection, AccountOverviewFacts, AccountOverviewHealth,
    AccountOverviewIdentity, AccountOverviewPermissions, AccountOverviewProfile,
    AccountOverviewResult, AccountPositionItem, AccountPositionsResult,
    AccountProviderConnectionArgs, AccountQueryCompleteness, AccountQueryError,
    AccountRecordResult, AccountRemoveResult, AccountSegmentProfileItem,
    AccountSegmentQueryOutcome, AccountSimulateResult, BindCredentialRequest,
    CliAccountApplication, ConnectAccountProviderRequest, ConnectAccountRequest,
    CreateCredentialRequest, ModifyAccountRequest, RegisterAccountRequest, SimulateAccountRequest,
    StoredCredentialResult,
};
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

pub use crate::domain::{AccountEvent, AccountObservedFill};

kairos_account_contract::account_control_rpc_conflux_actor! {
    pub trait AccountRpcActor;
    service AccountRpcService;
}
