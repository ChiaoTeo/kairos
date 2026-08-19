use std::time::{SystemTime, UNIX_EPOCH};

use kairos_primitives::{ClientOrderId, OrderId, Symbol, UnixNanos};
use serde_json::Value;

use crate::{
    CommandOutcome, ExternalOrder, IndeterminateCommand, IntegrationError, OrderEntryEvent,
    OrderEntryRequest, OrderEntryStatus, OrderSide, OrderType, ParticipantRejection, TimeInForce,
};

pub(crate) fn normalize_okx_orders(
    connection_key: &crate::ConnectionKey,
    value: &Value,
) -> Result<Vec<ExternalOrder>, String> {
    value
        .get("data")
        .and_then(Value::as_array)
        .ok_or_else(|| "OKX order query data is missing".to_string())?
        .iter()
        .map(|value| normalize_okx_order(connection_key, value))
        .collect()
}
pub(crate) fn normalize_okx_order(
    connection_key: &crate::ConnectionKey,
    value: &Value,
) -> Result<ExternalOrder, String> {
    let text = |key: &str| {
        value
            .get(key)
            .and_then(Value::as_str)
            .map(str::to_owned)
            .filter(|v| !v.is_empty())
    };
    let order_id = text("ordId").ok_or_else(|| "OKX order id is missing".to_string())?;
    let quantity = decimal_field(value, "sz")?;
    let filled_quantity = decimal_field(value, "accFillSz").unwrap_or_default();
    let average_fill_price = decimal_field(value, "avgPx").ok();
    Ok(ExternalOrder {
        connection_key: connection_key.clone(),
        order_id: OrderId::try_from(order_id).map_err(|error| error.to_string())?,
        client_order_id: text("clOrdId")
            .map(ClientOrderId::try_from)
            .transpose()
            .map_err(|error| error.to_string())?,
        symbol: Symbol::try_from(text("instId").unwrap_or_else(|| "UNKNOWN".into()))
            .map_err(|error| error.to_string())?,
        side: if text("side").as_deref() == Some("sell") {
            OrderSide::Sell
        } else {
            OrderSide::Buy
        },
        order_type: if text("ordType").as_deref() == Some("market") {
            OrderType::Market
        } else {
            OrderType::Limit
        },
        status: crate::domain::execution::normalize_order_status(
            &text("state").unwrap_or_else(|| "UNKNOWN".into()),
        ),
        quantity,
        filled_quantity,
        average_fill_price,
        occurred_at_unix_nanos: value
            .get("uTime")
            .and_then(Value::as_str)
            .and_then(|v| v.parse::<u64>().ok())
            .map(|value| UnixNanos::from(value.saturating_mul(1_000))),
    })
}

fn decimal_field(
    value: &Value,
    field: &str,
) -> Result<crate::domain::account::ExternalDecimal, String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("OKX field is missing: {field}"))
        .and_then(crate::domain::account::ExternalDecimal::parse)
}

pub(crate) fn order_request_body(
    request: &OrderEntryRequest,
    trading_mode: &str,
) -> Result<Value, IntegrationError> {
    let mut body = serde_json::Map::from_iter([
        (
            "instId".into(),
            Value::String(symbol(request).map_err(IntegrationError::InvalidRequest)?),
        ),
        ("tdMode".into(), Value::String(trading_mode.into())),
        ("side".into(), Value::String(side(request.side).into())),
        ("ordType".into(), Value::String(order_type(request).into())),
        (
            "sz".into(),
            Value::String(format_entry_decimal(request.quantity)),
        ),
        (
            "clOrdId".into(),
            Value::String(request.order_id.to_string()),
        ),
    ]);
    if let Some(position_side) = &request.options.position_side {
        body.insert("posSide".into(), Value::String(position_side.clone()));
    }
    if let Some(reduce_only) = request.options.reduce_only {
        body.insert("reduceOnly".into(), Value::Bool(reduce_only));
    }
    if let Some(quote_asset) = &request.options.quote_asset {
        body.insert("tgtCcy".into(), Value::String(quote_asset.clone()));
    }
    if let (OrderType::Limit, Some(price)) = (request.order_type, request.limit_price) {
        body.insert("px".into(), Value::String(format_entry_decimal(price)));
    }
    Ok(Value::Object(body))
}

pub(crate) fn normalize_order_submission(
    request: &OrderEntryRequest,
    payload: &Value,
) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
    let Some(row) = payload
        .get("data")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
    else {
        return Ok(CommandOutcome::Indeterminate(
            IndeterminateCommand::may_have_been_sent("OKX order response data is missing"),
        ));
    };
    let code = row.get("sCode").and_then(Value::as_str).unwrap_or("0");
    if code != "0" {
        return Ok(CommandOutcome::Rejected(ParticipantRejection {
            code: Some(code.into()),
            message: row
                .get("sMsg")
                .and_then(Value::as_str)
                .unwrap_or("OKX rejected the order")
                .into(),
            participant_request_id: None,
        }));
    }
    Ok(CommandOutcome::Confirmed(OrderEntryEvent {
        order_id: request.order_id.clone(),
        status: OrderEntryStatus::Accepted,
        remote_order_id: row
            .get("ordId")
            .and_then(Value::as_str)
            .and_then(|value| kairos_primitives::RemoteOrderId::new(value).ok()),
        filled_quantity: None,
        occurred_at_unix_nanos: now_nanos().into(),
        reason: row
            .get("sMsg")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
    }))
}

