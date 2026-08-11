use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

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
        return SubscriptionStatus::Ready;
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

/// Validate the small, module-owned selector language used by subscriptions.
/// An empty selector list means all observation kinds.  `bar:<timeframe>`
/// selects one bar view, while `bar` selects every bar timeframe.
pub fn validate_selectors(selectors: &[String]) -> Result<(), String> {
    for selector in selectors {
        let value = selector.trim();
        if value.is_empty() {
            return Err("subscription selector cannot be blank".into());
        }
        let (kind, qualifier) = value.split_once(':').unwrap_or((value, ""));
        let kind = kind.to_ascii_lowercase();
        if !matches!(
            kind.as_str(),
            "*" | "quote"
                | "trade"
                | "bar"
                | "trade_bar"
                | "quote_bar"
                | "greek"
                | "greeks"
                | "orderbook"
                | "rate"
                | "funding_rate"
                | "mark_price"
                | "index_price"
                | "open_interest"
                | "instrument_status"
        ) {
            return Err(format!("unsupported market selector: {selector}"));
        }
        if kind == "*" && !qualifier.is_empty() {
            return Err(format!(
                "wildcard selector cannot have a qualifier: {selector}"
            ));
        }
        if !matches!(
            kind.as_str(),
            "bar" | "trade_bar" | "quote_bar" | "rate" | "funding_rate"
        ) && !qualifier.is_empty()
        {
            return Err(format!(
                "selector qualifier is only supported for bars and rates: {selector}"
            ));
        }
        if matches!(
            kind.as_str(),
            "bar" | "trade_bar" | "quote_bar" | "rate" | "funding_rate"
        ) && qualifier.trim().is_empty()
            && value.contains(':')
        {
            return Err(format!("selector qualifier cannot be blank: {selector}"));
        }
    }
    Ok(())
}

pub fn selector_matches_observation(
    selectors: &[String],
    kind: &str,
    qualifier: Option<&str>,
) -> bool {
    if selectors.is_empty() {
        return true;
    }
    selectors.iter().any(|selector| {
        let (selected_kind, selected_qualifier) = selector
            .trim()
            .split_once(':')
            .unwrap_or((selector.trim(), ""));
        let selected_kind = selected_kind.to_ascii_lowercase();
        if selected_kind == "*"
            || selected_kind == kind
            || (kind == "rate" && selected_kind == "funding_rate")
            || (kind == "funding_rate" && selected_kind == "rate")
            || (kind == "greek" && selected_kind == "greeks")
        {
            selected_qualifier.is_empty() || qualifier == Some(selected_qualifier)
        } else {
            false
        }
    })
}

pub fn selector_matches_orderbook(selectors: &[String]) -> bool {
    selectors.is_empty()
        || selectors.iter().any(|selector| {
            matches!(
                selector.trim().to_ascii_lowercase().as_str(),
                "*" | "orderbook"
            )
        })
}

#[cfg(test)]
mod tests {
    use super::{selector_matches_observation, validate_selectors};

    #[test]
    fn derivative_selectors_support_qualified_rates() {
        let selectors = vec!["funding_rate:8h".to_string(), "mark_price".to_string()];
        validate_selectors(&selectors).unwrap();
        assert!(selector_matches_observation(
            &["funding_rate".into()],
            "rate",
            None
        ));
        assert!(selector_matches_observation(
            &["mark_price".into()],
            "mark_price",
            None
        ));
    }
}
