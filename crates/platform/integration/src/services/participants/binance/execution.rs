use kairos_primitives::execution::{ClientOrderId, OrderId};
use kairos_primitives::integration::RemoteOrderId;
use kairos_primitives::reference::Symbol;
use kairos_primitives::time::UnixNanos;
use serde_json::Value;

use crate::{
    CommandOutcome, DecimalValue, ExternalOrder, IndeterminateCommand, IntegrationError,
    OrderEntryEvent, OrderEntryRequest, OrderEntryStatus, OrderSide, OrderType,
    ParticipantRejection,
};
pub(crate) fn submitted_outcome(
    request: &OrderEntryRequest,
    outcome: CommandOutcome<Value>,
) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
    match outcome {
        CommandOutcome::Confirmed(value) => submitted(request, &value),
        CommandOutcome::Rejected(error) => Ok(CommandOutcome::Rejected(error)),
        CommandOutcome::Indeterminate(error) => Ok(CommandOutcome::Indeterminate(error)),
    }
}
pub(crate) fn canceled_outcome(
    request: &OrderEntryRequest,
    remote: &str,
    at: u64,
    outcome: CommandOutcome<Value>,
) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
    match outcome {
        CommandOutcome::Confirmed(value) => canceled(request, remote, at, &value),
        CommandOutcome::Rejected(error) => Ok(CommandOutcome::Rejected(error)),
        CommandOutcome::Indeterminate(error) => Ok(CommandOutcome::Indeterminate(error)),
    }
}
pub(crate) fn params(
    request: &OrderEntryRequest,
) -> Result<Vec<(&'static str, String)>, IntegrationError> {
    let mut p = vec![
        (
            "symbol",
            request.participant_instrument.source_symbol.as_str().into(),
        ),
        (
            "side",
            if request.side == OrderSide::Buy {
                "BUY".into()
            } else {
                "SELL".into()
            },
        ),
        (
            "type",
            match request.order_type {
                OrderType::Market => "MARKET",
                OrderType::Limit => "LIMIT",
                OrderType::Stop => "STOP_LOSS",
                OrderType::StopLimit => "STOP_LOSS_LIMIT",
            }
            .into(),
        ),
        ("quantity", decimal(request.quantity)),
        ("newClientOrderId", request.order_id.to_string()),
    ];
    if let Some(price) = request.limit_price {
        p.push(("price", decimal(price)));
    }
    if matches!(request.order_type, OrderType::Limit | OrderType::StopLimit) {
        p.push((
            "timeInForce",
            match request
                .options
                .time_in_force
                .unwrap_or(crate::TimeInForce::GoodTilCanceled)
            {
                crate::TimeInForce::GoodTilCanceled => "GTC",
                crate::TimeInForce::ImmediateOrCancel => "IOC",
                crate::TimeInForce::FillOrKill => "FOK",
                crate::TimeInForce::Day => "GTC",
            }
            .into(),
        ));
    }
    if let Some(value) = request.options.reduce_only {
        p.push(("reduceOnly", value.to_string()));
    }
    if let Some(value) = &request.options.position_side {
        p.push(("positionSide", value.clone()));
    }
    Ok(p)
}

pub(crate) fn amend_params(
    request: &crate::participants::binance::BinanceAmendOrderRequest,
) -> Result<Vec<(&'static str, String)>, IntegrationError> {
    let replacement = &request.replacement;
    if replacement.order_type != OrderType::Limit {
        return Err(IntegrationError::UnsupportedOperation);
    }
    let price = replacement.limit_price.ok_or_else(|| {
        IntegrationError::InvalidRequest("Binance amend requires a limit price".into())
    })?;
    Ok(vec![
        (
            "symbol",
            replacement.participant_instrument.source_symbol.to_string(),
        ),
        ("orderId", request.remote_order_id.clone()),
        (
            "side",
            if replacement.side == OrderSide::Buy {
                "BUY".into()
            } else {
                "SELL".into()
            },
        ),
        ("quantity", decimal(replacement.quantity)),
        ("price", decimal(price)),
    ])
}

