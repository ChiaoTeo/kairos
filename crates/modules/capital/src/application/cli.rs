use std::path::{Path, PathBuf};

use kairos_workspace::Workspace;
use serde::Serialize;
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

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind", content = "request", rename_all = "snake_case")]
pub enum CapitalCliRequest {
    FundingObjective(kairos_capital_contract::PublishFundingObjectiveRequest),
    CapitalDemand(kairos_capital_contract::ObserveCapitalDemandRequest),
    Availability(kairos_capital_contract::QueryCapitalAvailabilityRequest),
    CancelFundingObjective(kairos_capital_contract::CancelFundingObjectiveRequest),
    ReconcilePlan(kairos_capital_contract::ReconcileCapitalPlanRequest),
}

#[derive(Clone, Debug, Serialize)]
pub struct CapitalValidationResult {
    pub owner: &'static str,
    pub mode: &'static str,
    pub kind: &'static str,
    pub valid: bool,
    pub file: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct CapitalPreviewResult {
    pub owner: &'static str,
    pub mode: &'static str,
    pub command: &'static str,
    pub kind: &'static str,
    pub file: String,
    pub valid: bool,
    pub connects_server: bool,
    pub writes_runtime_state: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request: Option<CapitalCliRequest>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct CapitalPlanInput {
    pub file: String,
    #[serde(flatten)]
    pub request: CapitalCliRequest,
}

#[derive(Clone, Debug, Serialize)]
pub struct CapitalPlanInputError {
    pub file: String,
    pub kind: &'static str,
    pub error: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct CapitalPlanSummary {
    pub objective_count: usize,
    pub demand_count: usize,
    pub availability_query_count: usize,
    pub invalid_count: usize,
}

#[derive(Clone, Debug, Serialize)]
pub struct CapitalPlanInputs {
    pub objectives: Vec<CapitalPlanInput>,
    pub demands: Vec<CapitalPlanInput>,
    pub availability_queries: Vec<CapitalPlanInput>,
}

#[derive(Clone, Debug, Serialize)]
pub struct CapitalPlanResult {
    pub owner: &'static str,
    pub mode: &'static str,
    pub command: &'static str,
    pub valid: bool,
    pub connects_server: bool,
    pub writes_runtime_state: bool,
    pub executes_transfer: bool,
    pub summary: CapitalPlanSummary,
    pub inputs: CapitalPlanInputs,
    pub errors: Vec<Vec<CapitalPlanInputError>>,
    pub planning_limits: [&'static str; 3],
}

#[derive(Clone, Debug, Serialize)]
#[serde(untagged)]
pub enum CapitalStandaloneOutput {
    Schema(Value),
    Validation(CapitalValidationResult),
    Preview(CapitalPreviewResult),
    Plan(CapitalPlanResult),
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
    ) -> Result<CapitalValidationResult, Box<dyn std::error::Error>> {
        let data = std::fs::read_to_string(file)?;
        let error = parse_request(kind, &data).err();
        Ok(CapitalValidationResult {
            owner: "capital",
            mode: "standalone",
            kind: kind.as_str(),
            valid: error.is_none(),
            file: file.display().to_string(),
            error,
        })
    }

    pub fn preview(
        &self,
        kind: CapitalCliRequestKind,
        file: &Path,
    ) -> Result<CapitalPreviewResult, Box<dyn std::error::Error>> {
        let data = std::fs::read_to_string(file)?;
        let (request, error) = match parse_request(kind, &data) {
            Ok(request) => (Some(request), None),
            Err(error) => (None, Some(error)),
        };
        Ok(CapitalPreviewResult {
            owner: "capital",
            mode: "standalone",
            command: "preview",
            kind: kind.as_str(),
            file: file.display().to_string(),
            valid: error.is_none(),
            connects_server: false,
            writes_runtime_state: false,
            request,
            error,
        })
    }

    pub fn plan(
        &self,
        objective_files: &[PathBuf],
        demand_files: &[PathBuf],
        availability_files: &[PathBuf],
    ) -> Result<CapitalPlanResult, Box<dyn std::error::Error>> {
        let objectives =
            collect_plan_inputs(CapitalCliRequestKind::FundingObjective, objective_files)?;
        let demands = collect_plan_inputs(CapitalCliRequestKind::CapitalDemand, demand_files)?;
        let availability_queries =
            collect_plan_inputs(CapitalCliRequestKind::Availability, availability_files)?;
        let invalid_count =
            objectives.invalid_count + demands.invalid_count + availability_queries.invalid_count;
        Ok(CapitalPlanResult {
            owner: "capital",
            mode: "standalone",
            command: "plan",
            valid: invalid_count == 0,
            connects_server: false,
            writes_runtime_state: false,
            executes_transfer: false,
            summary: CapitalPlanSummary {
                objective_count: objectives.items.len(),
                demand_count: demands.items.len(),
                availability_query_count: availability_queries.items.len(),
                invalid_count,
            },
            inputs: CapitalPlanInputs {
                objectives: objectives.items,
                demands: demands.items,
                availability_queries: availability_queries.items,
            },
            errors: vec![
                objectives.errors,
                demands.errors,
                availability_queries.errors,
            ],
            planning_limits: [
                "standalone plan validates and summarizes local typed request files only",
                "route selection requires explicit offline fixtures or connected Capital runtime",
                "no funding objective is published and no transfer is submitted",
            ],
        })
    }
}

struct PlanInputs {
    items: Vec<CapitalPlanInput>,
    errors: Vec<CapitalPlanInputError>,
    invalid_count: usize,
}

fn collect_plan_inputs(
    kind: CapitalCliRequestKind,
    files: &[PathBuf],
) -> Result<PlanInputs, Box<dyn std::error::Error>> {
    let mut items = Vec::new();
    let mut errors = Vec::new();
    for file in files {
        let data = std::fs::read_to_string(file)?;
        match parse_request(kind, &data) {
            Ok(request) => items.push(CapitalPlanInput {
                file: file.display().to_string(),
                request,
            }),
            Err(error) => errors.push(CapitalPlanInputError {
                file: file.display().to_string(),
                kind: kind.as_str(),
                error,
            }),
        }
    }
    let invalid_count = errors.len();
    Ok(PlanInputs {
        items,
        errors,
        invalid_count,
    })
}

fn parse_request(kind: CapitalCliRequestKind, data: &str) -> Result<CapitalCliRequest, String> {
    match kind {
        CapitalCliRequestKind::FundingObjective => serde_json::from_str(data)
            .map(CapitalCliRequest::FundingObjective)
            .map_err(|error| error.to_string()),
        CapitalCliRequestKind::CapitalDemand => serde_json::from_str(data)
            .map(CapitalCliRequest::CapitalDemand)
            .map_err(|error| error.to_string()),
        CapitalCliRequestKind::Availability => serde_json::from_str(data)
            .map(CapitalCliRequest::Availability)
            .map_err(|error| error.to_string()),
        CapitalCliRequestKind::CancelFundingObjective => serde_json::from_str(data)
            .map(CapitalCliRequest::CancelFundingObjective)
            .map_err(|error| error.to_string()),
        CapitalCliRequestKind::ReconcilePlan => serde_json::from_str(data)
            .map(CapitalCliRequest::ReconcilePlan)
            .map_err(|error| error.to_string()),
    }
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
