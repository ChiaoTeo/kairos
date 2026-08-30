//! Concrete Account assembly and process-owned contract publishers.

use std::path::PathBuf;

use kairos_primitives::runtime::InstanceIdentity;

use crate::application::ConnectedAccountApplication;

pub mod account;
pub mod cli;
pub mod registry;

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

pub use crate::services::publication::empty_snapshot;

/// Select and install the concrete runtime client used by the connected CLI.
pub fn connect_account_application(
    socket: PathBuf,
    view_root: Option<PathBuf>,
    identity: InstanceIdentity,
) -> Result<ConnectedAccountApplication, Box<dyn std::error::Error>> {
    let mut system = kairos_conflux::ConfluxSystem::new();
    system.install_account_connection("account", socket, view_root)?;
    let client = system
        .account_client("account")
        .ok_or("managed Account client is missing: account")?;
    Ok(ConnectedAccountApplication::new(client, identity))
}
