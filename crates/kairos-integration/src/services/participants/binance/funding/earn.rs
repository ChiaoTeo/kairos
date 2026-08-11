//! Binance Simple Earn capability.

use serde_json::Value;

use crate::application::{
    CommandOutcome, EarnActionResult, EarnPosition, EarnProduct, EarnProductType, EarnReward,
    ProviderRejection,
};

pub(crate) fn normalize_product(value: &Value) -> Option<EarnProduct> {
    Some(EarnProduct {
        product_id: value.get("productId")?.as_str()?.into(),
        asset: kairos_domain_types::Currency::new(value.get("asset")?.as_str()?).ok()?,
        product_type: product_type(
            value
                .get("productType")
                .and_then(Value::as_str)
                .unwrap_or("flexible"),
        ),
        annual_rate: value
            .get("latestAnnualPercentageRate")
            .map(value_string)
            .unwrap_or_default()
            .unwrap_or_default()
            .parse()
            .ok()?,
        min_amount: value
            .get("minAmount")
            .map(value_string)
            .unwrap_or_default()
            .unwrap_or_default()
            .parse()
            .ok()?,
        max_amount: value
            .get("maxAmount")
            .map(value_string)
            .unwrap_or_default()
            .unwrap_or_default()
            .parse()
            .ok()?,
        status: value
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .into(),
        duration_days: value
            .get("duration")
            .and_then(Value::as_u64)
            .map(|value| value as u32),
    })
}

pub(crate) fn normalize_position(value: &Value) -> Option<EarnPosition> {
    Some(EarnPosition {
        product_id: value.get("productId")?.as_str()?.into(),
        asset: kairos_domain_types::Currency::new(value.get("asset")?.as_str()?).ok()?,
        amount: value
            .get("totalAmount")
            .map(value_string)
            .unwrap_or_default()
            .unwrap_or_default()
            .parse()
            .ok()?,
        rewards: value
            .get("totalRewards")
            .map(value_string)
            .unwrap_or_default()
            .unwrap_or_default()
            .parse()
            .ok()?,
        annual_rate: value
            .get("latestAnnualPercentageRate")
            .map(value_string)
            .unwrap_or_default()
            .unwrap_or_default()
            .parse()
            .ok()?,
        status: value
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .into(),
        updated_at_unix_millis: value
            .get("updateTime")
            .and_then(Value::as_u64)
            .map(|value| value.saturating_mul(1_000_000).into()),
    })
}

pub(crate) fn normalize_reward(value: &Value) -> Option<EarnReward> {
    Some(EarnReward {
        asset: kairos_domain_types::Currency::new(value.get("asset")?.as_str()?).ok()?,
        amount: value
            .get("rewardsAmount")
            .map(value_string)
            .unwrap_or_default()
            .unwrap_or_default()
            .parse()
            .ok()?,
        product_id: value
            .get("productId")
            .and_then(Value::as_str)
            .map(str::to_owned),
        occurred_at_unix_millis: value
            .get("time")
            .and_then(Value::as_u64)
            .map(|value| value.saturating_mul(1_000_000).into()),
    })
}

fn product_type(value: &str) -> EarnProductType {
    if value.eq_ignore_ascii_case("locked") {
        EarnProductType::Locked
    } else {
        EarnProductType::Flexible
    }
}
pub(crate) fn product_type_name(value: EarnProductType) -> &'static str {
    match value {
        EarnProductType::Locked => "locked",
        EarnProductType::Flexible => "flexible",
    }
}
fn value_string(value: &Value) -> Option<String> {
    Some(
        value
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| value.to_string()),
    )
}

pub(crate) fn normalize_action(payload: &Value) -> CommandOutcome<EarnActionResult> {
    let success = payload
        .get("success")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let result = EarnActionResult {
        accepted: success,
        action_id: payload
            .get("positionId")
            .or_else(|| payload.get("purchaseId"))
            .and_then(value_string),
        status: if success { "accepted" } else { "rejected" }.into(),
        reason: payload
            .get("msg")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
    };
    if success {
        CommandOutcome::Confirmed(result)
    } else {
        CommandOutcome::Rejected(ProviderRejection::new(result.reason))
    }
}
