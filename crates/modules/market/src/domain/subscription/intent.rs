use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use super::{
    ObservationSelector, SubscriptionMemberRequirement, SubscriptionMemberStatus,
    SubscriptionStatus,
};
use crate::domain::market::{MarketSelectionQuery, ResolvedMarket};

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct SubscriptionId(pub String);

impl SubscriptionId {
    pub fn new(value: impl Into<String>) -> Result<Self, String> {
        let value = value.into();
        if value.trim().is_empty() {
            return Err("subscription id is required".into());
        }
        Ok(Self(value))
    }
}

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
