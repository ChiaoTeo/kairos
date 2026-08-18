use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

use crate::domain::account::{
    external_instrument_ref, ExternalAccountModel as AccountModel,
    ExternalAccountSegment as AccountSegment, ExternalAccountSnapshot as AccountSnapshot,
    ExternalAccountStatus as AccountStatus, ExternalBalance as Balance,
    ExternalDecimal as DecimalValue, ExternalOpenOrder as OpenOrder, ExternalPosition as Position,
};
use crate::{
    ExternalAccountCredentialProfile, ExternalMarketProfile as AccountMarketProfile,
    ExternalMarketProfileRequest as AccountMarketProfileRequest,
};

pub(crate) fn normalize_market_profile(
    request: &AccountMarketProfileRequest,
    fee_payload: &Value,
    config_payload: &Value,
) -> Result<AccountMarketProfile, String> {
    let fee_row = fee_payload
        .get("data")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .ok_or_else(|| "OKX trade fee data is missing".to_string())?;
    let config_row = config_payload
        .get("data")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .ok_or_else(|| "OKX account config data is missing".to_string())?;
    let maker_fee =
        decimal_field(fee_row, "maker").or_else(|_| decimal_field(fee_row, "makerU"))?;
    let taker_fee =
        decimal_field(fee_row, "taker").or_else(|_| decimal_field(fee_row, "takerU"))?;
    let account_model = match config_row.get("acctLv").and_then(Value::as_str) {
        Some("1") => Some(AccountModel::NoMargin),
        Some("2") => Some(AccountModel::Margin),
        Some("3") => Some(AccountModel::Unified),
        Some("4") => Some(AccountModel::PortfolioMargin),
        _ => None,
    };
    Ok(AccountMarketProfile {
        account_id: request.account_id.clone(),
        segment_key: request.segment_key.clone(),
        market_id: request.market_id.clone(),
        account_model,
        margin_mode: None,
        position_mode: config_row
            .get("posMode")
            .and_then(Value::as_str)
            .map(str::to_owned),
        maker_fee: Some(maker_fee),
        taker_fee: Some(taker_fee),
        fee_currency: fee_row
            .get("feeCcy")
            .and_then(Value::as_str)
            .map(kairos_primitives::Currency::new)
            .transpose()
            .map_err(|error| error.to_string())?,
        fee_discount: None,
        fee_tier: fee_row
            .get("feeGroup")
            .and_then(Value::as_str)
            .map(str::to_owned),
        source: "okx".into(),
        observed_at_unix_nanos: now_nanos().into(),
    })
}

pub(crate) fn normalize_account(
    segment: &AccountSegment,
    balance: &Value,
    positions: &Value,
    orders: &Value,
) -> Result<AccountSnapshot, String> {
    let balance_row = balance
        .get("data")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .ok_or_else(|| "OKX balance data is missing".to_string())?;
    let details = balance_row
        .get("details")
        .and_then(Value::as_array)
        .ok_or_else(|| "OKX balance details is missing".to_string())?;
    let balances = details
        .iter()
        .filter_map(|item| {
            let code = item.get("ccy")?.as_str()?;
            let total = decimal_field(item, "eq")
                .or_else(|_| decimal_field(item, "cashBal"))
                .ok()?;
            Some(Balance {
                asset_id: kairos_primitives::AssetId::new(format!("asset:crypto:{code}")).ok()?,
                asset_code: kairos_primitives::Currency::new(code).ok()?,
                total,
                available: decimal_field(item, "availBal").ok(),
                locked: decimal_field(item, "frozenBal").ok(),
                ..Default::default()
            })
        })
        .collect::<Vec<_>>();

    let rows = positions
        .get("data")
        .and_then(Value::as_array)
        .ok_or_else(|| "OKX positions data is missing".to_string())?;
    let positions = rows
        .iter()
        .filter_map(|item| {
            let symbol = item.get("instId")?.as_str()?;
            let mut quantity = decimal_field(item, "pos").ok()?;
            if item.get("posSide").and_then(Value::as_str) == Some("short") {
                quantity.mantissa = -quantity.mantissa.abs();
            }
            if quantity.mantissa == 0 {
                return None;
            }
            let participant_instrument = external_instrument_ref(
                crate::domain::ParticipantKind::Exchange,
                "okx",
                item.get("instType")
                    .and_then(Value::as_str)
                    .unwrap_or("okx"),
                symbol,
            )
            .ok()?;
            Some(Position {
                participant_instrument,
                quantity,
                average_price: decimal_field(item, "avgPx").ok(),
                mark_price: decimal_field(item, "markPx").ok(),
                unrealized_pnl: decimal_field(item, "upl").ok(),
                ..Default::default()
            })
        })
        .collect();

    let open_orders = orders
        .get("data")
        .and_then(Value::as_array)
        .ok_or_else(|| "OKX pending orders data is missing".to_string())?
        .iter()
        .map(normalize_open_order)
        .collect::<Result<Vec<_>, _>>()?;
    Ok(AccountSnapshot {
        segment_key: segment.segment_key.clone(),
        balances: balances.clone(),
        collateral: balances,
        positions,
        open_orders,
        status: AccountStatus::Ready,
        observed_at_unix_nanos: now_nanos().into(),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: segment
            .account_model
            .as_deref()
            .and_then(AccountModel::parse),
        margin_mode: None,
        position_mode: None,
        partial: false,
    })
}