pub(crate) fn cancel_request_body(
    request: &OrderEntryRequest,
    remote_order_id: &str,
) -> Result<Value, IntegrationError> {
    Ok(serde_json::json!({
        "instId": symbol(request).map_err(IntegrationError::InvalidRequest)?,
        "ordId": remote_order_id,
    }))
}

pub(crate) fn normalize_order_cancellation(
    request: &OrderEntryRequest,
    remote_order_id: &str,
    at_unix_nanos: u64,
    payload: &Value,
) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
    let row = payload
        .get("data")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first());
    let code = row
        .and_then(|value| value.get("sCode"))
        .and_then(Value::as_str)
        .unwrap_or("0");
    if code != "0" {
        return Ok(CommandOutcome::Rejected(ParticipantRejection {
            code: Some(code.into()),
            message: row
                .and_then(|value| value.get("sMsg"))
                .and_then(Value::as_str)
                .unwrap_or("OKX rejected the cancellation")
                .into(),
            participant_request_id: None,
        }));
    }
    Ok(CommandOutcome::Confirmed(OrderEntryEvent {
        order_id: request.order_id.clone(),
        status: OrderEntryStatus::Canceled,
        remote_order_id: kairos_primitives::RemoteOrderId::new(remote_order_id).ok(),
        filled_quantity: None,
        occurred_at_unix_nanos: at_unix_nanos.into(),
        reason: row
            .and_then(|value| value.get("sMsg"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
    }))
}

fn symbol(request: &OrderEntryRequest) -> Result<String, String> {
    if request.participant_instrument.participant.id.as_str() != "okx" {
        return Err(format!(
            "OKX order received provider instrument for {}",
            request.participant_instrument.participant.id
        ));
    }
    Ok(request
        .participant_instrument
        .source_symbol
        .as_str()
        .replace('/', "-")
        .to_ascii_uppercase())
}

fn side(value: OrderSide) -> &'static str {
    match value {
        OrderSide::Buy => "buy",
        OrderSide::Sell => "sell",
    }
}

fn order_type(request: &OrderEntryRequest) -> &'static str {
    match request.options.time_in_force {
        Some(TimeInForce::ImmediateOrCancel) => "ioc",
        Some(TimeInForce::FillOrKill) => "fok",
        _ if request.options.post_only == Some(true) => "post_only",
        _ => match request.order_type {
            OrderType::Market => "market",
            OrderType::Limit => "limit",
            OrderType::Stop => "market",
            OrderType::StopLimit => "limit",
        },
    }
}

fn format_entry_decimal(value: crate::DecimalValue) -> String {
    if value.scale == 0 {
        return value.mantissa.to_string();
    }
    let negative = value.mantissa < 0;
    let digits = value.mantissa.abs().to_string();
    let scale = value.scale as usize;
    let padded = format!(
        "{}{}",
        "0".repeat(scale.saturating_sub(digits.len())),
        digits
    );
    let split = padded.len() - scale;
    format!(
        "{}{}.{}",
        if negative { "-" } else { "" },
        &padded[..split],
        &padded[split..]
    )
}

fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::order_request_body;
    #[test]
    fn order_uses_participant_instrument_instead_of_parsing_market_id() {
        let request = crate::OrderEntryRequest {
            order_id: kairos_primitives::OrderId::new("order-1").unwrap(),
            intent_id: None,
            account_id: kairos_primitives::AccountId::new("main").unwrap(),
            segment_key: kairos_primitives::SegmentKey::new("swap").unwrap(),
            instrument_id: kairos_primitives::InstrumentId::new("instrument:btc-swap").unwrap(),
            market_id: Some(
                kairos_primitives::MarketId::new("market:canonical:not-an-okx-symbol").unwrap(),
            ),
            participant_instrument: crate::domain::ParticipantInstrumentRef::new(
                crate::domain::ParticipantRef::new(crate::domain::ParticipantKind::Exchange, "okx")
                    .unwrap(),
                Some(crate::ParticipantInstrumentTypeRef::new("swap").unwrap()),
                "BTC-USDT-SWAP",
            )
            .unwrap(),
            side: crate::OrderSide::Buy,
            quantity: crate::DecimalValue::new(1, 0),
            order_type: crate::OrderType::Market,
            limit_price: None,
            options: Default::default(),
        };
        let body = order_request_body(&request, "cross").unwrap();
        assert_eq!(body["instId"], "BTC-USDT-SWAP");
    }
}
