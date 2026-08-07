//! Composition helpers for account sources and process-owned snapshot writers.

pub mod account;

pub use crate::services::persistence::JsonAccountStore;
pub use crate::services::persistence::MemoryAccountStore;
pub use crate::services::publication::FileAccountPublisher;
pub use crate::services::publication::FlatbuffersAccountPublisher;

use crate::application::protocol::AccountSnapshotSource;
use crate::domain::{AccountSegment, AccountSnapshot, AccountStatus};

pub struct InMemoryAccountSource {
    pub snapshots: std::collections::BTreeMap<String, AccountSnapshot>,
}

impl AccountSnapshotSource for InMemoryAccountSource {
    fn fetch(&mut self, segment: &AccountSegment) -> Result<AccountSnapshot, String> {
        self.snapshots
            .get(&segment.segment_key)
            .cloned()
            .ok_or_else(|| format!("missing snapshot for segment: {}", segment.segment_key))
    }
}

pub fn empty_snapshot(segment_key: impl Into<String>) -> AccountSnapshot {
    AccountSnapshot {
        segment_key: segment_key.into(),
        balances: Vec::new(),
        collateral: Vec::new(),
        positions: Vec::new(),
        open_orders: Vec::new(),
        status: AccountStatus::Ready,
        observed_at_unix_nanos: 0,
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: None,
        margin_mode: None,
        position_mode: None,
        partial: false,
    }
}
