//! OKX private account WebSocket payload normalization.

use std::time::{SystemTime, UNIX_EPOCH};

use crate::application::capabilities::account_facts::{
    external_instrument_ref, ExternalAccountEvent as AccountEvent,
    ExternalAccountSnapshot as AccountSnapshot, ExternalAccountStatus as AccountStatus,
    ExternalBalance as Balance, ExternalDecimal as DecimalValue, ExternalFillEvent as FillEvent,
    ExternalOrderEvent as OrderEvent, ExternalOrderStatus as OrderStatus,
};
use crate::application::capabilities::{
    execution_facts::normalize_order_status, DecimalValue as ExecutionDecimal, OrderSide, OrderType,
};
use crate::application::{ExternalEventEnvelope, ExternalExecutionEvent};
use kairos_primitives::{Currency, FillId, OrderId, Symbol, UnixNanos};
use serde_json::Value;
use tokio_tungstenite::tungstenite::Message;

pub(crate) fn login_succeeded(message: &Message) -> Result<bool, String> {
    let Message::Text(text) = message else {
        return Err("OKX private stream did not return a login response".into());
    };
    let value: Value = serde_json::from_str(text).map_err(|error| error.to_string())?;
    if value.get("event").and_then(Value::as_str) == Some("error") {
        return Ok(false);
    }
    Ok(value.get("event").and_then(Value::as_str) == Some("login")
        && value.get("code").and_then(Value::as_str).unwrap_or("0") == "0")
}

pub(crate) fn parse_event(segment_key: &str, text: &str) -> Result<Option<AccountEvent>, String> {
    let value: Value = serde_json::from_str(text).map_err(|error| error.to_string())?;
    if value.get("event").is_some() {
        return Ok(None);
    }
    match value
        .get("arg")
        .and_then(|arg| arg.get("channel"))
        .and_then(Value::as_str)
    {
        Some("account") => parse_account_event(segment_key, &value),
        Some("orders") => parse_order_event(segment_key, &value),
        _ => Ok(None),
    }
}

pub(crate) fn parse_execution_events(
    binding_id: &str,
    channel_id: &str,
    channel_epoch: u64,
    trading_mode: &str,
    received_at_unix_nanos: UnixNanos,
    text: &str,
) -> Result<Vec<ExternalEventEnvelope<ExternalExecutionEvent>>, String> {
    let value: Value = serde_json::from_str(text).map_err(|error| error.to_string())?;
    if value.get("event").is_some()
        || value.pointer("/arg/channel").and_then(Value::as_str) != Some("orders")
    {
        return Ok(Vec::new());
    }
    let rows = value
        .get("data")
        .and_then(Value::as_array)
        .ok_or_else(|| "OKX order event data is missing".to_string())?;
    rows.iter()
        .filter(|row| {
            row.get("tdMode")
                .and_then(Value::as_str)
                .is_none_or(|value| value == trading_mode)
        })
        .map(|row| {
            let text = |field: &str| {
                row.get(field)
                    .and_then(Value::as_str)
                    .filter(|value| !value.is_empty())
            };
            let local_order_id = text("clOrdId")
                .or_else(|| text("ordId"))
                .ok_or_else(|| "OKX execution event order identity is missing".to_string())?;
            let provider_order_id = text("ordId").unwrap_or("unknown");
            let symbol = text("instId")
                .ok_or_else(|| "OKX execution event instrument is missing".to_string())?;
            let state = text("state").unwrap_or("unknown");
            let event_time_millis = text("uTime").and_then(|value| value.parse::<u64>().ok());
            let observed_at_unix_nanos = event_time_millis
                .map(|value| UnixNanos::from(value.saturating_mul(1_000_000)))
                .unwrap_or(received_at_unix_nanos);
            let trade_id = text("tradeId").filter(|value| *value != "0");
            let provider_event_id = Some(format!(
                "okx:{provider_order_id}:{}:{state}:{}",
                trade_id.unwrap_or("none"),
                event_time_millis.unwrap_or_default()
            ));
            let fill_quantity =
                optional_execution_decimal(row, "fillSz")?.filter(|value| value.mantissa != 0);
            let fill_price =
                optional_execution_decimal(row, "fillPx")?.filter(|value| value.mantissa != 0);
            let fee_amount = optional_execution_decimal(row, "fee")?
                .filter(|value| value.mantissa != 0)
                .map(|value| ExecutionDecimal::new(value.mantissa.saturating_abs(), value.scale));
            Ok(ExternalEventEnvelope {
                participant: crate::domain::ParticipantRef::new(
                    crate::domain::ParticipantKind::Exchange,
                    "okx",
                )
                .expect("static OKX participant is valid"),
                binding_id: binding_id.into(),
                channel_id: channel_id.into(),
                channel_epoch,
                provider_event_id,
                provider_sequence: value.get("seqId").and_then(Value::as_u64),
                observed_at_unix_nanos,
                received_at_unix_nanos,
                payload: ExternalExecutionEvent {
                    order_id: OrderId::new(local_order_id)?,
                    symbol: Symbol::new(symbol)?,
                    status: normalize_order_status(state),
                    side: Some(if text("side") == Some("sell") {
                        OrderSide::Sell
                    } else {
                        OrderSide::Buy
                    }),
                    order_type: text("ordType").and_then(normalize_execution_order_type),
                    quantity: optional_execution_decimal(row, "sz")?,
                    limit_price: optional_execution_decimal(row, "px")?
                        .filter(|value| value.mantissa != 0),
                    filled_quantity: optional_execution_decimal(row, "accFillSz")?,
                    remaining_quantity: None,
                    fill_quantity,
                    fill_price,
                    execution_id: trade_id
                        .map(|value| FillId::new(format!("okx:{value}")))
                        .transpose()?,
                    fee_currency: text("feeCcy").map(Currency::new).transpose()?,
                    fee_amount,
                    occurred_at_unix_nanos: observed_at_unix_nanos,
                    reason: text("cancelSourceReason")
                        .or_else(|| text("sMsg"))
                        .unwrap_or_default()
                        .into(),
                },
            })
        })
        .collect()
}

