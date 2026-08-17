//! JSON control-wire parsing and compatibility mapping.

use super::*;
use kairos_workspace::runtime::STOP_PATH;
use serde_json::{json, Value};

#[derive(serde::Deserialize)]
struct CommandEnvelope<T> {
    schema_version: u16,
    operation: String,
    instance_id: String,
    payload: T,
}

#[derive(serde::Deserialize)]
struct ReplaceOrderPatchWire {
    #[serde(default)]
    quantity: Option<Quantity>,
    #[serde(default)]
    options: ExecutionOrderOptions,
}

#[cfg(test)]
pub(crate) fn request_class(method: &str, target: &str) -> RequestClass {
    let path = target.split_once('?').map_or(target, |(path, _)| path);
    match path {
        "/v1/intents/cancel"
        | "/v1/intents/expire"
        | "/v1/intents/refresh-quote"
        | "/v1/fill"
        | "/v1/link-unknown-remote"
        | "/v1/reconciliation"
        | STOP_PATH => RequestClass::Command,
        "/v1/intents" if method == "POST" => RequestClass::Command,
        "/v1/orders" if method == "POST" => RequestClass::Command,
        path if method == "DELETE" && path.starts_with("/v1/orders/") => RequestClass::Command,
        path if method == "PATCH" && path.starts_with("/v1/orders/") => RequestClass::Command,
        _ => RequestClass::Query,
    }
}