pub(crate) fn normalize_credential_profile(
    payload: &Value,
    segment: &str,
) -> Result<ExternalAccountCredentialProfile, String> {
    let row = payload
        .get("data")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .ok_or_else(|| "OKX account config data is missing".to_string())?;
    let account_type = row.get("acctLv").and_then(Value::as_str).map(str::to_owned);
    let remote_identity = row.get("uid").and_then(Value::as_str).map(str::to_owned);
    let mut attributes = std::collections::BTreeMap::new();
    if let Some(value) = row.get("posMode").and_then(Value::as_str) {
        attributes.insert("position_mode".into(), value.into());
    }
    Ok(ExternalAccountCredentialProfile {
        remote_identity,
        account_type,
        permissions: vec!["read".into()],
        segments: vec![segment.into()],
        attributes,
    })
}

fn normalize_open_order(value: &Value) -> Result<OpenOrder, String> {
    let remote_order_id = value
        .get("ordId")
        .and_then(Value::as_str)
        .ok_or_else(|| "OKX pending order id is missing".to_string())?;
    let symbol = value
        .get("instId")
        .and_then(Value::as_str)
        .ok_or_else(|| "OKX pending order instrument is missing".to_string())?;
    let participant_instrument = external_instrument_ref(
        crate::domain::ParticipantKind::Exchange,
        "okx",
        value
            .get("instType")
            .and_then(Value::as_str)
            .unwrap_or("okx"),
        symbol,
    )?;
    let local_order_id = value
        .get("clOrdId")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or(remote_order_id);
    Ok(OpenOrder {
        order_id: kairos_primitives::OrderId::new(local_order_id)?,
        remote_order_id: Some(kairos_primitives::RemoteOrderId::new(remote_order_id)?),
        participant_instrument,
        side: crate::domain::execution::normalize_order_side(
            value
                .get("side")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        ),
        quantity: decimal_field(value, "sz")?,
        filled_quantity: decimal_field(value, "accFillSz").unwrap_or_default(),
        status: crate::domain::execution::normalize_order_status(
            value
                .get("state")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        ),
    })
}

fn decimal_field(value: &Value, field: &str) -> Result<DecimalValue, String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("OKX field is missing: {field}"))
        .and_then(decimal)
}

fn decimal(value: &str) -> Result<DecimalValue, String> {
    DecimalValue::parse(value)
}

fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::{normalize_account, normalize_market_profile};
    use crate::domain::account::{
        ExternalAccountIdentity, ExternalAccountSegment as AccountSegment,
    };
    use crate::ExternalMarketProfileRequest as AccountMarketProfileRequest;
    #[test]
    fn normalizes_okx_balance_and_short_position() {
        let segment = AccountSegment {
            identity: ExternalAccountIdentity::new("okx", "main").unwrap(),
            segment_key: kairos_primitives::SegmentKey::new("swap").unwrap(),
            environment: "live".into(),
            account_model: Some("unified".into()),
        };
        let balance = serde_json::json!({"code":"0","data":[{"details":[{"ccy":"USDT","eq":"1000","availBal":"900","frozenBal":"100"}]}]});
        let positions = serde_json::json!({"code":"0","data":[{"instId":"BTC-USDT-SWAP","pos":"2","posSide":"short","avgPx":"60000","markPx":"59000","upl":"200"}]});
        let result = normalize_account(
                &segment,
                &balance,
                &positions,
                &serde_json::json!({"data":[{"ordId":"9","clOrdId":"okx-9","instId":"BTC-USDT-SWAP","side":"sell","sz":"1","accFillSz":"0","state":"live"}]}),
            )
            .unwrap();
        assert_eq!(result.balances[0].total.mantissa, 1000);
        assert_eq!(result.positions[0].quantity.mantissa, -2);
        assert_eq!(result.open_orders[0].order_id, "okx-9");
    }

    #[test]
    fn normalizes_okx_market_fee_and_account_mode_profile() {
        let request = AccountMarketProfileRequest {
            account_id: kairos_primitives::AccountId::new("main").unwrap(),
            segment_key: kairos_primitives::SegmentKey::new("swap").unwrap(),
            market_id: kairos_primitives::MarketId::new("market:okx:BTC-USDT-SWAP").unwrap(),
            source_symbol: kairos_primitives::Symbol::new("BTC-USDT-SWAP").unwrap(),
        };
        let result = normalize_market_profile(
                &request,
                &serde_json::json!({"code":"0","data":[{"maker":"-0.0002","taker":"0.0005","feeCcy":"USDT","feeGroup":"1"}]}),
                &serde_json::json!({"code":"0","data":[{"acctLv":"3","posMode":"long_short_mode"}]}),
            )
            .unwrap();
        assert_eq!(
            result.account_model,
            Some(crate::domain::account::ExternalAccountModel::Unified)
        );
        assert_eq!(result.position_mode.as_deref(), Some("long_short_mode"));
        assert_eq!(result.fee_currency.as_deref(), Some("USDT"));
    }
}
