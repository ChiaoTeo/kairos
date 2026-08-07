//! Account business boundary.
//!
//! Account is the single owner of balances, positions and account freshness.
//! Provider connections are injected through the account application boundary;
//! snapshot publication belongs to the account process.

pub mod application;
pub mod composition;
pub mod domain;
mod services;

pub use application::AccountsSnapshot;
pub use application::{
    AccountApplication, AccountDataQuery, AccountError, AccountProcess, AccountQuery,
    ReconcileAccount, RefreshAccount,
};
pub use domain::{Account, AccountState, Balance, Position};