pub(super) fn parse_operation(
    method: &str,
    target: &str,
    body: &[u8],
) -> Result<ControlOperation, (u16, Value)> {
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    if method == "GET" && path == "/v1/routes" {
        let parse = |key: &str| query_value(query, key);
        return Ok(ControlOperation::AvailableRoutes(ExecutionRouteQuery {
            account_id: parse("account_id")
                .map(kairos_primitives::AccountId::new)
                .transpose()
                .map_err(|error| (422, json!({"error": error.to_string()})))?,
            segment_key: parse("segment_key")
                .map(kairos_primitives::SegmentKey::new)
                .transpose()
                .map_err(|error| (422, json!({"error": error.to_string()})))?,
            instrument_id: parse("instrument_id")
                .map(kairos_primitives::InstrumentId::new)
                .transpose()
                .map_err(|error| (422, json!({"error": error.to_string()})))?,
            market_id: parse("market_id")
                .map(kairos_primitives::MarketId::new)
                .transpose()
                .map_err(|error| (422, json!({"error": error.to_string()})))?,
            order_type: parse("order_type")
                .map(|value| match value.to_ascii_lowercase().as_str() {
                    "market" => Ok(crate::application::OrderType::Market),
                    "limit" => Ok(crate::application::OrderType::Limit),
                    _ => Err((
                        422,
                        json!({"error": format!("unsupported order_type: {value}")}),
                    )),
                })
                .transpose()?,
            required_options: parse("options")
                .map(|value| {
                    value
                        .split(',')
                        .filter(|item| !item.is_empty())
                        .map(str::to_owned)
                        .collect()
                })
                .unwrap_or_default(),
        }));
    }
    if method == "GET" && path != kairos_workspace::runtime::HEALTH_PATH {
        return Err((
            405,
            json!({"error":"REST business queries are disabled; read the typed mmap view"}),
        ));
    }
    if path == kairos_workspace::runtime::HEALTH_PATH {
        return if method == "GET" {
            Ok(ControlOperation::Health)
        } else {
            Err((405, json!({"error":"health only supports GET"})))
        };
    }
    if method != "POST" && method != "DELETE" && method != "PATCH" {
        return Err((
            405,
            json!({"error":"Execution REST accepts only control commands"}),
        ));
    }
    let body =
        std::str::from_utf8(body).map_err(|error| (422, json!({"error": error.to_string()})))?;
    let invalid = |error: String| (422, json!({"error": error}));
    match (method, path) {
        ("POST", "/v1/time/advance") => {
            let value: Value =
                serde_json::from_str(body).map_err(|error| invalid(error.to_string()))?;
            let at = value
                .get("event_time_unix_nanos")
                .and_then(Value::as_u64)
                .ok_or_else(|| invalid("event_time_unix_nanos is required".into()))?;
            Ok(ControlOperation::AdvanceTime(at))
        }
        ("POST", "/v1/intents") => v2_submit_intent(body)
            .map(|(intent, idempotency_key)| ControlOperation::SubmitIntent {
                intent,
                idempotency_key,
            })
            .map_err(invalid),
        ("POST", "/v1/orders") => serde_json::from_str(body)
            .map(ControlOperation::SubmitOrder)
            .map_err(|error| invalid(error.to_string())),
        ("DELETE", path) if path.starts_with("/v1/orders/") => {
            let order_id = path.trim_start_matches("/v1/orders/");
            if order_id.is_empty() {
                return Err((404, json!({"error":"order id is required"})));
            }
            let reason = serde_json::from_str::<Value>(body)
                .ok()
                .and_then(|value| {
                    value
                        .get("reason")
                        .and_then(Value::as_str)
                        .map(str::to_owned)
                })
                .unwrap_or_default();
            Ok(ControlOperation::CancelOrder(CancelOrder {
                order_id: OrderId::new(order_id).map_err(|error| invalid(error.to_string()))?,
                reason,
            }))
        }
        ("PATCH", path) if path.starts_with("/v1/orders/") => {
            let order_id = path.trim_start_matches("/v1/orders/");
            if order_id.is_empty() {
                return Err((404, json!({"error":"order id is required"})));
            }
            let value: Value =
                serde_json::from_str(body).map_err(|error| invalid(error.to_string()))?;
            let patch: ReplaceOrderPatchWire = serde_json::from_value(value.clone())
                .map_err(|error| invalid(error.to_string()))?;
            let limit_price = value
                .get("limit_price")
                .map(|value| serde_json::from_value::<Option<Price>>(value.clone()))
                .transpose()
                .map_err(|error| invalid(error.to_string()))?;
            Ok(ControlOperation::ReplaceOrder {
                order_id: OrderId::new(order_id).map_err(|error| invalid(error.to_string()))?,
                patch: ReplaceOrderPatch {
                    quantity: patch.quantity,
                    limit_price,
                    options: patch.options,
                },
            })
        }
        ("POST", "/v1/reconciliation") => {
            let request: kairos_execution_contract::control::ReconcileExecutionRequest =
                serde_json::from_str(body).map_err(|error| invalid(error.to_string()))?;
            let order_id = request
                .order_id
                .map(OrderId::new)
                .transpose()
                .map_err(|error| invalid(error.to_string()))?;
            Ok(ControlOperation::Reconcile(RemoteOrderQuery {
                order_id,
                ..RemoteOrderQuery::default()
            }))
        }
        ("POST", "/v1/link-unknown-remote") => Ok(ControlOperation::LinkUnknownRemote {
            remote_order_id: query_value(query, "remote_order_id").unwrap_or_default(),
            local_order_id: query_value(query, "local_order_id").unwrap_or_default(),
        }),
        ("POST", "/v1/backtest") => serde_json::from_str(body)
            .map(ControlOperation::EvaluateBacktest)
            .map_err(|error| invalid(error.to_string())),
        ("POST", "/v1/backtest/run") => serde_json::from_str(body)
            .map(ControlOperation::RunBacktest)
            .map_err(|error| invalid(error.to_string())),
        ("POST", "/v1/backtest/market") => serde_json::from_str(body)
            .map(ControlOperation::ApplyBacktestMarket)
            .map_err(|error| invalid(error.to_string())),
        ("POST", "/v1/intents/cancel") => serde_json::from_str(body)
            .map(ControlOperation::CancelIntent)
            .map_err(|error| invalid(error.to_string())),
        ("POST", "/v1/intents/expire") => serde_json::from_str(body)
            .map(ControlOperation::ExpireIntent)
            .map_err(|error| invalid(error.to_string())),
        ("POST", "/v1/intents/refresh-quote") => {
            let command: CommandEnvelope<RefreshQuoteIntent> =
                serde_json::from_str(body).map_err(|error| invalid(error.to_string()))?;
            if command.schema_version != 1
                || command.operation != "execution.refresh_quote"
                || command.instance_id.trim().is_empty()
            {
                return Err(invalid("invalid quote refresh command envelope".into()));
            }
            Ok(ControlOperation::RefreshQuote(command.payload))
        }
        ("POST", "/v1/preview-submit") => serde_json::from_str(body)
            .map(ControlOperation::PreviewSubmit)
            .map_err(|error| invalid(error.to_string())),
        ("POST", "/v1/fill") => serde_json::from_str(body)
            .map(ControlOperation::RecordFill)
            .map_err(|error| invalid(error.to_string())),
        ("POST", STOP_PATH) => Ok(ControlOperation::Stop),
        _ => Err((404, json!({"error":"unknown execution control path"}))),
    }
}