fn optional_execution_decimal(
    value: &Value,
    field: &str,
) -> Result<Option<ExecutionDecimal>, String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(DecimalValue::parse)
        .transpose()
        .map(|value| value.map(|value| ExecutionDecimal::new(value.mantissa, value.scale)))
}

fn normalize_execution_order_type(value: &str) -> Option<OrderType> {
    match value {
        "market" => Some(OrderType::Market),
        "limit" | "post_only" | "fok" | "ioc" => Some(OrderType::Limit),
        "conditional" | "trigger" => Some(OrderType::Stop),
        _ => None,
    }
}

fn parse_account_event(segment_key: &str, value: &Value) -> Result<Option<AccountEvent>, String> {
    let row = value
        .get("data")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .ok_or_else(|| "OKX account event data is missing".to_string())?;
    let code = row
        .get("ccy")
        .and_then(Value::as_str)
        .ok_or_else(|| "OKX account event currency is missing".to_string())?;
    let total = decimal_field(row, "eq").or_else(|_| decimal_field(row, "cashBal"))?;
    Ok(Some(AccountEvent::Snapshot(AccountSnapshot {
        segment_key: kairos_primitives::SegmentKey::new(segment_key)?,
        balances: vec![Balance {
            asset_id: kairos_primitives::AssetId::new(format!("asset:crypto:{code}"))?,
            asset_code: kairos_primitives::Currency::new(code)?,
            total,
            available: decimal_field(row, "availBal").ok(),
            locked: decimal_field(row, "frozenBal").ok(),
            ..Default::default()
        }],
        collateral: vec![Balance {
            asset_id: kairos_primitives::AssetId::new(format!("asset:crypto:{code}"))?,
            asset_code: kairos_primitives::Currency::new(code)?,
            total,
            available: decimal_field(row, "availBal").ok(),
            locked: decimal_field(row, "frozenBal").ok(),
            ..Default::default()
        }],
        positions: Vec::new(),
        open_orders: Vec::new(),
        status: AccountStatus::Ready,
        observed_at_unix_nanos: row
            .get("uTime")
            .and_then(Value::as_str)
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or_else(now_nanos)
            .into(),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: None,
        margin_mode: None,
        position_mode: None,
        partial: true,
    })))
}

