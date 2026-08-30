//! Account business boundary.
//!
//! Account is the single owner of balances, positions and account freshness.
//! Integration connections are injected through the account application boundary;
//! snapshot publication belongs to the account process.

pub mod application;
pub mod composition;
mod domain;
mod services;

pub use application::{
    Account, AccountApplication, AccountCurrentView, AccountDomainError, AccountError,
    AccountEvent, AccountFill, AccountModel, AccountObservedFill, AccountRpcService,
    AccountRuntimeMode, AccountSegment, AccountSnapshot, AccountState, AccountStatus, Accounts,
    ApplyOutcome, AssetId, Balance, ConnectedAccountApplication, ConnectedAccountCurrentResult,
    ConnectedAccountOutput, EarnAccruedReward, EarnHolding, EarnHoldingLiquidity, EarnHoldingState,
    EarnHoldingsSnapshot, ExternalAccountIdentity, FillId, InstrumentId, MarginMode, MarkToMarket,
    Money, OpenOrder, OrderSide, Position, PositionMode, ReconcileAccount, RefreshAccount,
    SegmentKey, SignedQuantity, SimulatedCapitalMutation, SimulatedCapitalMutationKind,
    SnapshotKind,
};
pub use composition::{
    AccountAdapterKind, AccountBalanceItem, AccountBalancesResult, AccountBrowseResult,
    AccountCredentialProbeRequest, AccountEarnHoldingItem, AccountEarnHoldingsResult,
    AccountFeeComponent, AccountFeeDiscount, AccountFeesResult, AccountListItem, AccountListResult,
    AccountLocalSnapshotResult, AccountModifyResult, AccountOpenOrderItem, AccountOpenOrdersResult,
    AccountOverviewResult, AccountPositionItem, AccountPositionsResult,
    AccountProviderConnectionArgs, AccountQueryCompleteness, AccountQueryError,
    AccountRecordResult, AccountRemoveResult, AccountSimulateResult, BindCredentialRequest,
    CliAccountApplication, ConnectAccountProviderRequest, ConnectAccountRequest,
    CreateCredentialRequest, ModifyAccountRequest, RegisterAccountRequest, SimulateAccountRequest,
    StoredCredentialResult,
};
