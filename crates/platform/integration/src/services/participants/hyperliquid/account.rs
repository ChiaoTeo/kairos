use kairos_primitives::{AssetId, ClientOrderId, Currency, OrderId, Symbol, UnixNanos};
use serde_json::Value;

use crate::{
    DecimalValue, ExternalAccountModel, ExternalAccountSegment, ExternalAccountSnapshot,
    ExternalAccountStatus, ExternalBalance, ExternalDecimal, ExternalMarginMode, ExternalOrder,
    ExternalOrderQuery, ExternalPosition, IntegrationError, OrderSide, OrderType, ParticipantKind,
    external_instrument_ref,
};

pub(crate) fn snapshot(
    segment: &ExternalAccountSegment,
    perpetual: &Value,
    spot: &Value,
) -> Result<ExternalAccountSnapshot, IntegrationError> {
    let mut balances = spot
        .get("balances")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|row| {
            let asset = text(row, "coin")?;
            let total = external_decimal(text(row, "total")?)?;
            let locked = optional_external(row, "hold")?;
            Ok(ExternalBalance {
                asset_id: AssetId::new(format!("asset:crypto:{asset}")).map_err(payload)?,
                asset_code: Currency::new(asset).map_err(payload)?,
                total,
                available: locked.map(|locked| subtract(total, locked)),
                locked,
                borrowed: None,
                interest: None,
            })
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    if let Some(withdrawable) = perpetual.get("withdrawable").and_then(Value::as_str) {
        balances.push(ExternalBalance {
            asset_id: AssetId::new("asset:crypto:USDC").map_err(payload)?,
            asset_code: Currency::new("USDC").map_err(payload)?,
            total: perpetual
                .pointer("/marginSummary/accountValue")
                .and_then(Value::as_str)
                .map(external_decimal)
                .transpose()?
                .unwrap_or_else(|| ExternalDecimal::new(0, 0)),
            available: Some(external_decimal(withdrawable)?),
            locked: None,
            borrowed: None,
            interest: None,
        });
    }
    let positions = perpetual
        .get("assetPositions")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|row| row.get("position").unwrap_or(row))
        .map(|row| {
            let symbol = text(row, "coin")?;
            Ok(ExternalPosition {
                participant_instrument: external_instrument_ref(
                    ParticipantKind::Exchange,
                    "hyperliquid",
                    "perpetual",
                    symbol,
                )
                .map_err(IntegrationError::InvalidPayload)?,
                position_side: kairos_primitives::PositionSide::Net,
                quantity: external_decimal(text(row, "szi")?)?,
                average_price: optional_external(row, "entryPx")?,
                mark_price: None,
                unrealized_pnl: optional_external(row, "unrealizedPnl")?,
                realized_pnl: optional_external(row, "cumFunding")?,
                updated_at_unix_nanos: now(),
            })
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    Ok(ExternalAccountSnapshot {
        segment_key: segment.segment_key.clone(),
        collateral: balances.clone(),
        balances,
        positions,
        open_orders: Vec::new(),
        status: ExternalAccountStatus::Ready,
        observed_at_unix_nanos: now(),
        equity: perpetual
            .pointer("/marginSummary/accountValue")
            .and_then(Value::as_str)
            .map(external_decimal)
            .transpose()?,
        initial_equity: None,
        net_profit: None,
        account_model: Some(ExternalAccountModel::ContractUnified),
        margin_mode: Some(ExternalMarginMode::Cross),
        position_mode: None,
        partial: false,
    })
}

pub(crate) fn orders(
    connection_key: &crate::ConnectionKey,
    value: &Value,
    query: &ExternalOrderQuery,
) -> Result<Vec<ExternalOrder>, IntegrationError> {
    value
        .as_array()
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Hyperliquid orders must be an array".into())
        })?
        .iter()
        .filter_map(|outer| {
            let row = outer.get("order").unwrap_or(outer);
            let symbol = row.get("coin").and_then(Value::as_str)?;
            if query
                .symbol
                .as_ref()
                .is_some_and(|value| value.as_str() != symbol)
            {
                return None;
            }
            Some(normalize_order(connection_key, outer, row, symbol))
        })
        .collect()
}

fn normalize_order(
    connection_key: &crate::ConnectionKey,
    outer: &Value,
    row: &Value,
    symbol: &str,
) -> Result<ExternalOrder, IntegrationError> {
    let remote = row
        .get("oid")
        .and_then(Value::as_u64)
        .map(|value| value.to_string())
        .or_else(|| row.get("oid").and_then(Value::as_str).map(str::to_owned))
        .ok_or_else(|| IntegrationError::InvalidPayload("Hyperliquid oid is missing".into()))?;
    let local = row
        .get("cloid")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or(&remote);
    Ok(ExternalOrder {
        connection_key: connection_key.clone(),
        order_id: OrderId::new(local).map_err(payload)?,
        client_order_id: row
            .get("cloid")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(ClientOrderId::new)
            .transpose()
            .map_err(payload)?,
        symbol: Symbol::new(symbol).map_err(payload)?,
        side: if row.get("side").and_then(Value::as_str) == Some("A") {
            OrderSide::Sell
        } else {
            OrderSide::Buy
        },
        order_type: OrderType::Limit,
        status: crate::domain::execution::normalize_order_status(
            outer
                .get("status")
                .and_then(Value::as_str)
                .unwrap_or("OPEN"),
        ),
        quantity: decimal(text(row, "origSz")?)?,
        filled_quantity: DecimalValue::new(0, 0),
        average_fill_price: None,
        occurred_at_unix_nanos: row
            .get("timestamp")
            .and_then(Value::as_u64)
            .map(|value| UnixNanos::from(value.saturating_mul(1_000_000))),
    })
}

fn text<'a>(value: &'a Value, field: &str) -> Result<&'a str, IntegrationError> {
    value
        .get(field)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| IntegrationError::InvalidPayload(format!("Hyperliquid {field} is missing")))
}

fn optional_external(
    value: &Value,
    field: &str,
) -> Result<Option<ExternalDecimal>, IntegrationError> {
    value
        .get(field)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(external_decimal)
        .transpose()
}

fn external_decimal(value: &str) -> Result<ExternalDecimal, IntegrationError> {
    decimal(value)
}

fn decimal(value: &str) -> Result<DecimalValue, IntegrationError> {
    DecimalValue::parse(value)
        .and_then(DecimalValue::normalized)
        .map_err(payload)
}

fn subtract(left: ExternalDecimal, right: ExternalDecimal) -> ExternalDecimal {
    let scale = left.scale.max(right.scale);
    let left = left
        .mantissa
        .saturating_mul(10_i64.saturating_pow(u32::from(scale - left.scale)));
    let right = right
        .mantissa
        .saturating_mul(10_i64.saturating_pow(u32::from(scale - right.scale)));
    ExternalDecimal::new(left.saturating_sub(right), scale)
}

fn now() -> UnixNanos {
    use std::time::{SystemTime, UNIX_EPOCH};
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or_default();
    UnixNanos::from(u64::try_from(nanos).unwrap_or(u64::MAX))
}

fn payload(error: impl std::fmt::Display) -> IntegrationError {
    IntegrationError::InvalidPayload(error.to_string())
}
