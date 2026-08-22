use kairos_primitives::reference::{AssetId, Currency};
use kairos_primitives::time::UnixNanos;
use serde_json::Value;

use crate::{
    ExternalAccountModel, ExternalAccountSegment, ExternalAccountSnapshot, ExternalAccountStatus,
    ExternalBalance, ExternalDecimal, ExternalMarginMode, ExternalPosition, IntegrationError,
    ParticipantKind, external_instrument_ref,
};

pub(crate) fn spot(
    segment: &ExternalAccountSegment,
    value: &Value,
) -> Result<ExternalAccountSnapshot, IntegrationError> {
    snapshot(segment, balances(value, "balances", false)?, None)
}

pub(crate) fn margin(
    segment: &ExternalAccountSegment,
    value: &Value,
) -> Result<ExternalAccountSnapshot, IntegrationError> {
    snapshot(
        segment,
        balances(value, "userAssets", true)?,
        Some(ExternalMarginMode::Cross),
    )
}

pub(crate) fn funding(
    segment: &ExternalAccountSegment,
    value: &Value,
) -> Result<ExternalAccountSnapshot, IntegrationError> {
    let rows = value.as_array().ok_or_else(|| {
        IntegrationError::InvalidPayload("Binance Funding Wallet response must be an array".into())
    })?;
    let balances = rows
        .iter()
        .map(|row| {
            let asset = row.get("asset").and_then(Value::as_str).ok_or_else(|| {
                IntegrationError::InvalidPayload("Binance Funding Wallet asset is missing".into())
            })?;
            let available = first_decimal(row, &["free"])?.unwrap_or_default();
            let locked = ["locked", "freeze", "withdrawing"].into_iter().try_fold(
                ExternalDecimal::default(),
                |total, field| {
                    first_decimal(row, &[field]).map(|value| add(total, value.unwrap_or_default()))
                },
            )?;
            Ok(ExternalBalance {
                asset_id: AssetId::new(format!("asset:crypto:{asset}")).map_err(payload)?,
                asset_code: Currency::new(asset).map_err(payload)?,
                total: add(available, locked),
                available: Some(available),
                locked: Some(locked),
                borrowed: None,
                interest: None,
            })
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    snapshot(segment, balances, None)
}

pub(crate) fn futures(
    segment: &ExternalAccountSegment,
    value: &Value,
    instrument_type: &str,
) -> Result<ExternalAccountSnapshot, IntegrationError> {
    let balances = value
        .get("assets")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance Futures account assets are missing".into())
        })?
        .iter()
        .map(|row| -> Result<Option<ExternalBalance>, IntegrationError> {
            let Some(asset) = row.get("asset").and_then(Value::as_str) else {
                return Ok(None);
            };
            let Some(total) = row
                .get("balance")
                .or_else(|| row.get("walletBalance"))
                .and_then(Value::as_str)
                .map(parse)
                .transpose()?
            else {
                return Ok(None);
            };
            let available = row
                .get("availableBalance")
                .and_then(Value::as_str)
                .map(parse)
                .transpose()?;
            Ok(Some(ExternalBalance {
                asset_id: AssetId::new(format!("asset:crypto:{asset}")).map_err(payload)?,
                asset_code: Currency::new(asset).map_err(payload)?,
                total,
                available,
                locked: None,
                borrowed: None,
                interest: None,
            }))
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?
        .into_iter()
        .flatten()
        .collect::<Vec<_>>();
    let positions = value
        .get("positions")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(
            |row| -> Result<Option<ExternalPosition>, IntegrationError> {
                let Some(symbol) = row.get("symbol").and_then(Value::as_str) else {
                    return Ok(None);
                };
                let Some(quantity) = row
                    .get("positionAmt")
                    .and_then(Value::as_str)
                    .map(parse)
                    .transpose()?
                else {
                    return Ok(None);
                };
                if quantity.mantissa == 0 {
                    return Ok(None);
                }
                Ok(Some(ExternalPosition {
                    participant_instrument: external_instrument_ref(
                        ParticipantKind::Exchange,
                        "binance",
                        instrument_type,
                        symbol,
                    )
                    .map_err(IntegrationError::InvalidPayload)?,
                    position_side: Default::default(),
                    quantity,
                    average_price: row
                        .get("entryPrice")
                        .and_then(Value::as_str)
                        .map(parse)
                        .transpose()?,
                    mark_price: row
                        .get("markPrice")
                        .and_then(Value::as_str)
                        .map(parse)
                        .transpose()?,
                    unrealized_pnl: row
                        .get("unrealizedProfit")
                        .and_then(Value::as_str)
                        .map(parse)
                        .transpose()?,
                    realized_pnl: None,
                    updated_at_unix_nanos: now(),
                }))
            },
        )
        .collect::<Result<Vec<_>, IntegrationError>>()?
        .into_iter()
        .flatten()
        .collect::<Vec<_>>();
    Ok(ExternalAccountSnapshot {
        segment_key: segment.segment_key.clone(),
        balances: balances.clone(),
        collateral: balances,
        positions,
        open_orders: Vec::new(),
        status: ExternalAccountStatus::Ready,
        observed_at_unix_nanos: now(),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: Some(ExternalAccountModel::Contract),
        margin_mode: Some(ExternalMarginMode::Cross),
        position_mode: None,
        partial: false,
    })
}

pub(crate) fn portfolio(
    segment: &ExternalAccountSegment,
    value: &Value,
) -> Result<ExternalAccountSnapshot, IntegrationError> {
    let rows = value
        .as_array()
        .or_else(|| value.get("data").and_then(Value::as_array))
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance portfolio balances are missing".into())
        })?;
    let balances = rows
        .iter()
        .map(|row| {
            let asset = row.get("asset").and_then(Value::as_str).ok_or_else(|| {
                IntegrationError::InvalidPayload(
                    "Binance portfolio balance asset is missing".into(),
                )
            })?;
            let total = first_decimal(row, &["totalWalletBalance", "crossMarginAsset", "balance"])?
                .unwrap_or_default();
            let available = first_decimal(row, &["crossMarginFree", "availableBalance", "free"])?;
            Ok(ExternalBalance {
                asset_id: AssetId::new(format!("asset:crypto:{asset}")).map_err(payload)?,
                asset_code: Currency::new(asset).map_err(payload)?,
                total,
                available,
                locked: available.map(|value| subtract(total, value)),
                borrowed: first_decimal(row, &["crossMarginBorrowed", "borrowed"])?,
                interest: first_decimal(row, &["crossMarginInterest", "interest"])?,
            })
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    Ok(ExternalAccountSnapshot {
        segment_key: segment.segment_key.clone(),
        collateral: balances.clone(),
        balances,
        positions: Vec::new(),
        open_orders: Vec::new(),
        status: ExternalAccountStatus::Ready,
        observed_at_unix_nanos: now(),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: Some(ExternalAccountModel::PortfolioMargin),
        margin_mode: Some(ExternalMarginMode::Cross),
        position_mode: None,
        partial: true,
    })
}

pub(crate) fn options(
    segment: &ExternalAccountSegment,
    value: &Value,
) -> Result<ExternalAccountSnapshot, IntegrationError> {
    let balance_rows = value
        .get("asset")
        .or_else(|| value.get("assets"))
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let balances = balance_rows
        .iter()
        .map(|row| {
            let asset = row
                .get("asset")
                .or_else(|| row.get("assetName"))
                .and_then(Value::as_str)
                .ok_or_else(|| {
                    IntegrationError::InvalidPayload(
                        "Binance Options balance asset is missing".into(),
                    )
                })?;
            let total = first_decimal(
                row,
                &["marginBalance", "equity", "walletBalance", "balance"],
            )?
            .unwrap_or_default();
            let available =
                first_decimal(row, &["available", "availableBalance", "availableMargin"])?;
            Ok(ExternalBalance {
                asset_id: AssetId::new(format!("asset:crypto:{asset}")).map_err(payload)?,
                asset_code: Currency::new(asset).map_err(payload)?,
                total,
                available,
                locked: available.map(|value| subtract(total, value)),
                borrowed: None,
                interest: None,
            })
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    let positions = value
        .get("position")
        .or_else(|| value.get("positions"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|row| {
            let symbol = row.get("symbol").and_then(Value::as_str).ok_or_else(|| {
                IntegrationError::InvalidPayload(
                    "Binance Options position symbol is missing".into(),
                )
            })?;
            Ok(ExternalPosition {
                participant_instrument: external_instrument_ref(
                    ParticipantKind::Exchange,
                    "binance",
                    "option",
                    symbol,
                )
                .map_err(IntegrationError::InvalidPayload)?,
                position_side: kairos_primitives::account::PositionSide::Net,
                quantity: first_decimal(row, &["quantity", "positionAmt"])?.unwrap_or_default(),
                average_price: first_decimal(row, &["entryPrice", "averagePrice"])?,
                mark_price: first_decimal(row, &["markPrice"])?,
                unrealized_pnl: first_decimal(row, &["unrealizedPNL", "unrealizedProfit"])?,
                realized_pnl: first_decimal(row, &["realizedPNL", "realizedProfit"])?,
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
        equity: first_decimal(value, &["equity", "marginBalance"])?,
        initial_equity: None,
        net_profit: first_decimal(value, &["unrealizedPNL"])?,
        account_model: Some(ExternalAccountModel::Contract),
        margin_mode: None,
        position_mode: None,
        partial: true,
    })
}

fn first_decimal(
    row: &Value,
    fields: &[&str],
) -> Result<Option<ExternalDecimal>, IntegrationError> {
    fields
        .iter()
        .find_map(|field| {
            row.get(*field)
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
        })
        .map(parse)
        .transpose()
}

fn balances(
    value: &Value,
    field: &str,
    include_debt: bool,
) -> Result<Vec<ExternalBalance>, IntegrationError> {
    value
        .get(field)
        .and_then(Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload(format!("Binance account {field} are missing"))
        })?
        .iter()
        .map(|row| -> Result<Option<ExternalBalance>, IntegrationError> {
            let Some(asset) = row.get("asset").and_then(Value::as_str) else {
                return Ok(None);
            };
            let Some(available) = row
                .get("free")
                .and_then(Value::as_str)
                .map(parse)
                .transpose()?
            else {
                return Ok(None);
            };
            let Some(locked) = row
                .get("locked")
                .and_then(Value::as_str)
                .map(parse)
                .transpose()?
            else {
                return Ok(None);
            };
            let borrowed = if include_debt {
                row.get("borrowed")
                    .and_then(Value::as_str)
                    .map(parse)
                    .transpose()?
            } else {
                None
            };
            let interest = if include_debt {
                row.get("interest")
                    .and_then(Value::as_str)
                    .map(parse)
                    .transpose()?
            } else {
                None
            };
            let debt = add(borrowed.unwrap_or_default(), interest.unwrap_or_default());
            let total = subtract(add(available, locked), debt);
            if total.mantissa == 0 && available.mantissa == 0 && locked.mantissa == 0 {
                return Ok(None);
            }
            Ok(Some(ExternalBalance {
                asset_id: AssetId::new(format!("asset:crypto:{asset}")).map_err(payload)?,
                asset_code: Currency::new(asset).map_err(payload)?,
                total,
                available: Some(available),
                locked: Some(locked),
                borrowed,
                interest,
            }))
        })
        .collect::<Result<Vec<_>, IntegrationError>>()
        .map(|rows| rows.into_iter().flatten().collect())
}

fn snapshot(
    segment: &ExternalAccountSegment,
    balances: Vec<ExternalBalance>,
    margin_mode: Option<ExternalMarginMode>,
) -> Result<ExternalAccountSnapshot, IntegrationError> {
    Ok(ExternalAccountSnapshot {
        segment_key: segment.segment_key.clone(),
        balances: balances.clone(),
        collateral: balances,
        positions: Vec::new(),
        open_orders: Vec::new(),
        status: ExternalAccountStatus::Ready,
        observed_at_unix_nanos: now(),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: segment
            .account_model
            .as_deref()
            .and_then(ExternalAccountModel::parse),
        margin_mode,
        position_mode: None,
        partial: false,
    })
}

fn parse(value: &str) -> Result<ExternalDecimal, IntegrationError> {
    ExternalDecimal::parse(value)
        .and_then(ExternalDecimal::normalized)
        .map_err(payload)
}

fn add(left: ExternalDecimal, right: ExternalDecimal) -> ExternalDecimal {
    combine(left, right, i64::saturating_add)
}

fn subtract(left: ExternalDecimal, right: ExternalDecimal) -> ExternalDecimal {
    combine(left, right, i64::saturating_sub)
}

fn combine(
    left: ExternalDecimal,
    right: ExternalDecimal,
    operation: fn(i64, i64) -> i64,
) -> ExternalDecimal {
    let scale = left.scale.max(right.scale);
    let left = left
        .mantissa
        .saturating_mul(10_i64.saturating_pow(u32::from(scale - left.scale)));
    let right = right
        .mantissa
        .saturating_mul(10_i64.saturating_pow(u32::from(scale - right.scale)));
    ExternalDecimal::new(operation(left, right), scale)
}

fn payload(error: impl std::fmt::Display) -> IntegrationError {
    IntegrationError::InvalidPayload(error.to_string())
}

fn now() -> UnixNanos {
    use std::time::{SystemTime, UNIX_EPOCH};

    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or_default();
    UnixNanos::from(u64::try_from(nanos).unwrap_or(u64::MAX))
}

#[cfg(test)]
mod tests {
    use super::funding;
    use crate::{ExternalAccountIdentity, ExternalAccountSegment, ExternalDecimal};

    #[test]
    fn funding_wallet_combines_every_unavailable_balance_bucket() {
        let segment = ExternalAccountSegment {
            identity: ExternalAccountIdentity::new("binance", "main").unwrap(),
            segment_key: kairos_primitives::account::SegmentKey::new("funding").unwrap(),
            environment: "test".into(),
            account_model: Some("no_margin".into()),
        };
        let snapshot = funding(
            &segment,
            &serde_json::json!([{
                "asset": "USDT",
                "free": "10.25",
                "locked": "1.00",
                "freeze": "0.50",
                "withdrawing": "0.25"
            }]),
        )
        .unwrap();

        assert_eq!(snapshot.balances.len(), 1);
        assert_eq!(
            snapshot.balances[0].available,
            Some(ExternalDecimal::new(1025, 2))
        );
        assert_eq!(
            snapshot.balances[0].locked,
            Some(ExternalDecimal::new(175, 2))
        );
        assert_eq!(snapshot.balances[0].total, ExternalDecimal::new(1200, 2));
    }
}