pub(crate) fn batch_order_parameter(
    requests: &[OrderEntryRequest],
) -> Result<String, IntegrationError> {
    if !(1..=5).contains(&requests.len()) {
        return Err(IntegrationError::InvalidRequest(
            "Binance submit batch must contain 1-5 orders".into(),
        ));
    }
    let rows = requests
        .iter()
        .map(|request| {
            params(request).map(|params| {
                serde_json::Value::Object(
                    params
                        .into_iter()
                        .map(|(key, value)| (key.into(), Value::String(value)))
                        .collect(),
                )
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    serde_json::to_string(&rows)
        .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))
}

pub(crate) fn cancel_id_parameter(
    requests: &[crate::participants::binance::BinanceCancelOrderRequest],
) -> Result<String, IntegrationError> {
    if !(1..=10).contains(&requests.len()) {
        return Err(IntegrationError::InvalidRequest(
            "Binance cancel batch must contain 1-10 orders".into(),
        ));
    }
    let values = requests
        .iter()
        .map(|request| {
            request.remote_order_id.parse::<u64>().map_err(|_| {
                IntegrationError::InvalidRequest("invalid Binance remote order id".into())
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    serde_json::to_string(&values)
        .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))
}

pub(crate) fn submitted_batch_outcome(
    requests: &[OrderEntryRequest],
    outcome: CommandOutcome<Value>,
) -> Result<CommandOutcome<Vec<CommandOutcome<OrderEntryEvent>>>, IntegrationError> {
    match outcome {
        CommandOutcome::Rejected(error) => Ok(CommandOutcome::Rejected(error)),
        CommandOutcome::Indeterminate(error) => Ok(CommandOutcome::Indeterminate(error)),
        CommandOutcome::Confirmed(value) => {
            let rows = value.as_array();
            Ok(CommandOutcome::Confirmed(
                requests
                    .iter()
                    .enumerate()
                    .map(
                        |(index, request)| match rows.and_then(|rows| rows.get(index)) {
                            Some(row) => submitted(request, row),
                            None => Ok(CommandOutcome::Indeterminate(
                                IndeterminateCommand::may_have_been_sent(format!(
                                    "Binance batch submit response item {index} is missing"
                                )),
                            )),
                        },
                    )
                    .collect::<Result<Vec<_>, _>>()?,
            ))
        },
    }
}

pub(crate) fn canceled_batch_outcome(
    requests: &[crate::participants::binance::BinanceCancelOrderRequest],
    outcome: CommandOutcome<Value>,
) -> Result<CommandOutcome<Vec<CommandOutcome<OrderEntryEvent>>>, IntegrationError> {
    match outcome {
        CommandOutcome::Rejected(error) => Ok(CommandOutcome::Rejected(error)),
        CommandOutcome::Indeterminate(error) => Ok(CommandOutcome::Indeterminate(error)),
        CommandOutcome::Confirmed(value) => {
            let rows = value.as_array();
            Ok(CommandOutcome::Confirmed(
                requests
                    .iter()
                    .enumerate()
                    .map(
                        |(index, request)| match rows.and_then(|rows| rows.get(index)) {
                            Some(row) => canceled(
                                &request.order,
                                &request.remote_order_id,
                                request.at_unix_nanos,
                                row,
                            ),
                            None => Ok(CommandOutcome::Indeterminate(
                                IndeterminateCommand::may_have_been_sent(format!(
                                    "Binance batch cancel response item {index} is missing"
                                )),
                            )),
                        },
                    )
                    .collect::<Result<Vec<_>, _>>()?,
            ))
        },
    }
}
pub(crate) fn submitted(
    request: &OrderEntryRequest,
    value: &Value,
) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
    if let Some(code) = value.get("code").and_then(Value::as_i64).filter(|v| *v < 0) {
        return Ok(CommandOutcome::Rejected(ParticipantRejection {
            code: Some(code.to_string()),
            message: value
                .get("msg")
                .and_then(Value::as_str)
                .unwrap_or("Binance rejected order")
                .into(),
            participant_request_id: None,
        }));
    }
    let Some(remote_order_id) = response_remote_order_id(value) else {
        return Ok(CommandOutcome::Indeterminate(
            IndeterminateCommand::may_have_been_sent(
                "Binance accepted-order response is missing a valid orderId",
            ),
        ));
    };
    Ok(CommandOutcome::Confirmed(OrderEntryEvent {
        order_id: request.order_id.clone(),
        status: OrderEntryStatus::Accepted,
        remote_order_id: Some(remote_order_id),
        filled_quantity: None,
        occurred_at_unix_nanos: now(),
        reason: String::new(),
    }))
}
pub(crate) fn canceled(
    request: &OrderEntryRequest,
    remote: &str,
    at: u64,
    value: &Value,
) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
    if let Some(code) = value.get("code").and_then(Value::as_i64).filter(|v| *v < 0) {
        return Ok(CommandOutcome::Rejected(ParticipantRejection {
            code: Some(code.to_string()),
            message: value
                .get("msg")
                .and_then(Value::as_str)
                .unwrap_or("Binance rejected cancel")
                .into(),
            participant_request_id: None,
        }));
    }
    let Some(response_remote_order_id) = response_remote_order_id(value) else {
        return Ok(CommandOutcome::Indeterminate(
            IndeterminateCommand::may_have_been_sent(
                "Binance cancel response is missing a valid orderId",
            ),
        ));
    };
    if response_remote_order_id.as_str() != remote {
        return Ok(CommandOutcome::Indeterminate(
            IndeterminateCommand::may_have_been_sent(format!(
                "Binance cancel response orderId {} does not match requested {remote}",
                response_remote_order_id.as_str()
            )),
        ));
    }
    Ok(CommandOutcome::Confirmed(OrderEntryEvent {
        order_id: request.order_id.clone(),
        status: OrderEntryStatus::Canceled,
        remote_order_id: Some(response_remote_order_id),
        filled_quantity: None,
        occurred_at_unix_nanos: at.into(),
        reason: String::new(),
    }))
}

fn response_remote_order_id(value: &Value) -> Option<RemoteOrderId> {
    value
        .get("orderId")
        .and_then(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .or_else(|| value.as_u64().map(|value| value.to_string()))
        })
        .and_then(|value| RemoteOrderId::new(value).ok())
}
pub(crate) fn orders(
    connection_key: &crate::ConnectionKey,
    value: &Value,
) -> Result<Vec<ExternalOrder>, IntegrationError> {
    let rows = if let Some(rows) = value.as_array() {
        rows.clone()
    } else {
        vec![value.clone()]
    };
    rows.iter().map(|row| order(connection_key, row)).collect()
}
fn order(
    connection_key: &crate::ConnectionKey,
    row: &Value,
) -> Result<ExternalOrder, IntegrationError> {
    let text = |f: &str| row.get(f).and_then(Value::as_str).filter(|v| !v.is_empty());
    let id = row
        .get("orderId")
        .and_then(Value::as_u64)
        .map(|v| v.to_string())
        .or_else(|| text("orderId").map(str::to_owned))
        .ok_or_else(|| IntegrationError::InvalidPayload("Binance order id missing".into()))?;
    Ok(ExternalOrder {
        connection_key: connection_key.clone(),
        order_id: OrderId::new(text("clientOrderId").unwrap_or(&id)).map_err(payload)?,
        remote_order_id: RemoteOrderId::new(id).map_err(payload)?,
        client_order_id: text("clientOrderId")
            .map(ClientOrderId::new)
            .transpose()
            .map_err(payload)?,
        symbol: Symbol::new(text("symbol").unwrap_or("UNKNOWN")).map_err(payload)?,
        side: if text("side") == Some("SELL") {
            OrderSide::Sell
        } else {
            OrderSide::Buy
        },
        order_type: if text("type") == Some("MARKET") {
            OrderType::Market
        } else {
            OrderType::Limit
        },
        status: crate::domain::execution::normalize_order_status(
            text("status").unwrap_or("UNKNOWN"),
        ),
        quantity: parse(text("origQty").ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance original quantity is missing".into())
        })?)?,
        filled_quantity: parse(text("executedQty").ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance executed quantity is missing".into())
        })?)?,
        average_fill_price: text("avgPrice")
            .or_else(|| text("price"))
            .map(parse)
            .transpose()?,
        occurred_at_unix_nanos: row
            .get("updateTime")
            .or_else(|| row.get("time"))
            .and_then(Value::as_u64)
            .map(|v| UnixNanos::from(v.saturating_mul(1_000_000))),
    })
}
pub(crate) fn decimal(v: DecimalValue) -> String {
    if v.scale == 0 {
        return v.mantissa.to_string();
    }
    let neg = v.mantissa < 0;
    let digits = v.mantissa.abs().to_string();
    let scale = v.scale as usize;
    let padded = if digits.len() <= scale {
        format!("{}{}", "0".repeat(scale + 1 - digits.len()), digits)
    } else {
        digits
    };
    let split = padded.len() - scale;
    format!(
        "{}{}.{}",
        if neg { "-" } else { "" },
        &padded[..split],
        &padded[split..]
    )
}

