use std::path::{Path, PathBuf};

use kairos_workspace::Workspace;
use serde_json::{Value, json};

/// Standalone Capital CLI facade.
///
/// This facade is reserved for local capital planning, availability previews,
/// and future direct transfer previews/actions. It must not operate the
/// Capital runtime process or mutate runtime funding objectives.
pub struct CliCapitalApplication {
    workspace_root: PathBuf,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CapitalCliRequestKind {
    FundingObjective,
    CapitalDemand,
    Availability,
    CancelFundingObjective,
    ReconcilePlan,
}

impl CliCapitalApplication {
    pub fn open(workspace: &Workspace) -> Self {
        Self {
            workspace_root: workspace.root().to_path_buf(),
        }
    }

    pub fn workspace_root(&self) -> &Path {
        &self.workspace_root
    }

    pub fn schema(&self, kind: Option<CapitalCliRequestKind>) -> Value {
        let schemas = match kind {
            Some(CapitalCliRequestKind::FundingObjective) => vec![funding_objective_schema()],
            Some(CapitalCliRequestKind::CapitalDemand) => vec![capital_demand_schema()],
            Some(CapitalCliRequestKind::Availability) => vec![availability_schema()],
            Some(CapitalCliRequestKind::CancelFundingObjective) => {
                vec![cancel_funding_objective_schema()]
            },
            Some(CapitalCliRequestKind::ReconcilePlan) => vec![reconcile_plan_schema()],
            None => vec![
                funding_objective_schema(),
                capital_demand_schema(),
                availability_schema(),
                cancel_funding_objective_schema(),
                reconcile_plan_schema(),
            ],
        };
        json!({
            "owner": "capital",
            "mode": "standalone",
            "workspace_root": self.workspace_root.display().to_string(),
            "schemas": schemas,
        })
    }

    pub fn doctor(
        &self,
        kind: CapitalCliRequestKind,
        file: &Path,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let data = std::fs::read_to_string(file)?;
        let result = match kind {
            CapitalCliRequestKind::FundingObjective => validate_file::<
                kairos_capital_contract::PublishFundingObjectiveRequest,
            >(kind, file, &data),
            CapitalCliRequestKind::CapitalDemand => validate_file::<
                kairos_capital_contract::ObserveCapitalDemandRequest,
            >(kind, file, &data),
            CapitalCliRequestKind::Availability => validate_file::<
                kairos_capital_contract::QueryCapitalAvailabilityRequest,
            >(kind, file, &data),
            CapitalCliRequestKind::CancelFundingObjective => validate_file::<
                kairos_capital_contract::CancelFundingObjectiveRequest,
            >(kind, file, &data),
            CapitalCliRequestKind::ReconcilePlan => validate_file::<
                kairos_capital_contract::ReconcileCapitalPlanRequest,
            >(kind, file, &data),
        };
        Ok(result)
    }

    pub fn preview(
        &self,
        kind: CapitalCliRequestKind,
        file: &Path,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let value = read_json_value(file)?;
        let validation = validate_value(kind, &value);
        Ok(json!({
            "owner": "capital",
            "mode": "standalone",
            "command": "preview",
            "kind": kind.as_str(),
            "file": file.display().to_string(),
            "valid": validation.valid,
            "connects_server": false,
            "writes_runtime_state": false,
            "summary": if validation.valid {
                Some(preview_summary(kind, &value))
            } else {
                None
            },
            "error": validation.error,
        }))
    }

