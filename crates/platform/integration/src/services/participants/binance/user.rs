use kairos_primitives::{
    AssetId, Currency, FillId, OrderId, RemoteOrderId, SegmentKey, Symbol, UnixNanos,
};
use serde_json::Value;

use crate::{
    domain::execution::normalize_order_status, ExternalAccountEvent, ExternalAccountSnapshot,
    ExternalAccountStatus, ExternalBalance, ExternalDecimal, ExternalEventEnvelope,
    ExternalExecutionEvent, ExternalFillEvent, ExternalOrderEvent, ExternalOrderStatus,
    ExternalPosition, IntegrationError, OrderSide, OrderType, ParticipantKind, ParticipantRef,
};

pub(crate) fn account_event(
    connection_key: &crate::ConnectionKey,
    segment_key: &str,
    channel_epoch: u64,
    value: &Value,
) -> Result<Option<crate::ExternalAccountEventEnvelope>, IntegrationError> {
    let event_type = event_type(value);
    let account = match event_type {
        "outboundAccountPosition" => {
            ExternalAccountEvent::Snapshot(spot_account(segment_key, value)?)
        }
        "balanceUpdate" => ExternalAccountEvent::Snapshot(spot_balance(segment_key, value)?),
        "ACCOUNT_UPDATE" => ExternalAccountEvent::Snapshot(futures_account(segment_key, value)?),
        "executionReport" | "ORDER_TRADE_UPDATE" => order_account(segment_key, value)?,
        _ => return Ok(None),
    };
    Ok(Some(envelope(
        connection_key,
        channel_epoch,
        value,
        account,
    )))
}

pub(crate) fn execution_event(
    connection_key: &crate::ConnectionKey,
    channel_epoch: u64,
    value: &Value,
) -> Result<Option<ExternalEventEnvelope<ExternalExecutionEvent>>, IntegrationError> {
    let event_type = event_type(value);
    let row = match event_type {
        "executionReport" => value,
        "ORDER_TRADE_UPDATE" => value.get("o").ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance order update payload is missing".into())
        })?,
        _ => return Ok(None),
    };
    let order_id = field(row, "c", "clientOrderId")
        .or_else(|| field(row, "i", "orderId"))
        .ok_or_else(|| IntegrationError::InvalidPayload("Binance order id is missing".into()))?;
    let symbol = field(row, "s", "symbol").ok_or_else(|| {
        IntegrationError::InvalidPayload("Binance order symbol is missing".into())
    })?;
    let observed = event_millis(value).saturating_mul(1_000_000).into();
    let execution_id = field(row, "t", "tradeId")
        .filter(|value| value != "-1" && value != "0")
        .map(|value| FillId::new(format!("binance:{value}")))
        .transpose()
        .map_err(payload)?;
    Ok(Some(ExternalEventEnvelope {
        participant: participant(),
        connection_key: connection_key.clone(),
        channel_id: format!("{connection_key}.user"),
        channel_epoch,
        participant_event_id: execution_id.as_ref().map(ToString::to_string),
        participant_sequence: None,
        delivery: crate::ExternalEventDelivery::Incremental,
        observed_at_unix_nanos: observed,
        received_at_unix_nanos: now(),
        payload: ExternalExecutionEvent {
            order_id: OrderId::new(order_id).map_err(payload)?,
            symbol: Symbol::new(symbol).map_err(payload)?,
            status: normalize_order_status(
                field(row, "X", "orderStatus")
                    .as_deref()
                    .unwrap_or("UNKNOWN"),
            ),
            side: Some(if field(row, "S", "side").as_deref() == Some("SELL") {
                OrderSide::Sell
            } else {
                OrderSide::Buy
            }),
            order_type: Some(
                match field(row, "o", "orderType").as_deref().unwrap_or("LIMIT") {
                    "MARKET" => OrderType::Market,
                    "STOP" | "STOP_MARKET" | "STOP_LOSS" => OrderType::Stop,
                    "STOP_LOSS_LIMIT" | "TAKE_PROFIT_LIMIT" => OrderType::StopLimit,
                    _ => OrderType::Limit,
                },
            ),
            quantity: execution_decimal(field(row, "q", "originalQuantity").as_deref())?,
            limit_price: execution_decimal(field(row, "p", "originalPrice").as_deref())?
                .filter(|value| value.mantissa != 0),
            filled_quantity: execution_decimal(
                field(row, "z", "accumulatedFilledQuantity").as_deref(),
            )?,
            remaining_quantity: None,
            fill_quantity: execution_decimal(field(row, "l", "lastFilledQuantity").as_deref())?
                .filter(|value| value.mantissa != 0),
            fill_price: execution_decimal(field(row, "L", "lastFilledPrice").as_deref())?
                .filter(|value| value.mantissa != 0),
            execution_id,
            fee_currency: field(row, "N", "commissionAsset")
                .map(Currency::new)
                .transpose()
                .map_err(payload)?,
            fee_amount: execution_decimal(field(row, "n", "commission").as_deref())?,
            occurred_at_unix_nanos: observed,
            reason: field(row, "r", "rejectReason").unwrap_or_default(),
        },
    }))
}