#[cfg(test)]
mod native_order_tests {
    use super::{amend_params, batch_order_parameter, cancel_id_parameter, canceled, submitted};
    use super::{canceled_batch_outcome, submitted_batch_outcome};

    fn order(id: &str) -> crate::OrderEntryRequest {
        crate::OrderEntryRequest {
            order_id: kairos_primitives::execution::OrderId::new(id).unwrap(),
            intent_id: None,
            account_id: kairos_primitives::account::AccountId::new("main").unwrap(),
            segment_key: kairos_primitives::account::SegmentKey::new("usdm").unwrap(),
            instrument_id: kairos_primitives::reference::InstrumentId::new("btc-perp").unwrap(),
            market_id: None,
            participant_instrument: crate::ParticipantInstrumentRef::new(
                crate::ParticipantRef::new(crate::ParticipantKind::Exchange, "binance").unwrap(),
                Some(crate::ParticipantInstrumentTypeRef::new("perpetual").unwrap()),
                "BTCUSDT",
            )
            .unwrap(),
            side: crate::OrderSide::Buy,
            quantity: crate::DecimalValue::new(2, 0),
            order_type: crate::OrderType::Limit,
            limit_price: Some(crate::DecimalValue::new(50_000, 0)),
            options: Default::default(),
        }
    }