fn parse_order_event(segment_key: &str, value: &Value) -> Result<Option<AccountEvent>, String> {
    let row = value
        .get("data")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .ok_or_else(|| "OKX order event data is missing".to_string())?;
    let order_id = row
        .get("clOrdId")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .or_else(|| row.get("ordId").and_then(Value::as_str))
        .ok_or_else(|| "OKX order event identity is missing".to_string())?;
    let status = match row.get("state").and_then(Value::as_str).unwrap_or_default() {
        "live" | "partially_filled" => {
            if row.get("state").and_then(Value::as_str) == Some("partially_filled") {
                OrderStatus::PartiallyFilled
            } else {
                OrderStatus::Acknowledged
            }
        }
        "filled" => OrderStatus::Filled,
        "canceled" | "mmp_canceled" => OrderStatus::Canceled,
        _ => OrderStatus::Unknown,
    };
    let occurred_at_unix_nanos = row
        .get("uTime")
        .and_then(Value::as_str)
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or_else(now_nanos)
        * 1_000_000;
    let mut events = vec![AccountEvent::Order(OrderEvent {
        order_id: kairos_primitives::OrderId::new(order_id)?,
        status,
        remote_order_id: row
            .get("ordId")
            .and_then(Value::as_str)
            .map(kairos_primitives::RemoteOrderId::new)
            .transpose()?,
        filled_quantity: row
            .get("accFillSz")
            .and_then(Value::as_str)
            .map(decimal)
            .transpose()?,
        occurred_at_unix_nanos: occurred_at_unix_nanos.into(),
        reason: row
            .get("sMsg")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
    })];
    if let Some(fill_size) = row
        .get("fillSz")
        .and_then(Value::as_str)
        .filter(|value| *value != "0" && !value.is_empty())
    {
        let instrument_id = row
            .get("instId")
            .and_then(Value::as_str)
            .ok_or_else(|| "OKX fill instrument is missing".to_string())?;
        let price = row
            .get("fillPx")
            .and_then(Value::as_str)
            .ok_or_else(|| "OKX fill price is missing".to_string())?;
        let order_id = row
            .get("clOrdId")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .or_else(|| row.get("ordId").and_then(Value::as_str))
            .ok_or_else(|| "OKX fill order identity is missing".to_string())?;
        let fill_id = row
            .get("tradeId")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
            .unwrap_or_else(|| format!("{order_id}:{occurred_at_unix_nanos}"));
        events.push(AccountEvent::Fill(FillEvent {
            fill_id: kairos_primitives::FillId::new(fill_id)?,
            order_id: kairos_primitives::OrderId::new(order_id)?,
            segment_key: kairos_primitives::SegmentKey::new(segment_key)?,
            provider_instrument: external_instrument_ref(
                crate::domain::ParticipantKind::Exchange,
                "okx",
                row.get("instType").and_then(Value::as_str).unwrap_or("okx"),
                instrument_id,
            )?,
            side: row
                .get("side")
                .and_then(Value::as_str)
                .unwrap_or("buy")
                .into(),
            quantity: decimal(fill_size)?,
            price: decimal(price)?,
            fee_asset: row
                .get("feeCcy")
                .and_then(Value::as_str)
                .map(kairos_primitives::Currency::new)
                .transpose()?,
            fee_amount: row
                .get("fee")
                .and_then(Value::as_str)
                .filter(|value| *value != "0" && !value.is_empty())
                .map(decimal)
                .transpose()?
                .map(abs_decimal),
            occurred_at_unix_nanos: occurred_at_unix_nanos.into(),
        }));
    }
    // The caller supplies the account segment; keep the external fact neutral
    // while retaining the event batch so order state and fills are both seen.
    Ok(Some(AccountEvent::Batch(events)))
}

