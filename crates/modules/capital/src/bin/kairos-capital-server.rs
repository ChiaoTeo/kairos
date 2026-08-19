use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::str::FromStr;
use std::sync::{Arc, Mutex};

use axum::{
    extract::State,
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use clap::Parser;
use kairos_capital::{
    composition::compose_persistent_capital_application, CancelFundingObjective,
    CapitalApplication, CapitalDemand, CapitalDemandId, CapitalDemandReceipt, CapitalGroupConfig,
    CapitalGroupId, CapitalGroupMember, FundingLocation as DomainLocation, FundingObjective,
    FundingObjectiveId, FundingObjectiveReceipt, FundingPriority, ObserveCapitalDemand,
    PublishFundingObjective,
};
use kairos_capital_contract::{
    CancelFundingObjectiveRequest, ObserveCapitalDemandRequest, PublishFundingObjectiveRequest,
};
use kairos_primitives::{
    AccountId, BrokerId, Currency, Generation, IdempotencyKey, Quantity, SegmentKey, StrategyId,
    UnixNanos,
};
use kairos_workspace::workspace::Workspace;
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::net::UnixListener;
use tokio::sync::oneshot;

#[derive(Debug, Parser)]
struct Args {
    #[arg(long)]
    workspace: String,
    #[arg(long, default_value = "paper")]
    launch_mode: String,
    #[arg(long)]
    launch_id: String,
    #[arg(long, default_value = "default")]
    instance_id: String,
}

#[derive(Debug, Deserialize)]
struct NormalizedConfig {
    #[serde(default)]
    capital: CapitalConfig,
}

#[derive(Debug, Default, Deserialize)]
struct CapitalConfig {
    #[serde(default)]
    enabled: bool,
    capital_group_id: Option<String>,
    strategy_id: Option<String>,
    #[serde(default = "one")]
    membership_version: u64,
}

const fn one() -> u64 {
    1
}

#[derive(Debug, Deserialize)]
struct Manifest {
    #[serde(default)]
    accounts: BTreeMap<String, ManifestAccount>,
}

#[derive(Debug, Deserialize)]
struct ManifestAccount {
    broker: String,
    lease_fence: String,
    #[serde(default)]
    permitted_segments: Vec<String>,
}

struct CapitalState {
    application: Mutex<CapitalApplication>,
    account_lease_fences: BTreeMap<String, String>,
    stop: Mutex<Option<oneshot::Sender<()>>>,
}

#[tokio::main(flavor = "current_thread")]
async fn main() {
    kairos_workspace::logging::init("capital");
    if let Err(error) = run().await {
        tracing::error!(event = "process_failed", component = "capital", error = %error);
        eprintln!("kairos-capital-server: {error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let workspace = Workspace::open(args.workspace)?;
    let instance = workspace.instance(&args.launch_mode, &args.launch_id, &args.instance_id)?;
    instance.prepare()?;
    let _process_lock = instance.process_lock("capital")?;
    let normalized: NormalizedConfig =
        serde_json::from_slice(&std::fs::read(instance.normalized_config()?)?)?;
    if !normalized.capital.enabled {
        return Err("Capital server cannot start for a disabled launch".into());
    }
    let manifest: Manifest =
        serde_json::from_slice(&std::fs::read(instance.component_manifest()?)?)?;
    let account_lease_fences = manifest
        .accounts
        .iter()
        .map(|(account_id, account)| (account_id.clone(), account.lease_fence.clone()))
        .collect();
    let config = group_config(normalized.capital, manifest, args.launch_mode.clone())?;
    let application = compose_persistent_capital_application(
        config,
        instance.state(&["capital", "capital-state.json"])?,
    )?;
    let socket = instance.socket("capital")?;
    let health = instance.health("capital")?;
    remove_socket(&socket)?;
    if let Some(parent) = socket.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    let listener = UnixListener::bind(&socket)?;
    let (stop_tx, stop_rx) = oneshot::channel();
    let state = Arc::new(CapitalState {
        application: Mutex::new(application),
        account_lease_fences,
        stop: Mutex::new(Some(stop_tx)),
    });
    let router = Router::new()
        .route("/v1/health", get(health_handler))
        .route("/v1/stop", post(stop_handler))
        .route("/v1/objectives/publish", post(publish_objective))
        .route("/v1/objectives/cancel", post(cancel_objective))
        .route("/v1/demands/observe", post(observe_demand))
        .with_state(state);
    write_health(&health, "ready")?;
    axum::serve(listener, router)
        .with_graceful_shutdown(async move {
            let _ = stop_rx.await;
        })
        .await?;
    remove_socket(&socket)?;
    write_health(&health, "stopped")?;
    Ok(())
}

fn group_config(
    capital: CapitalConfig,
    manifest: Manifest,
    environment: String,
) -> Result<CapitalGroupConfig, String> {
    let members = manifest
        .accounts
        .into_iter()
        .map(|(account_id, value)| {
            let permitted_segments = value
                .permitted_segments
                .into_iter()
                .map(|segment| SegmentKey::new(segment).map_err(|error| error.to_string()))
                .collect::<Result<Vec<_>, _>>()?;
            Ok(CapitalGroupMember {
                broker: BrokerId::new(value.broker).map_err(|error| error.to_string())?,
                account_id: AccountId::new(account_id).map_err(|error| error.to_string())?,
                permitted_segments,
            })
        })
        .collect::<Result<Vec<_>, String>>()?;
    Ok(CapitalGroupConfig {
        capital_group_id: CapitalGroupId::new(
            capital
                .capital_group_id
                .ok_or_else(|| "capital_group_id is required".to_string())?,
        )?,
        strategy_id: StrategyId::new(
            capital
                .strategy_id
                .ok_or_else(|| "capital strategy_id is required".to_string())?,
        )
        .map_err(|error| error.to_string())?,
        environment,
        membership_version: Generation::new(capital.membership_version),
        members,
    })
}

async fn health_handler() -> Json<Value> {
    Json(json!({"status": "ready"}))
}

async fn stop_handler(State(state): State<Arc<CapitalState>>) -> Json<Value> {
    if let Some(stop) = state
        .stop
        .lock()
        .expect("Capital stop mutex poisoned")
        .take()
    {
        let _ = stop.send(());
    }
    Json(json!({"status": "stopping"}))
}

async fn publish_objective(
    State(state): State<Arc<CapitalState>>,
    Json(request): Json<PublishFundingObjectiveRequest>,
) -> Response {
    let response_request_id = request.request_id.clone();
    let response_objective_id = request.objective_id.clone();
    let response_version = request.version;
    let result = (|| {
        let objective = FundingObjective {
            objective_id: FundingObjectiveId::new(&request.objective_id)?,
            version: Generation::new(request.version),
            strategy_id: StrategyId::new(&request.strategy_id)?,
            destination: location(request.destination)?,
            desired_available: quantity(&request.desired_available)?,
            required_by: UnixNanos::new(request.required_by_unix_nanos),
            expires_at: UnixNanos::new(request.expires_at_unix_nanos),
            priority: priority(request.priority),
            confidence_bps: request.confidence_bps,
            strategy_decision_id: request.strategy_decision_id,
        };
        state
            .application
            .lock()
            .map_err(|_| "Capital application mutex is poisoned".to_string())?
            .publish_funding_objective(PublishFundingObjective {
                capital_group_id: CapitalGroupId::new(request.capital_group_id)?,
                objective,
                observed_at: UnixNanos::new(request.observed_at_unix_nanos),
            })
            .map_err(|error| error.to_string())
    })();
    match result {
        Ok(receipt) => objective_response(response_request_id, receipt),
        Err(error) => rejected_objective_response(
            response_request_id,
            response_objective_id,
            response_version,
            error,
        ),
    }
}

async fn cancel_objective(
    State(state): State<Arc<CapitalState>>,
    Json(request): Json<CancelFundingObjectiveRequest>,
) -> Response {
    let response_request_id = request.request_id.clone();
    let response_objective_id = request.objective_id.clone();
    let response_version = request.expected_version;
    let result = (|| {
        state
            .application
            .lock()
            .map_err(|_| "Capital application mutex is poisoned".to_string())?
            .cancel_funding_objective(CancelFundingObjective {
                capital_group_id: CapitalGroupId::new(request.capital_group_id)?,
                objective_id: FundingObjectiveId::new(&request.objective_id)?,
                expected_version: Generation::new(request.expected_version),
                observed_at: UnixNanos::new(request.observed_at_unix_nanos),
            })
            .map_err(|error| error.to_string())
    })();
    match result {
        Ok(receipt) => objective_response(response_request_id, receipt),
        Err(error) => rejected_objective_response(
            response_request_id,
            response_objective_id,
            response_version,
            error,
        ),
    }
}

async fn observe_demand(
    State(state): State<Arc<CapitalState>>,
    Json(request): Json<ObserveCapitalDemandRequest>,
) -> Response {
    let response_request_id = request.request_id.clone();
    let response_demand_id = request.demand_id.clone();
    let result = (|| {
        validate_lease_fence(
            &state.account_lease_fences,
            &request.destination.account_id,
            &request.destination_lease_fence,
        )?;
        let demand = CapitalDemand {
            demand_id: CapitalDemandId::new(&request.demand_id)?,
            idempotency_key: IdempotencyKey::new(request.idempotency_key)
                .map_err(|error| error.to_string())?,
            strategy_id: StrategyId::new(request.strategy_id)?,
            destination: location(request.destination)?,
            observed_shortfall: quantity(&request.observed_shortfall)?,
            observed_at: UnixNanos::new(request.observed_at_unix_nanos),
            required_by: UnixNanos::new(request.required_by_unix_nanos),
            expires_at: UnixNanos::new(request.expires_at_unix_nanos),
            priority: priority(request.priority),
            confidence_bps: request.confidence_bps,
            account_watermark: request.account_watermark.into(),
            risk_watermark: request.risk_watermark.into(),
            launch_id: request.launch_id,
            instance_id: request.instance_id,
            destination_lease_fence: request.destination_lease_fence,
            causal_references: request.causal_references,
        };
        state
            .application
            .lock()
            .map_err(|_| "Capital application mutex is poisoned".to_string())?
            .observe_demand(ObserveCapitalDemand {
                capital_group_id: CapitalGroupId::new(request.capital_group_id)?,
                demand,
            })
            .map_err(|error| error.to_string())
    })();
    match result {
        Ok(receipt) => {
            let (record, status) = match receipt {
                CapitalDemandReceipt::Accepted(record) => (record, "accepted"),
                CapitalDemandReceipt::Duplicate(record) => (record, "duplicate"),
            };
            (
                StatusCode::OK,
                Json(json!({
                    "request_id": response_request_id,
                    "demand_id": record.demand.demand_id.to_string(),
                    "status": status,
                })),
            )
                .into_response()
        }
        Err(error) => (
            StatusCode::OK,
            Json(json!({
                "request_id": response_request_id,
                "demand_id": response_demand_id,
                "status": "rejected",
                "message": error,
            })),
        )
            .into_response(),
    }
}

fn validate_lease_fence(
    expected: &BTreeMap<String, String>,
    account_id: &str,
    actual: &str,
) -> Result<(), String> {
    match expected.get(account_id) {
        Some(value) if value == actual => Ok(()),
        Some(_) => Err(format!(
            "stale lease fence for destination account {account_id}"
        )),
        None => Err(format!(
            "destination account {account_id} is outside the Capital group"
        )),
    }
}

fn objective_response(request_id: String, receipt: FundingObjectiveReceipt) -> Response {
    let (record, status) = match receipt {
        FundingObjectiveReceipt::Accepted(record) => (record, "accepted"),
        FundingObjectiveReceipt::Duplicate(record) => (record, "duplicate"),
        FundingObjectiveReceipt::Cancelled(record) => (record, "cancelled"),
    };
    (
        StatusCode::OK,
        Json(json!({
            "request_id": request_id,
            "objective_id": record.objective.objective_id.to_string(),
            "version": record.objective.version.get(),
            "status": status,
        })),
    )
        .into_response()
}

fn location(value: kairos_capital_contract::FundingLocation) -> Result<DomainLocation, String> {
    Ok(DomainLocation {
        broker: BrokerId::new(value.broker).map_err(|error| error.to_string())?,
        account_id: AccountId::new(value.account_id).map_err(|error| error.to_string())?,
        segment: SegmentKey::new(value.segment).map_err(|error| error.to_string())?,
        asset: Currency::new(value.asset).map_err(|error| error.to_string())?,
    })
}

fn quantity(value: &str) -> Result<Quantity, String> {
    Quantity::from_str(value).map_err(|error| error.to_string())
}

fn priority(value: kairos_capital_contract::FundingObjectivePriority) -> FundingPriority {
    match value {
        kairos_capital_contract::FundingObjectivePriority::Low => FundingPriority::Low,
        kairos_capital_contract::FundingObjectivePriority::Normal => FundingPriority::Normal,
        kairos_capital_contract::FundingObjectivePriority::High => FundingPriority::High,
        kairos_capital_contract::FundingObjectivePriority::Critical => FundingPriority::Critical,
    }
}

fn rejected_objective_response(
    request_id: String,
    objective_id: String,
    version: u64,
    error: String,
) -> Response {
    (
        StatusCode::OK,
        Json(json!({
            "request_id": request_id,
            "objective_id": objective_id,
            "version": version,
            "status": "rejected",
            "message": error,
        })),
    )
        .into_response()
}

fn remove_socket(path: &Path) -> std::io::Result<()> {
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

fn write_health(path: &PathBuf, status: &str) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let payload = serde_json::to_vec(&json!({"status": status})).map_err(std::io::Error::other)?;
    std::fs::write(path, payload)
}
