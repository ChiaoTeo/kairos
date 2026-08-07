//! Minimal ports consumed by the Account application.

use super::market_profile::{AccountMarketProfile, AccountMarketProfileRequest};
use crate::domain::{AccountEvent, AccountSegment, AccountSnapshot, AccountState};

use super::service::OrderRiskRequest;

/// Capability consumed by the order-planning use case.
///
/// Composition supplies the implementation. Account does not depend on the
/// Risk actor, transport, persistence, or vendor integration.
pub trait OrderRisk: Send {
    fn reserve(&mut self, request: &OrderRiskRequest) -> Result<(), String>;
    fn release(&mut self, reservation_id: &str) -> Result<(), String>;
    fn consume(&mut self, reservation_id: &str) -> Result<(), String>;
}

pub trait AccountSnapshotSource: Send {
    fn fetch(&mut self, segment: &AccountSegment) -> Result<AccountSnapshot, String>;
}

pub trait AccountStreamSource: Send {
    fn next_event(&mut self) -> Result<Option<AccountEvent>, String>;
}

pub trait AccountMarketProfileSource: Send {
    fn fetch_profile(
        &mut self,
        request: &AccountMarketProfileRequest,
    ) -> Result<AccountMarketProfile, String>;
}

pub trait AccountStateStore: Send {
    fn load(&mut self) -> Result<Vec<(AccountSegment, AccountState)>, String>;
    fn save(&mut self, accounts: &[crate::domain::Account]) -> Result<(), String>;
}
