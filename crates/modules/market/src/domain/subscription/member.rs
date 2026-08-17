use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SubscriptionMemberRequirement {
    #[default]
    Required,
    Optional,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SubscriptionMemberStatus {
    #[default]
    Pending,
    Ready,
    Degraded,
    Unavailable,
    Rejected,
}