    pub fn plan(
        &self,
        objective_files: &[PathBuf],
        demand_files: &[PathBuf],
        availability_files: &[PathBuf],
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let objectives =
            collect_plan_inputs(CapitalCliRequestKind::FundingObjective, objective_files)?;
        let demands = collect_plan_inputs(CapitalCliRequestKind::CapitalDemand, demand_files)?;
        let availability_queries =
            collect_plan_inputs(CapitalCliRequestKind::Availability, availability_files)?;
        let invalid_count =
            objectives.invalid_count + demands.invalid_count + availability_queries.invalid_count;
        Ok(json!({
            "owner": "capital",
            "mode": "standalone",
            "command": "plan",
            "valid": invalid_count == 0,
            "connects_server": false,
            "writes_runtime_state": false,
            "executes_transfer": false,
            "summary": {
                "objective_count": objectives.items.len(),
                "demand_count": demands.items.len(),
                "availability_query_count": availability_queries.items.len(),
                "invalid_count": invalid_count,
            },
            "inputs": {
                "objectives": objectives.items,
                "demands": demands.items,
                "availability_queries": availability_queries.items,
            },
            "errors": [
                objectives.errors,
                demands.errors,
                availability_queries.errors,
            ],
            "planning_limits": [
                "standalone plan validates and summarizes local typed request files only",
                "route selection requires explicit offline fixtures or connected Capital runtime",
                "no funding objective is published and no transfer is submitted"
            ],
        }))
    }
}

struct Validation {
    valid: bool,
    error: Option<String>,
}

struct PlanInputs {
    items: Vec<Value>,
    errors: Vec<Value>,
    invalid_count: usize,
}

fn read_json_value(file: &Path) -> Result<Value, Box<dyn std::error::Error>> {
    let data = std::fs::read_to_string(file)?;
    Ok(serde_json::from_str(&data)?)
}

fn validate_file<T: serde::de::DeserializeOwned>(
    kind: CapitalCliRequestKind,
    file: &Path,
    data: &str,
) -> Value {
    match serde_json::from_str::<T>(data) {
        Ok(_) => json!({
            "owner": "capital",
            "mode": "standalone",
            "kind": kind.as_str(),
            "valid": true,
            "file": file.display().to_string(),
        }),
        Err(error) => json!({
            "owner": "capital",
            "mode": "standalone",
            "kind": kind.as_str(),
            "valid": false,
            "file": file.display().to_string(),
            "error": error.to_string(),
        }),
    }
}

fn validate_value(kind: CapitalCliRequestKind, value: &Value) -> Validation {
    let result = match kind {
        CapitalCliRequestKind::FundingObjective => serde_json::from_value::<
            kairos_capital_contract::PublishFundingObjectiveRequest,
        >(value.clone())
        .map(|_| ()),
        CapitalCliRequestKind::CapitalDemand => serde_json::from_value::<
            kairos_capital_contract::ObserveCapitalDemandRequest,
        >(value.clone())
        .map(|_| ()),
        CapitalCliRequestKind::Availability => serde_json::from_value::<
            kairos_capital_contract::QueryCapitalAvailabilityRequest,
        >(value.clone())
        .map(|_| ()),
        CapitalCliRequestKind::CancelFundingObjective => serde_json::from_value::<
            kairos_capital_contract::CancelFundingObjectiveRequest,
        >(value.clone())
        .map(|_| ()),
        CapitalCliRequestKind::ReconcilePlan => serde_json::from_value::<
            kairos_capital_contract::ReconcileCapitalPlanRequest,
        >(value.clone())
        .map(|_| ()),
    };
    match result {
        Ok(()) => Validation {
            valid: true,
            error: None,
        },
        Err(error) => Validation {
            valid: false,
            error: Some(error.to_string()),
        },
    }
}

fn collect_plan_inputs(
    kind: CapitalCliRequestKind,
    files: &[PathBuf],
) -> Result<PlanInputs, Box<dyn std::error::Error>> {
    let mut items = Vec::new();
    let mut errors = Vec::new();
    for file in files {
        let value = read_json_value(file)?;
        let validation = validate_value(kind, &value);
        if validation.valid {
            items.push(json!({
                "file": file.display().to_string(),
                "kind": kind.as_str(),
                "summary": preview_summary(kind, &value),
            }));
        } else {
            errors.push(json!({
                "file": file.display().to_string(),
                "kind": kind.as_str(),
                "error": validation.error,
            }));
        }
    }
    let invalid_count = errors.len();
    Ok(PlanInputs {
        items,
        errors,
        invalid_count,
    })
}

fn preview_summary(kind: CapitalCliRequestKind, value: &Value) -> Value {
    match kind {
        CapitalCliRequestKind::FundingObjective => json!({
            "request_id": text_field(value, "request_id"),
            "capital_group_id": text_field(value, "capital_group_id"),
            "objective_id": text_field(value, "objective_id"),
            "strategy_id": text_field(value, "strategy_id"),
            "destination": value.get("destination").cloned(),
            "desired_available": text_field(value, "desired_available"),
            "required_by_unix_nanos": value.get("required_by_unix_nanos").cloned(),
            "expires_at_unix_nanos": value.get("expires_at_unix_nanos").cloned(),
            "priority": text_field(value, "priority"),
            "confidence_bps": value.get("confidence_bps").cloned(),
            "runtime_action_if_connected": "publish_funding_objective",
        }),
        CapitalCliRequestKind::CapitalDemand => json!({
            "request_id": text_field(value, "request_id"),
            "capital_group_id": text_field(value, "capital_group_id"),
            "demand_id": text_field(value, "demand_id"),
            "strategy_id": text_field(value, "strategy_id"),
            "destination": value.get("destination").cloned(),
            "observed_shortfall": text_field(value, "observed_shortfall"),
            "required_by_unix_nanos": value.get("required_by_unix_nanos").cloned(),
            "expires_at_unix_nanos": value.get("expires_at_unix_nanos").cloned(),
            "launch_id": text_field(value, "launch_id"),
            "instance_id": text_field(value, "instance_id"),
            "runtime_action_if_connected": "observe_capital_demand",
        }),
        CapitalCliRequestKind::Availability => json!({
            "request_id": text_field(value, "request_id"),
            "capital_group_id": text_field(value, "capital_group_id"),
            "location": value.get("location").cloned(),
            "runtime_action_if_connected": "query_capital_availability",
        }),
        CapitalCliRequestKind::CancelFundingObjective => json!({
            "request_id": text_field(value, "request_id"),
            "capital_group_id": text_field(value, "capital_group_id"),
            "objective_id": text_field(value, "objective_id"),
            "expected_version": value.get("expected_version").cloned(),
            "strategy_id": text_field(value, "strategy_id"),
            "runtime_action_if_connected": "cancel_funding_objective",
        }),
        CapitalCliRequestKind::ReconcilePlan => json!({
            "request_id": text_field(value, "request_id"),
            "capital_group_id": text_field(value, "capital_group_id"),
            "plan_id": text_field(value, "plan_id"),
            "runtime_action_if_connected": "reconcile_capital_plan",
        }),
    }
}

fn text_field(value: &Value, key: &str) -> Option<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
}

