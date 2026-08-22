use std::path::{Path, PathBuf};

use kairos_workspace::Workspace;
use serde_json::Value;

use crate::application::{CancelOrder, OrderType, ReplaceOrder, SubmitOrder};

/// Standalone Execution CLI facade.
///
/// This facade is reserved for future direct order-entry previews/actions and
/// local evidence inspection. It must not operate a running Execution server
/// or read runtime mmap projections.
pub struct CliExecutionApplication {
    workspace_root: PathBuf,
}

impl CliExecutionApplication {
    pub fn open(workspace: &Workspace) -> Self {
        Self {
            workspace_root: workspace.root().to_path_buf(),
        }
    }

    pub fn workspace_root(&self) -> &Path {
        &self.workspace_root
    }

    pub fn audit_file(
        &self,
        file: &Path,
        order_id: Option<&str>,
        remote_order_id: Option<&str>,
        status: Option<&str>,
        limit: Option<usize>,
    ) -> Result<Value, String> {
        let mut events = read_collection(file, "events")?
            .into_iter()
            .filter(|value| field_matches(value, "order_id", order_id))
            .filter(|value| field_matches(value, "remote_order_id", remote_order_id))
            .filter(|value| field_matches(value, "status", status))
            .collect::<Vec<_>>();
        if let Some(limit) = limit {
            events.truncate(limit);
        }
        Ok(serde_json::json!({
            "owner": "execution",
            "mode": "standalone",
            "source": "local_evidence_file",
            "file": file.display().to_string(),
            "events": events,
        }))
    }

    pub fn journal_file(&self, file: &Path, order_id: &str) -> Result<Value, String> {
        self.audit_file(file, Some(order_id), None, None, None)
    }

    pub fn inspect_file(&self, file: &Path, order_id: &str) -> Result<Value, String> {
        let orders = read_collection(file, "orders")?;
        let order = orders
            .into_iter()
            .find(|value| string_field(value, "order_id").is_some_and(|value| value == order_id))
            .ok_or_else(|| format!("order not found in local evidence file: {order_id}"))?;
        Ok(serde_json::json!({
            "owner": "execution",
            "mode": "standalone",
            "source": "local_evidence_file",
            "file": file.display().to_string(),
            "order": order,
        }))
    }

    pub fn fills_file(&self, file: &Path, order_id: Option<&str>) -> Result<Value, String> {
        let fills = read_collection(file, "fills")?
            .into_iter()
            .filter(|value| field_matches(value, "order_id", order_id))
            .collect::<Vec<_>>();
        Ok(serde_json::json!({
            "owner": "execution",
            "mode": "standalone",
            "source": "local_evidence_file",
            "file": file.display().to_string(),
            "fills": fills,
        }))
    }

    pub fn preview_submit(&self, request: SubmitOrder) -> Result<Value, String> {
        let mut issues = Vec::new();
        if request.execution_route_id.is_none() {
            issues.push("execution_route_id is required before a real submit action".to_owned());
        }
        if matches!(request.order_type, OrderType::Limit) && request.limit_price.is_none() {
            issues.push("limit order requires limit_price".to_owned());
        }
        Ok(serde_json::json!({
            "owner": "execution",
            "mode": "standalone",
            "command": "preview-submit",
            "effect": "dry_run",
            "connects_server": false,
            "submits_order": false,
            "source": "cli_arguments",
            "valid_for_preview": issues.is_empty(),
            "issues": issues,
            "request": request,
            "next_steps": [
                "standalone direct submit requires an Execution-owned provider action service",
                "runtime submit must use launch instance component execution submit"
            ],
        }))
    }

    pub fn preview_submit_file(&self, file: &Path) -> Result<Value, String> {
        let request = read_typed_file::<SubmitOrder>(file)?;
        Ok(mark_typed_file(self.preview_submit(request)?, file))
    }

