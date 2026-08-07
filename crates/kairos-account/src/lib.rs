//! Account business boundary.
//!
//! Account is the single owner of balances, positions and account freshness.
//! Provider connections are injected through the account application boundary;
//! snapshot publication belongs to the account process.

pub mod application;
pub mod composition;
pub mod domain;
mod services;

pub use application::account::AccountsSnapshot;
pub use application::account::{
    AccountApplication, AccountDataQuery, AccountError, AccountQuery, ReconcileAccount,
    RefreshAccount,
};
pub use domain::{Account, AccountState, Balance, Position};
pub use services::process::AccountProcess;
