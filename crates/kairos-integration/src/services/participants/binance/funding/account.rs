//! Binance Funding Wallet payload normalization.

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::application::capabilities::account_facts::{
    ExternalAccountSegment as AccountSegment, ExternalAccountSnapshot as AccountSnapshot,
    ExternalAccountStatus as AccountStatus, ExternalBalance as Balance,
    ExternalDecimal as DecimalValue,
};
use serde_json::Value;

use crate::application::ExternalAccountCredentialProfile;

pub(crate) fn normalize_credential_profile(payload: &Value) -> ExternalAccountCredentialProfile {
    let mut permissions = vec!["read".to_string()];
    if payload
        .get("canTrade")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        permissions.push("trade".into());
    }
    ExternalAccountCredentialProfile {
        remote_identity: None,
        account_type: payload
            .get("accountType")
            .and_then(Value::as_str)
            .map(str::to_owned),
        permissions,
        segments: vec!["funding".into()],
        attributes: BTreeMap::new(),
    }
}

pub(crate) fn normalize_funding(
    segment: &AccountSegment,
    payload: &Value,
) -> Result<AccountSnapshot, String> {
    let rows = payload
        .as_array()
        .ok_or_else(|| "Binance funding response must be an array".to_string())?;
    let balances = rows
        .iter()
        .filter_map(|row| {
            let code = row.get("asset").and_then(Value::as_str)?;
            let free = decimal(row.get("free").and_then(Value::as_str).unwrap_or("0")).ok()?;
            let locked = decimal(
                row.get("locked")
                    .or_else(|| row.get("freeze"))
                    .and_then(Value::as_str)
                    .unwrap_or("0"),
            )
            .ok()?;
            let scale = free.scale.max(locked.scale);
            let total = rescale(free, scale)
                .ok()?
                .checked_add(rescale(locked, scale).ok()?)?;
            Some(Ok(Balance {
                asset_id: kairos_domain_types::AssetId::new(format!("asset:crypto:{code}")).ok()?,
                asset_code: kairos_domain_types::Currency::new(code).ok()?,
                total: DecimalValue::new(total, scale),
                available: Some(free),
                locked: Some(locked),
                ..Default::default()
            }))
        })
        .collect::<Result<Vec<_>, String>>()?;
    Ok(AccountSnapshot {
        segment_key: segment.segment_key.clone(),
        balances,
        collateral: Vec::new(),
        positions: Vec::new(),
        open_orders: Vec::new(),
        status: AccountStatus::Ready,
        observed_at_unix_nanos: now_nanos().into(),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: None,
        margin_mode: None,
        position_mode: None,
        partial: false,
    })
}

fn decimal(value: &str) -> Result<DecimalValue, String> {
    DecimalValue::parse(value)
}

fn rescale(value: DecimalValue, scale: u8) -> Result<i64, String> {
    value.rescale_exact(scale).map(|value| value.mantissa)
}

fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::normalize_funding;
    use crate::application::capabilities::account_facts::{
        ExternalAccountIdentity, ExternalAccountSegment as AccountSegment,
    };

    #[test]
    fn normalizes_funding_wallet_balances() {
        let segment = AccountSegment {
            identity: ExternalAccountIdentity::new("binance", "main").unwrap(),
            segment_key: kairos_domain_types::SegmentKey::new("funding").unwrap(),
            environment: "live".into(),
            account_model: None,
        };
        let snapshot = normalize_funding(
            &segment,
            &serde_json::json!([{"asset":"USDT","free":"10.25","freeze":"0.75"}]),
        )
        .unwrap();
        assert_eq!(snapshot.balances[0].total.mantissa, 1100);
    }
}