    #[test]
    fn futures_amend_sends_full_required_replacement_state() {
        let params = amend_params(&crate::participants::binance::BinanceAmendOrderRequest {
            replacement: order("order-1"),
            remote_order_id: "42".into(),
        })
        .unwrap();
        assert!(params.contains(&("symbol", "BTCUSDT".into())));
        assert!(params.contains(&("orderId", "42".into())));
        assert!(params.contains(&("side", "BUY".into())));
        assert!(params.contains(&("quantity", "2".into())));
        assert!(params.contains(&("price", "50000".into())));
    }

    #[test]
    fn futures_batch_limits_and_remote_ids_are_explicit() {
        let orders = vec![order("order-1"), order("order-2")];
        let encoded = batch_order_parameter(&orders).unwrap();
        let rows: serde_json::Value = serde_json::from_str(&encoded).unwrap();
        assert_eq!(rows.as_array().unwrap().len(), 2);

        let cancels = orders
            .into_iter()
            .enumerate()
            .map(
                |(index, order)| crate::participants::binance::BinanceCancelOrderRequest {
                    order,
                    remote_order_id: (index + 1).to_string(),
                    at_unix_nanos: 1,
                },
            )
            .collect::<Vec<_>>();
        assert_eq!(cancel_id_parameter(&cancels).unwrap(), "[1,2]");
        assert!(batch_order_parameter(&[]).is_err());
    }

