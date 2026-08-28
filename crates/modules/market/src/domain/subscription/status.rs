use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use super::{SubscriptionMemberRequirement, SubscriptionMemberStatus};

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SubscriptionStatus {
    #[default]
    Pending,
    Ready,
    Degraded,
    Unavailable,
    Rejected,
}

pub fn derive_subscription_status(
    requirements: &BTreeMap<String, SubscriptionMemberRequirement>,
    members: &BTreeMap<String, SubscriptionMemberStatus>,
) -> SubscriptionStatus {
    if members.is_empty() {
        return SubscriptionStatus::Pending;
    }
    let required = members.iter().filter(|(member, _)| {
        requirements.get(*member).copied().unwrap_or_default()
            == SubscriptionMemberRequirement::Required
    });
    if required.clone().any(|(_, status)| {
        matches!(
            status,
            SubscriptionMemberStatus::Rejected | SubscriptionMemberStatus::Unavailable
        )
    }) {
        return SubscriptionStatus::Unavailable;
    }
    if required
        .clone()
        .any(|(_, status)| matches!(status, SubscriptionMemberStatus::Degraded))
    {
        return SubscriptionStatus::Degraded;
    }
    if required
        .clone()
        .any(|(_, status)| matches!(status, SubscriptionMemberStatus::Pending))
    {
        return SubscriptionStatus::Pending;
    }
    if members.values().any(|status| {
        matches!(
            status,
            SubscriptionMemberStatus::Degraded
                | SubscriptionMemberStatus::Unavailable
                | SubscriptionMemberStatus::Rejected
        )
    }) {
        SubscriptionStatus::Degraded
    } else {
        SubscriptionStatus::Ready
    }
}
