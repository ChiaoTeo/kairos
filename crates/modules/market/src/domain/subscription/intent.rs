use std::collections::BTreeMap;

pub use kairos_primitives::market::SubscriptionId;
use serde::{Deserialize, Serialize};

use super::{
    ObservationSelector, SubscriptionMemberRequirement, SubscriptionMemberStatus,
    SubscriptionStatus,
};
use crate::domain::market::{MarketSelectionQuery, ResolvedMarket};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SubscriptionMode {
    Static,
    Dynamic,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SubscriptionState {
    pub id: SubscriptionId,
    pub owner_id: String,
    pub mode: SubscriptionMode,
    pub query: Option<MarketSelectionQuery>,
    #[serde(default)]
    pub selectors: Vec<ObservationSelector>,
    pub members: BTreeMap<String, ResolvedMarket>,
    #[serde(default)]
    pub member_requirements: BTreeMap<String, SubscriptionMemberRequirement>,
    #[serde(default)]
    pub member_status: BTreeMap<String, SubscriptionMemberStatus>,
    #[serde(default)]
    pub status: SubscriptionStatus,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReconcileResult {
    pub added: Vec<String>,
    pub removed: Vec<String>,
    pub changed: Vec<String>,
    pub unchanged: Vec<String>,
    pub rejected: Option<String>,
}
