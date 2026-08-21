mod command;
mod conflux;
mod error;
mod result;
mod service;

pub use command::{MarkToMarket, ReconcileAccount, RefreshAccount};
pub use error::AccountError;
pub use result::{
    AccountBusinessChange, AccountBusinessEvent, AccountCurrentView, AccountDifference,
    AccountFactProvenance, AccountRefreshIssue, AccountRefreshReport, AccountSegmentCompleteness,
    AccountSegmentFreshness, AccountSegmentSyncLifecycle, AccountSegmentSyncMode,
    AccountSegmentView,
};
pub use service::{AccountApplication, AccountRuntimeMode};

pub use crate::domain::{AccountEvent, AccountObservedFill};

kairos_account_contract::account_control_rpc_conflux_actor! {
    pub trait AccountRpcActor;
    service AccountRpcService;
}