    #[test]
    fn submit_requires_a_valid_remote_order_identity() {
        let outcome = submitted(&order("order-1"), &serde_json::json!({})).unwrap();
        assert!(matches!(outcome, crate::CommandOutcome::Indeterminate(_)));

        let outcome = submitted(&order("order-1"), &serde_json::json!({"orderId": 42})).unwrap();
        let crate::CommandOutcome::Confirmed(event) = outcome else {
            panic!("valid Binance acknowledgement must be confirmed");
        };
        assert_eq!(event.remote_order_id.unwrap().as_str(), "42");
    }

    #[test]
    fn cancel_requires_a_matching_remote_order_identity() {
        let request = order("order-1");
        assert!(matches!(
            canceled(&request, "42", 1, &serde_json::json!({})).unwrap(),
            crate::CommandOutcome::Indeterminate(_)
        ));
        assert!(matches!(
            canceled(&request, "42", 1, &serde_json::json!({"orderId": 43})).unwrap(),
            crate::CommandOutcome::Indeterminate(_)
        ));
        assert!(matches!(
            canceled(&request, "42", 1, &serde_json::json!({"orderId": 42})).unwrap(),
            crate::CommandOutcome::Confirmed(_)
        ));
    }

    #[test]
    fn submit_and_cancel_preserve_explicit_rejection() {
        let request = order("order-1");
        let rejection = serde_json::json!({"code": -2010, "msg": "rejected"});
        assert!(matches!(
            submitted(&request, &rejection).unwrap(),
            crate::CommandOutcome::Rejected(_)
        ));
        assert!(matches!(
            canceled(&request, "42", 1, &rejection).unwrap(),
            crate::CommandOutcome::Rejected(_)
        ));
    }

    #[test]
    fn batch_outcomes_preserve_each_items_delivery_evidence() {
        let requests = vec![order("order-1"), order("order-2")];
        let crate::CommandOutcome::Confirmed(submissions) = submitted_batch_outcome(
            &requests,
            crate::CommandOutcome::Confirmed(serde_json::json!([
                {"orderId": 1},
                {"code": -2010, "msg": "rejected"}
            ])),
        )
        .unwrap() else {
            panic!("batch transport acknowledgement must preserve item outcomes");
        };
        assert!(matches!(
            submissions[0],
            crate::CommandOutcome::Confirmed(_)
        ));
        assert!(matches!(submissions[1], crate::CommandOutcome::Rejected(_)));

        let cancels = requests
            .into_iter()
            .enumerate()
            .map(
                |(index, order)| crate::participants::binance::BinanceCancelOrderRequest {
                    order,
                    remote_order_id: (index + 1).to_string(),
                    at_unix_nanos: 1,
                },
            )
            .collect::<Vec<_>>();
        let crate::CommandOutcome::Confirmed(cancellations) = canceled_batch_outcome(
            &cancels,
            crate::CommandOutcome::Confirmed(serde_json::json!([{"orderId": 1}])),
        )
        .unwrap() else {
            panic!("batch transport acknowledgement must preserve item outcomes");
        };
        assert!(matches!(
            cancellations[0],
            crate::CommandOutcome::Confirmed(_)
        ));
        assert!(matches!(
            cancellations[1],
            crate::CommandOutcome::Indeterminate(_)
        ));
    }
}
fn parse(value: &str) -> Result<DecimalValue, IntegrationError> {
    DecimalValue::parse(value).map_err(payload)
}
fn payload(error: impl std::fmt::Display) -> IntegrationError {
    IntegrationError::InvalidPayload(error.to_string())
}
fn now() -> UnixNanos {
    use std::time::{SystemTime, UNIX_EPOCH};
    let n = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|v| v.as_nanos())
        .unwrap_or_default();
    UnixNanos::from(u64::try_from(n).unwrap_or(u64::MAX))
}