    pub fn preview_cancel(&self, request: CancelOrder) -> Result<Value, String> {
        let mut issues = Vec::new();
        if request.reason.trim().is_empty() {
            issues.push("cancel reason is empty".to_owned());
        }
        Ok(serde_json::json!({
            "owner": "execution",
            "mode": "standalone",
            "command": "preview-cancel",
            "effect": "dry_run",
            "connects_server": false,
            "cancels_order": false,
            "source": "cli_arguments",
            "valid_for_preview": issues.is_empty(),
            "issues": issues,
            "request": request,
            "next_steps": [
                "standalone direct cancel requires an Execution-owned provider action service",
                "runtime cancel must use launch instance component execution cancel"
            ],
        }))
    }

    pub fn preview_cancel_file(&self, file: &Path) -> Result<Value, String> {
        let request = read_typed_file::<CancelOrder>(file)?;
        Ok(mark_typed_file(self.preview_cancel(request)?, file))
    }

    pub fn preview_replace(&self, request: ReplaceOrder) -> Result<Value, String> {
        let mut issues = Vec::new();
        if request.replacement.execution_route_id.is_none() {
            issues.push(
                "replacement execution_route_id is required before a real replace action"
                    .to_owned(),
            );
        }
        if matches!(request.replacement.order_type, OrderType::Limit)
            && request.replacement.limit_price.is_none()
        {
            issues.push("replacement limit order requires limit_price".to_owned());
        }
        Ok(serde_json::json!({
            "owner": "execution",
            "mode": "standalone",
            "command": "preview-replace",
            "effect": "dry_run",
            "connects_server": false,
            "replaces_order": false,
            "source": "cli_arguments",
            "valid_for_preview": issues.is_empty(),
            "issues": issues,
            "request": request,
            "next_steps": [
                "standalone direct replace requires an Execution-owned provider action service",
                "runtime replace must use launch instance component execution replace"
            ],
        }))
    }

    pub fn preview_replace_file(&self, file: &Path) -> Result<Value, String> {
        let request = read_typed_file::<ReplaceOrder>(file)?;
        Ok(mark_typed_file(self.preview_replace(request)?, file))
    }
}

fn read_typed_file<T: serde::de::DeserializeOwned>(file: &Path) -> Result<T, String> {
    let text = std::fs::read_to_string(file)
        .map_err(|error| format!("failed to read typed request file {file:?}: {error}"))?;
    serde_json::from_str(&text)
        .map_err(|error| format!("failed to parse typed request file {file:?}: {error}"))
}

fn mark_typed_file(mut value: Value, file: &Path) -> Value {
    if let Some(object) = value.as_object_mut() {
        object.insert("source".into(), Value::String("typed_request_file".into()));
        object.insert("file".into(), Value::String(file.display().to_string()));
    }
    value
}

fn read_collection(file: &Path, preferred_key: &str) -> Result<Vec<Value>, String> {
    let text = std::fs::read_to_string(file)
        .map_err(|error| format!("failed to read local evidence file {file:?}: {error}"))?;
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Ok(Vec::new());
    }
    if let Ok(value) = serde_json::from_str::<Value>(trimmed) {
        return collection_from_value(value, preferred_key);
    }
    let mut records = Vec::new();
    for (index, line) in text.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let value = serde_json::from_str::<Value>(line).map_err(|error| {
            format!(
                "failed to parse local evidence JSON line {} in {file:?}: {error}",
                index + 1
            )
        })?;
        records.push(value);
    }
    Ok(records)
}

fn collection_from_value(value: Value, preferred_key: &str) -> Result<Vec<Value>, String> {
    match value {
        Value::Array(values) => Ok(values),
        Value::Object(mut object) => {
            if let Some(Value::Array(values)) = object.remove(preferred_key) {
                return Ok(values);
            }
            Ok(vec![Value::Object(object)])
        },
        other => Err(format!(
            "local evidence must be a JSON object, array, or JSONL records; got {}",
            other_type(&other)
        )),
    }
}

fn field_matches(value: &Value, field: &str, expected: Option<&str>) -> bool {
    expected
        .is_none_or(|expected| string_field(value, field).is_some_and(|value| value == expected))
}

fn string_field<'a>(value: &'a Value, field: &str) -> Option<&'a str> {
    value.as_object()?.get(field)?.as_str()
}

fn other_type(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}
