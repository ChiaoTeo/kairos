//! JSON control wire records and decoding.

use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::BTreeMap;

#[derive(Debug, Deserialize)]
pub(crate) struct SubscribePayload {
    pub(crate) subject: String,
    pub(crate) selectors: Vec<String>,
    #[serde(default)]
    pub(crate) source_id: Option<String>,
    pub(crate) exchange: Option<String>,
    pub(crate) market_type: Option<String>,
    #[serde(default)]
    pub(crate) asset_type: Option<String>,
    #[serde(default)]
    pub(crate) params: BTreeMap<String, Value>,
    #[serde(default)]
    pub(crate) dynamic: bool,
}

#[derive(Debug, Deserialize)]
pub(crate) struct CommandEnvelope<T> {
    pub(crate) schema_version: u16,
    pub(crate) command_id: String,
    pub(crate) idempotency_key: String,
    pub(crate) operation: String,
    pub(crate) strategy_id: String,
    #[serde(default)]
    pub(crate) launch_id: Option<String>,
    pub(crate) instance_id: String,
    pub(crate) payload: T,
}

#[derive(Debug, Deserialize)]
pub(crate) struct UnsubscribePayload {
    pub(crate) subscription_id: String,
}

#[derive(Debug, Default, Deserialize)]
pub(crate) struct ReleaseOwnerPayload {}

pub(crate) fn parse_subscribe_command(
    body: &str,
) -> Result<CommandEnvelope<SubscribePayload>, serde_json::Error> {
    let raw: Value = serde_json::from_str(body)?;
    if raw.get("scope").is_none() {
        return serde_json::from_value(raw);
    }
    let scope = raw.get("scope").cloned().unwrap_or_default();
    let caller_id = scope
        .get("caller_id")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let instance_id = scope
        .get("instance_id")
        .and_then(Value::as_str)
        .or_else(|| scope.get("market_runtime_id").and_then(Value::as_str))
        .unwrap_or("market");
    serde_json::from_value(json!({
        "schema_version": 2,
        "command_id": raw.get("command_id").and_then(Value::as_str).unwrap_or_default(),
        "idempotency_key": raw.get("idempotency_key").and_then(Value::as_str).unwrap_or_default(),
        "operation": "market.subscribe",
        "strategy_id": caller_id,
        "launch_id": scope.get("launch_id").and_then(Value::as_str),
        "instance_id": instance_id,
        "payload": {
            "subject": raw.get("subject").and_then(Value::as_str).unwrap_or_default(),
            "selectors": raw.get("selectors").cloned().unwrap_or_else(|| json!([])),
            "source_id": raw.get("source_id").cloned().unwrap_or(Value::Null),
            "exchange": raw.get("exchange").cloned().unwrap_or(Value::Null),
            "market_type": raw.get("market_type").cloned().unwrap_or(Value::Null),
            "asset_type": raw.get("asset_type").cloned().unwrap_or(Value::Null),
            "params": raw.get("params").cloned().unwrap_or_else(|| json!({})),
            "dynamic": raw.get("dynamic").cloned().unwrap_or(json!(false)),
        }
    }))
}

pub(crate) fn parse_unsubscribe_command(
    body: &str,
) -> Result<CommandEnvelope<UnsubscribePayload>, serde_json::Error> {
    let raw: Value = serde_json::from_str(body)?;
    if raw.get("scope").is_none() {
        return serde_json::from_value(raw);
    }
    let scope = raw.get("scope").cloned().unwrap_or_default();
    serde_json::from_value(json!({
        "schema_version": 2,
        "command_id": raw.get("command_id").and_then(Value::as_str).unwrap_or_default(),
        "idempotency_key": raw.get("idempotency_key").and_then(Value::as_str).unwrap_or_default(),
        "operation": "market.unsubscribe",
        "strategy_id": scope.get("caller_id").and_then(Value::as_str).unwrap_or_default(),
        "launch_id": scope.get("launch_id").and_then(Value::as_str),
        "instance_id": scope.get("instance_id").and_then(Value::as_str).or_else(|| scope.get("market_runtime_id").and_then(Value::as_str)).unwrap_or("market"),
        "payload": {"subscription_id": raw.get("subscription_id").and_then(Value::as_str).unwrap_or_default()}
    }))
}

pub(crate) fn parse_release_owner_command(
    body: &str,
) -> Result<CommandEnvelope<ReleaseOwnerPayload>, serde_json::Error> {
    let raw: Value = serde_json::from_str(body)?;
    if raw.get("scope").is_none() {
        return serde_json::from_value(raw);
    }
    let scope = raw.get("scope").cloned().unwrap_or_default();
    serde_json::from_value(json!({
        "schema_version": 2,
        "command_id": raw.get("command_id").and_then(Value::as_str).unwrap_or_default(),
        "idempotency_key": raw.get("idempotency_key").and_then(Value::as_str).unwrap_or_default(),
        "operation": "market.release_owner",
        "strategy_id": scope.get("caller_id").and_then(Value::as_str).unwrap_or_default(),
        "launch_id": scope.get("launch_id").and_then(Value::as_str),
        "instance_id": scope.get("instance_id").and_then(Value::as_str).or_else(|| scope.get("market_runtime_id").and_then(Value::as_str)).unwrap_or("market"),
        "payload": {}
    }))
}

pub(crate) fn strategy_subscription_owner(
    launch_id: Option<&str>,
    instance_id: &str,
    strategy_id: &str,
) -> String {
    serde_json::to_string(&json!([
        "strategy",
        launch_id.unwrap_or_default(),
        instance_id,
        strategy_id
    ]))
    .expect("strategy subscription owner is JSON-compatible")
}

pub(crate) fn idempotency_key(body: &str) -> Option<String> {
    json_string_field(body, "idempotency_key").filter(|value| !value.trim().is_empty())
}

pub(crate) fn command_id(body: &str) -> Option<String> {
    json_string_field(body, "command_id")
}

fn json_string_field(body: &str, field: &str) -> Option<String> {
    serde_json::from_str::<Value>(body)
        .ok()?
        .get(field)
        .and_then(Value::as_str)
        .map(str::to_owned)
}