impl CapitalCliRequestKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::FundingObjective => "funding_objective",
            Self::CapitalDemand => "capital_demand",
            Self::Availability => "availability",
            Self::CancelFundingObjective => "cancel_funding_objective",
            Self::ReconcilePlan => "reconcile_plan",
        }
    }
}

fn location_schema() -> Value {
    json!({
        "broker": "binance",
        "account_id": "account-1",
        "segment": "spot",
        "asset": "USDT"
    })
}

fn funding_objective_schema() -> Value {
    json!({
        "kind": "funding_objective",
        "used_by": ["future connected publish-funding-objective --file"],
        "type": "PublishFundingObjectiveRequest",
        "required_fields": [
            "request_id",
            "capital_group_id",
            "objective_id",
            "version",
            "strategy_id",
            "destination",
            "desired_available",
            "required_by_unix_nanos",
            "expires_at_unix_nanos",
            "priority",
            "confidence_bps",
            "strategy_decision_id",
            "observed_at_unix_nanos"
        ],
        "priority": ["low", "normal", "high", "critical"],
        "decimal_fields": ["desired_available"],
        "example": {
            "request_id": "request-1",
            "capital_group_id": "capital-group-1",
            "objective_id": "objective-1",
            "version": 1,
            "strategy_id": "strategy-1",
            "destination": location_schema(),
            "desired_available": "1000.00",
            "required_by_unix_nanos": 100,
            "expires_at_unix_nanos": 200,
            "priority": "normal",
            "confidence_bps": 9000,
            "strategy_decision_id": "decision-1",
            "observed_at_unix_nanos": 1
        }
    })
}

fn capital_demand_schema() -> Value {
    json!({
        "kind": "capital_demand",
        "used_by": ["future connected observe-capital-demand --file"],
        "type": "ObserveCapitalDemandRequest",
        "required_fields": [
            "request_id",
            "demand_id",
            "idempotency_key",
            "capital_group_id",
            "strategy_id",
            "destination",
            "observed_shortfall",
            "observed_at_unix_nanos",
            "required_by_unix_nanos",
            "expires_at_unix_nanos",
            "priority",
            "confidence_bps",
            "account_watermark",
            "risk_watermark",
            "launch_id",
            "instance_id",
            "destination_lease_fence"
        ],
        "optional_fields": ["causal_references"],
        "priority": ["low", "normal", "high", "critical"],
        "decimal_fields": ["observed_shortfall"],
        "example": {
            "request_id": "request-1",
            "demand_id": "demand-1",
            "idempotency_key": "demand-1",
            "capital_group_id": "capital-group-1",
            "strategy_id": "strategy-1",
            "destination": location_schema(),
            "observed_shortfall": "250.00",
            "observed_at_unix_nanos": 1,
            "required_by_unix_nanos": 100,
            "expires_at_unix_nanos": 200,
            "priority": "normal",
            "confidence_bps": 9000,
            "account_watermark": 1,
            "risk_watermark": 1,
            "launch_id": "launch-1",
            "instance_id": "instance-1",
            "destination_lease_fence": "account-1:spot:USDT:1",
            "causal_references": []
        }
    })
}

fn availability_schema() -> Value {
    json!({
        "kind": "availability",
        "used_by": ["future connected availability --file"],
        "type": "QueryCapitalAvailabilityRequest",
        "required_fields": ["request_id", "capital_group_id", "location"],
        "example": {
            "request_id": "request-1",
            "capital_group_id": "capital-group-1",
            "location": location_schema()
        }
    })
}

fn cancel_funding_objective_schema() -> Value {
    json!({
        "kind": "cancel_funding_objective",
        "used_by": ["connected cancel-funding-objective --file"],
        "type": "CancelFundingObjectiveRequest",
        "required_fields": [
            "request_id",
            "capital_group_id",
            "objective_id",
            "expected_version",
            "strategy_id",
            "observed_at_unix_nanos"
        ],
        "example": {
            "request_id": "request-1",
            "capital_group_id": "capital-group-1",
            "objective_id": "objective-1",
            "expected_version": 1,
            "strategy_id": "strategy-1",
            "observed_at_unix_nanos": 1
        }
    })
}

fn reconcile_plan_schema() -> Value {
    json!({
        "kind": "reconcile_plan",
        "used_by": ["connected reconcile-plan --file"],
        "type": "ReconcileCapitalPlanRequest",
        "required_fields": [
            "request_id",
            "capital_group_id",
            "plan_id",
            "observed_at_unix_nanos"
        ],
        "example": {
            "request_id": "request-1",
            "capital_group_id": "capital-group-1",
            "plan_id": "plan-1",
            "observed_at_unix_nanos": 1
        }
    })
}
