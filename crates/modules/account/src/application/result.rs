pub use crate::domain::{
    AccountBusinessChange, AccountBusinessEvent, AccountCurrentView, AccountDifference,
    AccountFactProvenance, AccountSegmentCompleteness, AccountSegmentFreshness,
    AccountSegmentSyncLifecycle, AccountSegmentSyncMode, AccountSegmentView,
};
use crate::domain::{AccountId, SegmentKey};

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountRefreshIssue {
    pub segment_key: SegmentKey,
    pub error: String,
    pub elapsed_ms: u64,
    pub diagnostic_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountRefreshReport {
    pub account_id: AccountId,
    pub refreshed_segments: Vec<SegmentKey>,
    pub issues: Vec<AccountRefreshIssue>,
    #[serde(default)]
    pub differences: Vec<AccountDifference>,
}

impl From<crate::services::runtime::RuntimeRefreshReport> for AccountRefreshReport {
    fn from(value: crate::services::runtime::RuntimeRefreshReport) -> Self {
        Self {
            account_id: value.account_id,
            refreshed_segments: value.refreshed_segments,
            issues: value
                .issues
                .into_iter()
                .map(|issue| AccountRefreshIssue {
                    segment_key: issue.segment_key,
                    error: issue.error,
                    elapsed_ms: issue.elapsed_ms,
                    diagnostic_id: issue.diagnostic_id,
                })
                .collect(),
            differences: value.differences,
        }
    }
}
