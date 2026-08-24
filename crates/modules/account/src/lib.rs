//! Account business boundary.
//!
//! Account is the single owner of balances, positions and account freshness.
//! Integration connections are injected through the account application boundary;
//! snapshot publication belongs to the account process.

pub mod application;
pub mod composition;
pub mod domain;
mod services;

pub use application::{
    AccountAdapterKind, AccountApplication, AccountBalanceItem, AccountBalancesResult,
    AccountBrowseResult, AccountCredentialProbeRequest, AccountCurrentView, AccountEarnHoldingItem,
    AccountEarnHoldingsResult, AccountError, AccountEvent, AccountFeeComponent, AccountFeeDiscount,
    AccountFeesResult, AccountListItem, AccountListResult, AccountLocalSnapshotResult,
    AccountModifyResult, AccountObservedFill, AccountOpenOrderItem, AccountOpenOrdersResult,
    AccountOverviewResult, AccountPositionItem, AccountPositionsResult,
    AccountProviderConnectionArgs, AccountQueryCompleteness, AccountQueryError,
    AccountRecordResult, AccountRemoveResult, AccountRpcService, AccountRuntimeMode,
    AccountSimulateResult, BindCredentialRequest, CliAccountApplication,
    ConnectAccountProviderRequest, ConnectAccountRequest, ConnectedAccountApplication,
    ConnectedAccountCurrentResult, ConnectedAccountOutput, CreateCredentialRequest, MarkToMarket,
    ModifyAccountRequest, ReconcileAccount, RefreshAccount, RegisterAccountRequest,
    SimulateAccountRequest, StoredCredentialResult,
};