fn order_account(
    segment_key: &str,
    value: &Value,
) -> Result<ExternalAccountEvent, IntegrationError> {
    let futures = event_type(value) == "ORDER_TRADE_UPDATE";
    let row = if futures {
        value.get("o").ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance order update payload is missing".into())
        })?
    } else {
        value
    };
    let order_id = field(row, "c", "clientOrderId")
        .or_else(|| field(row, "i", "orderId"))
        .ok_or_else(|| IntegrationError::InvalidPayload("Binance order id is missing".into()))?;
    let remote_order_id = field(row, "i", "orderId")
        .map(RemoteOrderId::new)
        .transpose()
        .map_err(payload)?;
    let occurred = UnixNanos::from(event_millis(value).saturating_mul(1_000_000));
    let status_text = field(row, "X", "orderStatus").unwrap_or_else(|| "UNKNOWN".into());
    let status = match status_text.as_str() {
        "NEW" | "PENDING_NEW" => ExternalOrderStatus::Acknowledged,
        "PARTIALLY_FILLED" => ExternalOrderStatus::PartiallyFilled,
        "FILLED" => ExternalOrderStatus::Filled,
        "CANCELED" => ExternalOrderStatus::Canceled,
        "REJECTED" => ExternalOrderStatus::Rejected,
        "EXPIRED" | "EXPIRED_IN_MATCH" => ExternalOrderStatus::Expired,
        _ => ExternalOrderStatus::Unknown,
    };
    let mut events = vec![ExternalAccountEvent::Order(ExternalOrderEvent {
        order_id: OrderId::new(&order_id).map_err(payload)?,
        status,
        remote_order_id,
        filled_quantity: account_optional(field(row, "z", "accumulatedFilledQuantity").as_deref())?,
        occurred_at_unix_nanos: occurred,
        reason: field(row, "r", "rejectReason").unwrap_or_default(),
    })];

    let fill_id = field(row, "t", "tradeId").filter(|value| value != "-1" && value != "0");
    let fill_quantity = account_optional(field(row, "l", "lastFilledQuantity").as_deref())?
        .filter(|value| value.mantissa != 0);
    let fill_price = account_optional(field(row, "L", "lastFilledPrice").as_deref())?
        .filter(|value| value.mantissa != 0);
    if let (Some(fill_id), Some(quantity), Some(price)) = (fill_id, fill_quantity, fill_price) {
        let symbol = field(row, "s", "symbol").ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance fill symbol is missing".into())
        })?;
        events.push(ExternalAccountEvent::Fill(ExternalFillEvent {
            fill_id: FillId::new(format!("binance:{fill_id}")).map_err(payload)?,
            order_id: OrderId::new(order_id).map_err(payload)?,
            segment_key: SegmentKey::new(segment_key).map_err(payload)?,
            participant_instrument: crate::external_instrument_ref(
                ParticipantKind::Exchange,
                "binance",
                if futures { "contract" } else { "spot" },
                &symbol,
            )
            .map_err(IntegrationError::InvalidPayload)?,
            side: field(row, "S", "side").unwrap_or_else(|| "UNKNOWN".into()),
            quantity,
            price,
            fee_asset: field(row, "N", "commissionAsset")
                .map(Currency::new)
                .transpose()
                .map_err(payload)?,
            fee_amount: account_optional(field(row, "n", "commission").as_deref())?,
            occurred_at_unix_nanos: occurred,
        }));
    }
    Ok(ExternalAccountEvent::Batch(events))
}