fn abs_decimal(value: DecimalValue) -> DecimalValue {
    DecimalValue::new(value.mantissa.saturating_abs(), value.scale)
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
        .map(|value| value.as_nanos() as u64)
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::{parse_event, parse_execution_events};
    use crate::application::capabilities::account_facts::{
        ExternalAccountEvent as AccountEvent, ExternalOrderStatus as AccountOrderStatus,
    };
    use crate::application::capabilities::{OrderSide, OrderType};
    use kairos_primitives::{OrderStatus, UnixNanos};

    #[test]
    fn parses_all_matching_execution_rows_into_stable_envelopes() {
        let events = parse_execution_events(
            "execution.okx.swap.trading.swap.cross",
            "execution.okx.swap.trading.swap.cross.order-events",
            4,
            "cross",
            UnixNanos::from(1_800_000_000_000_000_000),
            r#"{"arg":{"channel":"orders"},"data":[{"clOrdId":"local-1","ordId":"88","tradeId":"99","instId":"BTC-USDT-SWAP","tdMode":"cross","side":"buy","ordType":"limit","state":"partially_filled","sz":"2","px":"100.5","accFillSz":"0.25","fillSz":"0.25","fillPx":"100.5","fee":"-0.01","feeCcy":"USDT","uTime":"1700000000000"},{"clOrdId":"local-2","ordId":"89","instId":"ETH-USDT-SWAP","tdMode":"isolated","side":"sell","ordType":"market","state":"live","sz":"1","uTime":"1700000000001"}]}"#,
        )
        .unwrap();

        assert_eq!(events.len(), 1);
        let event = &events[0];
        assert_eq!(event.channel_epoch, 4);
        assert_eq!(event.payload.order_id, "local-1");
        assert_eq!(event.payload.symbol, "BTC-USDT-SWAP");
        assert_eq!(event.payload.status, OrderStatus::PartiallyFilled);
        assert_eq!(event.payload.side, Some(OrderSide::Buy));
        assert_eq!(event.payload.order_type, Some(OrderType::Limit));
        assert_eq!(event.payload.execution_id.as_deref(), Some("okx:99"));
        assert_eq!(event.payload.fee_amount.unwrap().mantissa, 1);
    }

    #[test]
    fn parses_okx_order_event_into_neutral_fact() {
        let event = parse_event(
            "spot",
            r#"{"arg":{"channel":"orders"},"data":[{"clOrdId":"local-1","ordId":"88","state":"filled","accFillSz":"1.25","uTime":"1700000000000"}]}"#,
        )
        .unwrap()
        .unwrap();
        let AccountEvent::Batch(events) = event else {
            panic!("expected event batch")
        };
        let AccountEvent::Order(order) = &events[0] else {
            panic!("expected order")
        };
        assert_eq!(order.status, AccountOrderStatus::Filled);
        assert_eq!(order.remote_order_id.as_deref(), Some("88"));
        assert_eq!(order.filled_quantity.unwrap().mantissa, 125);
    }

    #[test]
    fn parses_okx_fill_and_fee_fact() {
        let event = parse_event(
            "spot",
            r#"{"arg":{"channel":"orders"},"data":[{"clOrdId":"local-1","ordId":"88","tradeId":"99","instId":"BTC-USDT","side":"buy","state":"partially_filled","accFillSz":"1.25","fillSz":"0.25","fillPx":"100.5","fee":"-0.01","feeCcy":"USDT","uTime":"1700000000000"}]}"#,
        )
        .unwrap()
        .unwrap();
        let AccountEvent::Batch(events) = event else {
            panic!("expected event batch")
        };
        let AccountEvent::Fill(fill) = &events[1] else {
            panic!("expected fill event")
        };
        assert_eq!(fill.fill_id, "99");
        assert_eq!(fill.segment_key, "spot");
        assert_eq!(fill.quantity.mantissa, 25);
        assert_eq!(fill.fee_amount.unwrap().mantissa, 1);
    }

    #[test]
    fn parses_okx_account_event_into_snapshot_fact() {
        let event = parse_event(
            "spot",
            r#"{"arg":{"channel":"account"},"data":[{"ccy":"USDT","eq":"10.5","availBal":"9.5","frozenBal":"1","uTime":"1700000000000"}]}"#,
        )
        .unwrap()
        .unwrap();
        let AccountEvent::Snapshot(snapshot) = event else {
            panic!("expected snapshot")
        };
        assert_eq!(snapshot.segment_key, "spot");
        assert_eq!(snapshot.balances[0].total.mantissa, 105);
    }
}
