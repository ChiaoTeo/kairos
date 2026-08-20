use kairos_primitives::account::{AccountId, SegmentKey};
use kairos_primitives::time::UnixNanos;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct AccountControlError {
    pub code: String,
    pub message: String,
    #[serde(default)]
    pub retryable: bool,
    #[serde(default)]
    pub details: std::collections::BTreeMap<String, serde_json::Value>,
}

/// Closed REST request set provided by one Account process.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum AccountRestRequest {
    Health,
    ApplySimulatedSettlement(super::SimulatedSettlement),
    ApplySimulatedCapitalMutation(super::SimulatedCapitalMutation),
    QuerySimulatedCapitalMutation(super::SimulatedCapitalMutationQuery),
    MarkToMarket(super::MarkToMarketRequest),
    AdvanceTime(super::AdvanceAccountTimeRequest),
    Refresh(AccountSegmentsRequest),
    Reconcile(AccountSegmentsRequest),
}

/// Closed REST response set. Each variant corresponds to exactly one request
/// variant, so the Conflux host never transports an untyped JSON result.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum AccountRestResponse {
    Health(Result<super::Health, AccountControlError>),
    ApplySimulatedSettlement(Result<AccountCommandStatus, AccountControlError>),
    ApplySimulatedCapitalMutation(Result<AccountCommandStatus, AccountControlError>),
    QuerySimulatedCapitalMutation(
        Result<super::SimulatedCapitalMutationStatusResponse, AccountControlError>,
    ),
    MarkToMarket(Result<AccountCommandStatus, AccountControlError>),
    AdvanceTime(Result<AdvanceAccountTimeResponse, AccountControlError>),
    Refresh(Result<AccountRefreshResponse, AccountControlError>),
    Reconcile(Result<AccountRefreshResponse, AccountControlError>),
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountSegmentsRequest {
    #[serde(default)]
    pub segments: Vec<SegmentKey>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AccountCommandOutcome {
    Applied,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountCommandStatus {
    pub status: AccountCommandOutcome,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AccountRefreshStatus {
    Completed,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AdvanceAccountTimeResponse {
    pub event_time_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountRefreshResponse {
    pub status: AccountRefreshStatus,
    pub account_id: Option<AccountId>,
    pub segments: Vec<SegmentKey>,
}