fn field(value: &Value, short: &str, long: &str) -> Option<String> {
    value
        .get(short)
        .or_else(|| value.get(long))
        .and_then(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .or_else(|| value.as_i64().map(|value| value.to_string()))
                .or_else(|| value.as_u64().map(|value| value.to_string()))
        })
        .filter(|value| !value.is_empty())
}

fn spot_account(
    segment_key: &str,
    value: &Value,
) -> Result<ExternalAccountSnapshot, IntegrationError> {
    let balances = value
        .get("B")
        .or_else(|| value.get("balances"))
        .and_then(Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance spot balances are missing".into())
        })?
        .iter()
        .map(|row| balance(row, "a", "f", "l", None))
        .collect::<Result<Vec<_>, _>>()?;
    snapshot(segment_key, balances, Vec::new(), event_millis(value))
}

fn spot_balance(
    segment_key: &str,
    value: &Value,
) -> Result<ExternalAccountSnapshot, IntegrationError> {
    let asset = value.get("a").and_then(Value::as_str).ok_or_else(|| {
        IntegrationError::InvalidPayload("Binance balance asset is missing".into())
    })?;
    let delta = account_decimal(value.get("d").and_then(Value::as_str).unwrap_or("0"))?;
    snapshot(
        segment_key,
        vec![ExternalBalance {
            asset_id: AssetId::new(format!("asset:crypto:{asset}")).map_err(payload)?,
            asset_code: Currency::new(asset).map_err(payload)?,
            total: delta,
            available: None,
            locked: None,
            borrowed: None,
            interest: None,
        }],
        Vec::new(),
        event_millis(value),
    )
}

fn futures_account(
    segment_key: &str,
    value: &Value,
) -> Result<ExternalAccountSnapshot, IntegrationError> {
    let account = value.get("a").ok_or_else(|| {
        IntegrationError::InvalidPayload("Binance futures account update is missing".into())
    })?;
    let balances = account
        .get("B")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|row| balance(row, "a", "cw", "bc", Some("wb")))
        .collect::<Result<Vec<_>, _>>()?;
    let positions = account
        .get("P")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|row| {
            let symbol = row.get("s").and_then(Value::as_str).ok_or_else(|| {
                IntegrationError::InvalidPayload("Binance position symbol is missing".into())
            })?;
            Ok(ExternalPosition {
                participant_instrument: crate::external_instrument_ref(
                    ParticipantKind::Exchange,
                    "binance",
                    "contract",
                    symbol,
                )
                .map_err(IntegrationError::InvalidPayload)?,
                position_side: match row.get("ps").and_then(Value::as_str) {
                    Some("LONG") => kairos_primitives::PositionSide::Long,
                    Some("SHORT") => kairos_primitives::PositionSide::Short,
                    _ => kairos_primitives::PositionSide::Net,
                },
                quantity: account_decimal(row.get("pa").and_then(Value::as_str).unwrap_or("0"))?,
                average_price: account_optional(row.get("ep").and_then(Value::as_str))?,
                mark_price: None,
                unrealized_pnl: account_optional(row.get("up").and_then(Value::as_str))?,
                realized_pnl: account_optional(row.get("cr").and_then(Value::as_str))?,
                updated_at_unix_nanos: UnixNanos::from(
                    event_millis(value).saturating_mul(1_000_000),
                ),
            })
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    snapshot(segment_key, balances, positions, event_millis(value))
}