pub(crate) fn query_value(query: &str, key: &str) -> Option<String> {
    query
        .split('&')
        .find_map(|part| part.strip_prefix(&format!("{key}=")).map(str::to_owned))
}
pub(crate) fn v2_submit_intent(body: &str) -> Result<(ExecuteStrategyIntent, String), String> {
    let request: Value = serde_json::from_str(body).map_err(|error| error.to_string())?;
    let intent = request
        .get("intent")
        .cloned()
        .ok_or_else(|| "intent is required".to_owned())?;
    let intent_object = intent
        .as_object()
        .ok_or_else(|| "intent must be an object".to_owned())?;
    let idempotency_key = request
        .get("idempotency_key")
        .or_else(|| request.get("command_id"))
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| "idempotency_key or command_id is required".to_owned())?
        .to_owned();
    let legs: Vec<Value> = match intent_object.get("legs").and_then(Value::as_array) {
        Some(legs) if !legs.is_empty() => legs.clone(),
        _ => vec![json!({
            "leg_id": format!("{}:leg", intent_object.get("intent_id").and_then(Value::as_str).unwrap_or("intent")),
            "account_id": intent_object
                .get("account_ids")
                .and_then(Value::as_array)
                .and_then(|accounts| accounts.first())
                .cloned()
                .unwrap_or_else(|| Value::String("main".to_owned())),
            "segment_key": intent_object.get("segment_key").cloned().unwrap_or_else(|| Value::String("spot".to_owned())),
            "instrument_id": intent_object.get("instrument_id").cloned().ok_or_else(|| "intent instrument_id is required".to_owned())?,
            "market_id": intent_object.get("market_id").cloned().unwrap_or(Value::Null),
            "side": "buy",
            "quantity": intent_object.get("target_quantity").cloned().ok_or_else(|| "intent target_quantity is required".to_owned())?,
            "quantity_semantics": "target_position",
            "limit_price": intent_object.get("limit_price").cloned().unwrap_or(Value::Null),
            "options": intent_object.get("order_options").cloned().unwrap_or_else(|| json!({})),
        })],
    };
    let first = legs[0]
        .as_object()
        .ok_or_else(|| "intent leg must be an object".to_owned())?;
    let account_ids: Vec<Value> = legs
        .iter()
        .map(|leg| {
            leg.get("account_id")
                .cloned()
                .ok_or_else(|| "intent leg account_id is required".to_owned())
        })
        .collect::<Result<_, _>>()?;
    let legacy_legs: Vec<Value> = legs
        .iter()
        .map(|leg| {
            let leg = leg
                .as_object()
                .ok_or_else(|| "intent leg must be an object".to_owned())?;
            let mut value = leg.clone();
            let quantity_semantics = leg
                .get("quantity_semantics")
                .and_then(Value::as_str)
                .unwrap_or("order_quantity");
            value.insert(
                "target_position".to_owned(),
                Value::Bool(quantity_semantics.replace('_', "-") == "target-position"),
            );
            value.insert(
                "options".to_owned(),
                leg.get("options").cloned().unwrap_or_else(|| json!({})),
            );
            Ok(Value::Object(value))
        })
        .collect::<Result<_, String>>()?;
    let mut legacy = intent_object.clone();
    legacy.insert(
        "instrument_id".to_owned(),
        first
            .get("instrument_id")
            .cloned()
            .ok_or_else(|| "intent leg instrument_id is required".to_owned())?,
    );
    legacy.insert(
        "market_id".to_owned(),
        first.get("market_id").cloned().unwrap_or(Value::Null),
    );
    legacy.insert("account_ids".to_owned(), Value::Array(account_ids));
    legacy.insert(
        "segment_key".to_owned(),
        first
            .get("segment_key")
            .cloned()
            .unwrap_or_else(|| Value::String("spot".to_owned())),
    );
    legacy.insert(
        "target_quantity".to_owned(),
        first
            .get("quantity")
            .cloned()
            .ok_or_else(|| "intent leg quantity is required".to_owned())?,
    );
    legacy.insert(
        "limit_price".to_owned(),
        first.get("limit_price").cloned().unwrap_or(Value::Null),
    );
    legacy.insert("source_snapshot_id".to_owned(), Value::Null);
    legacy.insert("source_event_sequence".to_owned(), Value::Null);
    legacy.insert("source_event_time_unix_nanos".to_owned(), Value::Null);
    legacy.insert(
        "order_options".to_owned(),
        first.get("options").cloned().unwrap_or_else(|| json!({})),
    );
    legacy.insert(
        "reason".to_owned(),
        intent_object
            .get("reason")
            .cloned()
            .unwrap_or_else(|| Value::String(String::new())),
    );
    legacy.insert("legs".to_owned(), Value::Array(legacy_legs));
    let intent =
        serde_json::from_value(Value::Object(legacy)).map_err(|error| error.to_string())?;
    Ok((intent, idempotency_key))
}
