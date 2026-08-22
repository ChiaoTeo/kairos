mod app;
mod cli;
mod command;
mod conflux;
mod connected;
mod error;
mod result;

pub use app::{AccountApplication, AccountRuntimeMode};
pub use cli::{
    AccountBalanceItem, AccountBalancesResult, AccountCredentialProbeRequest,
    AccountEarnHoldingItem, AccountEarnHoldingsResult, AccountEarnRewardItem, AccountFeeComponent,
    AccountFeeDiscount, AccountFeesResult, AccountListItem, AccountListResult,
    AccountOpenOrderItem, AccountOpenOrdersResult, AccountOverviewCommercial,
    AccountOverviewConnection, AccountOverviewFacts, AccountOverviewHealth,
    AccountOverviewIdentity, AccountOverviewPermissions, AccountOverviewProfile,
    AccountOverviewResult, AccountPositionItem, AccountPositionsResult,
    AccountProviderConnectionArgs, AccountQueryCompleteness, AccountQueryError,
    AccountSegmentProfileItem, AccountSegmentQueryOutcome, BindCredentialRequest,
    CliAccountApplication, ConnectAccountProviderRequest, ConnectAccountRequest,
    CreateCredentialRequest, ModifyAccountRequest, RegisterAccountRequest, SimulateAccountRequest,
};
pub use command::{MarkToMarket, ReconcileAccount, RefreshAccount};
pub use connected::ConnectedAccountApplication;
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