fn balance(
    row: &Value,
    asset_field: &str,
    available_field: &str,
    locked_or_change_field: &str,
    total_field: Option<&str>,
) -> Result<ExternalBalance, IntegrationError> {
    let asset = row
        .get(asset_field)
        .and_then(Value::as_str)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance balance asset is missing".into())
        })?;
    let available = account_decimal(
        row.get(available_field)
            .and_then(Value::as_str)
            .unwrap_or("0"),
    )?;
    let other = account_decimal(
        row.get(locked_or_change_field)
            .and_then(Value::as_str)
            .unwrap_or("0"),
    )?;
    let total = total_field
        .and_then(|field| row.get(field))
        .and_then(Value::as_str)
        .map(account_decimal)
        .transpose()?
        .unwrap_or_else(|| add(available, other));
    Ok(ExternalBalance {
        asset_id: AssetId::new(format!("asset:crypto:{asset}")).map_err(payload)?,
        asset_code: Currency::new(asset).map_err(payload)?,
        total,
        available: Some(available),
        locked: Some(other),
        borrowed: None,
        interest: None,
    })
}

fn snapshot(
    segment_key: &str,
    balances: Vec<ExternalBalance>,
    positions: Vec<ExternalPosition>,
    event_millis: u64,
) -> Result<ExternalAccountSnapshot, IntegrationError> {
    Ok(ExternalAccountSnapshot {
        segment_key: SegmentKey::new(segment_key).map_err(payload)?,
        collateral: balances.clone(),
        balances,
        positions,
        open_orders: Vec::new(),
        status: ExternalAccountStatus::Ready,
        observed_at_unix_nanos: UnixNanos::from(event_millis.saturating_mul(1_000_000)),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: None,
        margin_mode: None,
        position_mode: None,
        partial: true,
    })
}

fn envelope<T>(
    connection_key: &crate::ConnectionKey,
    channel_epoch: u64,
    value: &Value,
    payload: T,
) -> ExternalEventEnvelope<T> {
    let observed = UnixNanos::from(event_millis(value).saturating_mul(1_000_000));
    ExternalEventEnvelope {
        participant: participant(),
        connection_key: connection_key.clone(),
        channel_id: format!("{connection_key}.user"),
        channel_epoch,
        participant_event_id: None,
        participant_sequence: None,
        delivery: crate::ExternalEventDelivery::Incremental,
        observed_at_unix_nanos: observed,
        received_at_unix_nanos: now(),
        payload,
    }
}

fn event_type(value: &Value) -> &str {
    value
        .get("e")
        .or_else(|| value.get("eventType"))
        .and_then(Value::as_str)
        .unwrap_or_default()
}

fn event_millis(value: &Value) -> u64 {
    value
        .get("E")
        .or_else(|| value.get("T"))
        .and_then(Value::as_u64)
        .unwrap_or_else(|| now().get() / 1_000_000)
}

fn execution_decimal(value: Option<&str>) -> Result<Option<crate::DecimalValue>, IntegrationError> {
    value
        .filter(|value| !value.is_empty())
        .map(parse_parts)
        .transpose()
        .map(|value| value.map(|(mantissa, scale)| crate::DecimalValue::new(mantissa, scale)))
}

fn account_optional(value: Option<&str>) -> Result<Option<ExternalDecimal>, IntegrationError> {
    value
        .filter(|value| !value.is_empty())
        .map(account_decimal)
        .transpose()
}

fn account_decimal(value: &str) -> Result<ExternalDecimal, IntegrationError> {
    let (mantissa, scale) = parse_parts(value)?;
    Ok(ExternalDecimal::new(mantissa, scale))
}

fn parse_parts(value: &str) -> Result<(i64, u8), IntegrationError> {
    let value = crate::DecimalValue::parse(value)
        .and_then(crate::DecimalValue::normalized)
        .map_err(payload)?;
    Ok((value.mantissa, value.scale))
}

fn add(left: ExternalDecimal, right: ExternalDecimal) -> ExternalDecimal {
    let scale = left.scale.max(right.scale);
    let left = left
        .mantissa
        .saturating_mul(10_i64.saturating_pow(u32::from(scale - left.scale)));
    let right = right
        .mantissa
        .saturating_mul(10_i64.saturating_pow(u32::from(scale - right.scale)));
    ExternalDecimal::new(left.saturating_add(right), scale)
}

fn participant() -> ParticipantRef {
    ParticipantRef::new(ParticipantKind::Exchange, "binance").expect("static Binance participant")
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
