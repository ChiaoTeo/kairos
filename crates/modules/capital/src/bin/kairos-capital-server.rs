use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex as StdMutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use clap::Parser;
use kairos_account_contract::{AccountViewKey, AccountViewKind, AccountViewReader};
use kairos_capital::composition::{
    capital_current_view, capital_event, compose_persistent_capital_transfer_process,
};
use kairos_capital::{
    AuthorizeCapitalPlan, CancelFundingObjective, CapitalDemand, CapitalDemandReceipt,
    CapitalGroupConfig, CapitalGroupId, CapitalGroupMember, CapitalPlanId, CapitalPlanStatus,
    CapitalPolicy, CapitalReadiness, CapitalRouteKind, CapitalSettlementClass,
    CapitalTransferProcess, CapitalTransferRoute, EvaluateCapitalGroup,
    FundingLocation as DomainLocation, FundingObjective, FundingObjectiveReceipt, FundingPriority,
    ObserveCapitalDemand, ObserveCapitalSettlement, PublishFundingObjective, UpdateCapitalPolicy,
    UpdateCapitalRoute,
};
use kairos_capital_contract::{
    CancelFundingObjectiveRequest, CapitalAvailabilityResponse, CapitalReadinessStatus,
    MmapCapitalViewPublisher, ObserveCapitalDemandRequest, PublishFundingObjectiveRequest,
    QueryCapitalAvailabilityRequest, QueuedCapitalEventPublisher,
};
use kairos_conflux::{
    CapitalConnectionAccount, CapitalTransferConnections, compose_capital_transfer_connections,
    validate_capital_transfer_product,
};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_primitives::{
    AccountId, BrokerId, Generation, Quantity, SegmentKey, Sequence, StrategyId, UnixNanos,
};
use kairos_risk_contract::{RiskViewKey, RiskViewReader};
use kairos_workspace::workspace::Workspace;
use serde::Deserialize;
use serde_json::{Value, json};
use tokio::net::UnixListener;
use tokio::sync::{Mutex, oneshot};

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

    #[arg(long, env = "AERON_DIR")]
    aeron_dir: Option<String>,

    #[arg(long, default_value = kairos_capital_contract::DEFAULT_AERON_CHANNEL)]
    aeron_channel: String,

    #[arg(
        long,
        default_value_t = kairos_capital_contract::CAPITAL_EVENTS_STREAM_ID
    )]
    capital_events_stream_id: i32,
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
    #[serde(default)]
    policies: Vec<CapitalPolicyConfig>,
    #[serde(default)]
    routes: Vec<CapitalRouteConfig>,
    #[serde(default)]
    automatic_execution: bool,
    #[serde(default = "default_plan_ttl_millis")]
    plan_ttl_millis: u64,
}

#[derive(Clone, Debug, Deserialize)]
struct CapitalPolicyConfig {
    destination: kairos_capital_contract::FundingLocation,
    #[serde(default = "one")]
    version: u64,
    minimum: Quantity,
    default_target: Quantity,
    maximum: Quantity,
    #[serde(default)]
    stress_buffer: Quantity,
    #[serde(default)]
    minimum_movement: Quantity,
    #[serde(default)]
    hysteresis: Quantity,
    #[serde(default)]
    deficit_dwell_millis: u64,
    #[serde(default)]
    cooldown_millis: u64,
    max_fact_age_millis: u64,
}

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
enum CapitalRouteKindConfig {
    InternalTransfer,
    AccountTransfer,
    EarnRedemptionThenTransfer,
    EarnSubscription,
}

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
enum CapitalSettlementClassConfig {
    ImmediateBookTransfer,
    ParticipantHistoryThenAccountObservation,
}

#[derive(Clone, Debug, Deserialize)]
struct CapitalRouteConfig {
    route_id: String,
    #[serde(default = "one")]
    version: u64,
    source: kairos_capital_contract::FundingLocation,
    destination: kairos_capital_contract::FundingLocation,
    kind: CapitalRouteKindConfig,
    per_operation_limit: Quantity,
    daily_limit: Quantity,
    settlement_class: CapitalSettlementClassConfig,
    #[serde(default = "enabled")]
    enabled: bool,
    earn_product_id: Option<String>,
    #[serde(default)]
    demand_guard_millis: u64,
    #[serde(default)]
    allow_unknown_redemption_quota: bool,
}

const fn one() -> u64 {
    1
}

const fn enabled() -> bool {
    true
}

const fn default_plan_ttl_millis() -> u64 {
    30_000
}

#[derive(Debug, Deserialize)]
struct Manifest {
    #[serde(default)]
    accounts: BTreeMap<String, ManifestAccount>,
}

#[derive(Debug, Deserialize)]
struct ManifestAccount {
    broker: String,
    #[serde(default)]
    integration_provider: String,
    #[serde(default)]
    environment: String,
    credential_id: Option<String>,
    #[serde(default)]
    capital_controller_account_id: Option<String>,
    #[serde(default)]
    participant_account_ref: Option<String>,
    lease_fence: String,
    #[serde(default)]
    permitted_segments: Vec<String>,
    #[serde(default)]
    segment_products: BTreeMap<String, String>,
}

#[derive(Debug, Deserialize)]
struct AccountLeaseRecord {
    broker: String,
    account_id: String,
    launch_instance_id: String,
    fencing_token: String,
}

struct CapitalState {
    runtime: Mutex<CapitalTransferProcess<CapitalTransferConnections>>,
    view_publisher: StdMutex<MmapCapitalViewPublisher>,
    event_publisher: QueuedCapitalEventPublisher,
    account_lease_fences: BTreeMap<String, String>,
    account_brokers: BTreeMap<String, String>,
    account_controllers: BTreeMap<String, String>,
    account_lease_root: PathBuf,
    instance_id: String,
    automatic_execution: bool,
    plan_ttl_nanos: u64,
    reconcile_after: StdMutex<BTreeMap<String, UnixNanos>>,
    stop: StdMutex<Option<oneshot::Sender<()>>>,
}

fn capital_controller<'a>(
    account_id: &'a str,
    account: &'a ManifestAccount,
    manifest: &'a Manifest,
) -> Option<&'a str> {
    account
        .capital_controller_account_id
        .as_deref()
        .or_else(|| {
            manifest
                .accounts
                .values()
                .any(|candidate| {
                    candidate.capital_controller_account_id.as_deref() == Some(account_id)
                })
                .then_some(account_id)
        })
}

fn capital_product<'a>(account: &'a ManifestAccount, segment: &'a str) -> &'a str {
    account
        .segment_products
        .get(segment)
        .map(String::as_str)
        .unwrap_or(segment)
}

fn validate_automatic_routes(capital: &CapitalConfig, manifest: &Manifest) -> Result<(), String> {
    if !capital.automatic_execution {
        return Ok(());
    }
    for route in capital.routes.iter().filter(|route| route.enabled) {
        let source = manifest
            .accounts
            .get(route.source.account_id.as_str())
            .ok_or_else(|| {
                format!(
                    "Capital route '{}' references an unknown source Account",
                    route.route_id
                )
            })?;
        let destination = manifest
            .accounts
            .get(route.destination.account_id.as_str())
            .ok_or_else(|| {
                format!(
                    "Capital route '{}' references an unknown destination Account",
                    route.route_id
                )
            })?;
        let provider = if source.integration_provider.is_empty() {
            source.broker.as_str()
        } else {
            source.integration_provider.as_str()
        };
        validate_capital_transfer_product(
            provider,
            capital_product(source, route.source.segment.as_str()),
        )
        .and_then(|_| {
            validate_capital_transfer_product(
                provider,
                capital_product(destination, route.destination.segment.as_str()),
            )
        })
        .map_err(|error| {
            format!(
                "Capital route '{}' is not executable: {error}",
                route.route_id
            )
        })?;
        match route.kind {
            CapitalRouteKindConfig::InternalTransfer => {
                if route.source.account_id != route.destination.account_id {
                    return Err(format!(
                        "Capital internal route '{}' must remain inside one Account",
                        route.route_id
                    ));
                }
                if source.credential_id.is_none() {
                    return Err(format!(
                        "Capital route '{}' requires a credential-bound source Account",
                        route.route_id
                    ));
                }
            },
            CapitalRouteKindConfig::AccountTransfer => {
                if route.source.account_id == route.destination.account_id {
                    return Err(format!(
                        "Capital Account route '{}' must cross Account boundaries",
                        route.route_id
                    ));
                }
                let source_controller =
                    capital_controller(route.source.account_id.as_str(), source, manifest);
                let destination_controller = capital_controller(
                    route.destination.account_id.as_str(),
                    destination,
                    manifest,
                );
                if source_controller.is_none() || source_controller != destination_controller {
                    return Err(format!(
                        "Capital Account route '{}' requires one explicitly shared controller",
                        route.route_id
                    ));
                }
                let controller_id = source_controller.expect("checked above");
                let controller = manifest.accounts.get(controller_id).ok_or_else(|| {
                    format!(
                        "Capital route '{}' references missing controller Account '{}'",
                        route.route_id, controller_id
                    )
                })?;
                if controller.credential_id.is_none() {
                    return Err(format!(
                        "Capital controller Account '{controller_id}' requires a credential"
                    ));
                }
            },
            CapitalRouteKindConfig::EarnRedemptionThenTransfer => {
                if source.credential_id.is_none() {
                    return Err(format!(
                        "Capital Earn route '{}' requires a credential-bound source Account",
                        route.route_id
                    ));
                }
            },
            CapitalRouteKindConfig::EarnSubscription => {
                if route.source != route.destination {
                    return Err(format!(
                        "Capital Earn subscription route '{}' must remain at one balance location",
                        route.route_id
                    ));
                }
                if source.credential_id.is_none() || route.earn_product_id.is_none() {
                    return Err(format!(
                        "Capital Earn subscription route '{}' requires a credential and product id",
                        route.route_id
                    ));
                }
            },
        }
    }
    Ok(())
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
    validate_automatic_routes(&normalized.capital, &manifest)?;
    let account_lease_fences = manifest
        .accounts
        .iter()
        .map(|(account_id, account)| (account_id.clone(), account.lease_fence.clone()))
        .collect();
    let account_brokers = manifest
        .accounts
        .iter()
        .map(|(account_id, account)| (account_id.clone(), account.broker.clone()))
        .collect();
    let account_controllers = manifest
        .accounts
        .iter()
        .filter_map(|(account_id, account)| {
            capital_controller(account_id, account, &manifest)
                .map(|controller| (account_id.clone(), controller.to_owned()))
        })
        .collect();
    let plan_ttl_nanos = millis_to_nanos(normalized.capital.plan_ttl_millis)?;
    if plan_ttl_nanos == 0 {
        return Err("Capital plan_ttl_millis must be positive".into());
    }
    let config = group_config(&normalized.capital, &manifest, args.launch_mode.clone())?;
    let capital_group_id = config.capital_group_id.clone();
    let snapshot_root = instance.snapshot(&[])?;
    let connection_accounts = manifest
        .accounts
        .iter()
        .map(|(account_id, account)| CapitalConnectionAccount {
            account_id: account_id.clone(),
            broker: account.broker.clone(),
            integration_provider: account.integration_provider.clone(),
            environment: account.environment.clone(),
            credential_id: account.credential_id.clone(),
            capital_controller_account_id: account.capital_controller_account_id.clone(),
            participant_account_ref: account.participant_account_ref.clone(),
            permitted_segments: account.permitted_segments.clone(),
            segment_products: account.segment_products.clone(),
        })
        .collect::<Vec<_>>();
    let credential_config = workspace.existing_path(
        &["config", "credentials", "credentials.toml"],
        &["credentials", "credentials.toml"],
    )?;
    let connections = compose_capital_transfer_connections(
        &credential_config,
        &args.launch_mode,
        connection_accounts,
    )?;
    let mut runtime = compose_persistent_capital_transfer_process(
        config,
        instance.state(&["capital", "capital-state.json"])?,
        connections,
    )?;
    let configuration_updated_at = now_unix_nanos()?;
    for policy in &normalized.capital.policies {
        runtime
            .application_mut()
            .update_policy(UpdateCapitalPolicy {
                capital_group_id: capital_group_id.clone(),
                policy: policy_from_config(policy)?,
                updated_at: configuration_updated_at,
            })?;
    }
    for route in &normalized.capital.routes {
        runtime.application_mut().update_route(UpdateCapitalRoute {
            capital_group_id: capital_group_id.clone(),
            route: route_from_config(route, &account_lease_fences)?,
            updated_at: configuration_updated_at,
        })?;
    }
    runtime
        .application_mut()
        .evaluate(EvaluateCapitalGroup {
            evaluated_at: configuration_updated_at,
        })
        .map_err(|error| error.to_string())?;
    let socket = instance.socket("capital")?;
    let health = instance.health("capital")?;
    remove_socket(&socket)?;
    if let Some(parent) = socket.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    let listener = UnixListener::bind(&socket)?;
    let (stop_tx, stop_rx) = oneshot::channel();
    let identity = InstanceIdentity::new(workspace.id(), &args.launch_id, &args.instance_id)?;
    let view_publisher = MmapCapitalViewPublisher::create(
        &snapshot_root,
        4 * 1024 * 1024,
        format!("capital:{}", capital_group_id),
        identity.clone(),
        capital_group_id.to_string(),
    )?;
    let event_publisher = QueuedCapitalEventPublisher::start(
        kairos_capital_contract::AeronEndpoint::from_parts(
            args.aeron_dir.as_deref(),
            args.aeron_channel,
            args.capital_events_stream_id,
        )?,
        format!("capital:{}", capital_group_id),
        identity,
        1024,
    )?;
    let state = Arc::new(CapitalState {
        runtime: Mutex::new(runtime),
        view_publisher: StdMutex::new(view_publisher),
        event_publisher,
        account_lease_fences,
        account_brokers,
        account_controllers,
        account_lease_root: workspace.state_root().join("account-locks"),
        instance_id: args.instance_id.clone(),
        automatic_execution: normalized.capital.automatic_execution,
        plan_ttl_nanos,
        reconcile_after: StdMutex::new(BTreeMap::new()),
        stop: StdMutex::new(Some(stop_tx)),
    });
    publish_current_view(&state).await?;
    let facts_task = tokio::spawn(run_facts_loop(
        state.clone(),
        snapshot_root,
        args.instance_id,
        capital_group_id,
    ));
    let router = Router::new()
        .route("/v1/health", get(health_handler))
        .route("/v1/stop", post(stop_handler))
        .route("/v1/objectives/publish", post(publish_objective))
        .route("/v1/objectives/cancel", post(cancel_objective))
        .route("/v1/demands/observe", post(observe_demand))
        .route("/v1/availability/query", post(query_availability))
        .with_state(state);
    write_health(&health, "ready")?;
    axum::serve(listener, router)
        .with_graceful_shutdown(async move {
            let _ = stop_rx.await;
        })
        .await?;
    facts_task.abort();
    remove_socket(&socket)?;
    write_health(&health, "stopped")?;
    Ok(())
}

fn group_config(
    capital: &CapitalConfig,
    manifest: &Manifest,
    environment: String,
) -> Result<CapitalGroupConfig, String> {
    let members = manifest
        .accounts
        .iter()
        .map(|(account_id, value)| {
            let permitted_segments = value
                .permitted_segments
                .iter()
                .map(|segment| SegmentKey::new(segment).map_err(|error| error.to_string()))
                .collect::<Result<Vec<_>, _>>()?;
            Ok(CapitalGroupMember {
                broker: BrokerId::new(&value.broker).map_err(|error| error.to_string())?,
                account_id: AccountId::new(account_id).map_err(|error| error.to_string())?,
                permitted_segments,
            })
        })
        .collect::<Result<Vec<_>, String>>()?;
    Ok(CapitalGroupConfig {
        capital_group_id: CapitalGroupId::new(
            capital
                .capital_group_id
                .clone()
                .ok_or_else(|| "capital_group_id is required".to_string())?,
        )?,
        strategy_id: StrategyId::new(
            capital
                .strategy_id
                .clone()
                .ok_or_else(|| "capital strategy_id is required".to_string())?,
        )
        .map_err(|error| error.to_string())?,
        environment,
        membership_version: Generation::new(capital.membership_version),
        members,
    })
}

fn policy_from_config(value: &CapitalPolicyConfig) -> Result<CapitalPolicy, String> {
    Ok(CapitalPolicy {
        destination: domain_location(&value.destination),
        version: Generation::new(value.version),
        minimum: value.minimum,
        default_target: value.default_target,
        maximum: value.maximum,
        stress_buffer: value.stress_buffer,
        minimum_movement: value.minimum_movement,
        hysteresis: value.hysteresis,
        deficit_dwell_nanos: millis_to_nanos(value.deficit_dwell_millis)?,
        cooldown_nanos: millis_to_nanos(value.cooldown_millis)?,
        max_fact_age_nanos: millis_to_nanos(value.max_fact_age_millis)?,
    })
}

fn route_from_config(
    value: &CapitalRouteConfig,
    account_lease_fences: &BTreeMap<String, String>,
) -> Result<CapitalTransferRoute, String> {
    let source_authority = account_lease_fences
        .get(value.source.account_id.as_str())
        .cloned()
        .ok_or_else(|| {
            format!(
                "Capital route source account '{}' has no current lease fence",
                value.source.account_id
            )
        })?;
    Ok(CapitalTransferRoute {
        route_id: kairos_capital::CapitalRouteId::new(&value.route_id)
            .map_err(|error| error.to_string())?,
        version: Generation::new(value.version),
        source: domain_location(&value.source),
        destination: domain_location(&value.destination),
        kind: match value.kind {
            CapitalRouteKindConfig::InternalTransfer => CapitalRouteKind::InternalTransfer,
            CapitalRouteKindConfig::AccountTransfer => CapitalRouteKind::AccountTransfer,
            CapitalRouteKindConfig::EarnRedemptionThenTransfer => {
                CapitalRouteKind::EarnRedemptionThenTransfer
            },
            CapitalRouteKindConfig::EarnSubscription => CapitalRouteKind::EarnSubscription,
        },
        per_operation_limit: value.per_operation_limit,
        daily_limit: value.daily_limit,
        required_source_authority: source_authority,
        settlement_class: match value.settlement_class {
            CapitalSettlementClassConfig::ImmediateBookTransfer => {
                CapitalSettlementClass::ImmediateBookTransfer
            },
            CapitalSettlementClassConfig::ParticipantHistoryThenAccountObservation => {
                CapitalSettlementClass::ParticipantHistoryThenAccountObservation
            },
        },
        enabled: value.enabled,
        earn_product_id: value.earn_product_id.clone(),
        demand_guard_nanos: millis_to_nanos(value.demand_guard_millis)?,
        allow_unknown_redemption_quota: value.allow_unknown_redemption_quota,
    })
}

fn domain_location(value: &kairos_capital_contract::FundingLocation) -> DomainLocation {
    DomainLocation {
        broker: value.broker.clone(),
        account_id: value.account_id.clone(),
        segment: value.segment.clone(),
        asset: value.asset.clone(),
    }
}

fn millis_to_nanos(value: u64) -> Result<u64, String> {
    value
        .checked_mul(1_000_000)
        .ok_or_else(|| "Capital duration overflows nanoseconds".to_string())
}

async fn run_facts_loop(
    state: Arc<CapitalState>,
    snapshot_root: PathBuf,
    instance_id: String,
    capital_group_id: CapitalGroupId,
) {
    loop {
        if let Err(error) =
            refresh_facts(&state, &snapshot_root, &instance_id, &capital_group_id).await
        {
            tracing::warn!(event = "capital_facts_refresh_failed", error = %error);
            if let Ok(evaluated_at) = now_unix_nanos() {
                let evaluated = {
                    let mut runtime = state.runtime.lock().await;
                    runtime
                        .application_mut()
                        .evaluate(EvaluateCapitalGroup { evaluated_at })
                        .map_err(|error| error.to_string())
                };
                if let Err(evaluation_error) = evaluated {
                    tracing::warn!(event = "capital_degraded_evaluation_failed", error = %evaluation_error);
                } else if let Err(publication_error) = publish_current_view(&state).await {
                    tracing::warn!(event = "capital_degraded_view_publish_failed", error = %publication_error);
                }
            }
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
}

async fn refresh_facts(
    state: &CapitalState,
    snapshot_root: &Path,
    instance_id: &str,
    capital_group_id: &CapitalGroupId,
) -> Result<(), String> {
    let (policies, routes, strategy_id) = {
        let runtime = state.runtime.lock().await;
        let snapshot = runtime.application().snapshot();
        (snapshot.policies, snapshot.routes, snapshot.strategy_id)
    };
    if policies.is_empty() {
        publish_current_view(state).await?;
        return Ok(());
    }

    let risk_reader = RiskViewReader::open(
        snapshot_root,
        RiskViewKey::latest(format!("risk:{instance_id}")),
    )
    .map_err(|error| error.to_string())?;
    let risk_frame = risk_reader.read().map_err(|error| error.to_string())?;
    let risk_root = risk_frame.decode().map_err(|error| error.to_string())?;
    let risk_state = risk_root.state();
    let risk_metadata = risk_frame.envelope_metadata();
    let risk_watermark = Sequence::new(risk_metadata.applied_event_sequence);
    let risk_policy_version = Generation::new(risk_state.policy_version());

    let locations = policies
        .iter()
        .map(|policy| policy.destination.clone())
        .chain(
            routes
                .iter()
                .flat_map(|route| [route.source.clone(), route.destination.clone()]),
        )
        .collect::<std::collections::BTreeSet<_>>();
    let facts = locations
        .iter()
        .map(|location| {
            read_location_facts(
                snapshot_root,
                location,
                strategy_id.as_str(),
                risk_state,
                risk_policy_version,
                risk_watermark,
            )
        })
        .collect::<Result<Vec<_>, _>>()?;
    let evaluated_at = now_unix_nanos()?;
    let facts_by_location = facts
        .iter()
        .cloned()
        .map(|fact| (fact.destination.clone(), fact))
        .collect::<BTreeMap<_, _>>();
    let mut runtime = state.runtime.lock().await;
    let application = runtime.application_mut();
    for fact in facts {
        application
            .observe_facts(kairos_capital::ObserveCapitalFacts {
                capital_group_id: capital_group_id.clone(),
                facts: fact,
            })
            .map_err(|error| error.to_string())?;
    }
    application
        .expire_funding_objectives(kairos_capital::ExpireFundingObjectives {
            observed_at: evaluated_at,
        })
        .map_err(|error| error.to_string())?;
    application
        .expire_demands(kairos_capital::ExpireCapitalDemands {
            observed_at: evaluated_at,
        })
        .map_err(|error| error.to_string())?;
    application
        .expire_plans(kairos_capital::ExpireCapitalPlans {
            observed_at: evaluated_at,
        })
        .map_err(|error| error.to_string())?;
    application
        .evaluate(kairos_capital::EvaluateCapitalGroup { evaluated_at })
        .map_err(|error| error.to_string())?;
    let settling = application
        .snapshot()
        .plans
        .into_iter()
        .filter(|plan| plan.status == CapitalPlanStatus::Reconciling)
        .collect::<Vec<_>>();
    for plan in settling {
        let (Some(source), Some(destination)) = (
            facts_by_location.get(&plan.source),
            facts_by_location.get(&plan.destination),
        ) else {
            continue;
        };
        application
            .observe_settlement(ObserveCapitalSettlement {
                capital_group_id: capital_group_id.clone(),
                plan_id: plan.plan_id,
                source: source.clone(),
                destination: destination.clone(),
                observed_at: evaluated_at,
            })
            .map_err(|error| error.to_string())?;
    }
    if !state.automatic_execution {
        drop(runtime);
        publish_current_view(state).await?;
        return Ok(());
    }

    let active_plans = runtime
        .application()
        .snapshot()
        .plans
        .into_iter()
        .filter(|plan| {
            matches!(
                plan.status,
                CapitalPlanStatus::Authorized
                    | CapitalPlanStatus::Redeeming
                    | CapitalPlanStatus::AwaitingRedemption
                    | CapitalPlanStatus::Transferring
                    | CapitalPlanStatus::AwaitingTransfer
                    | CapitalPlanStatus::Subscribing
                    | CapitalPlanStatus::AwaitingSubscription
                    | CapitalPlanStatus::Available
                    | CapitalPlanStatus::Indeterminate
            )
        })
        .collect::<Vec<_>>();
    for plan in active_plans {
        if !reconciliation_is_due(state, &plan.plan_id, evaluated_at)? {
            continue;
        }
        validate_current_transfer_leases(
            state,
            plan.source.account_id.as_str(),
            plan.destination.account_id.as_str(),
        )?;
        let plan_id = plan.plan_id.clone();
        let result = runtime
            .execute_capital_plan(plan.plan_id, evaluated_at)
            .await
            .map_err(|error| error.to_string());
        schedule_reconciliation(state, &plan_id, evaluated_at)?;
        result?;
    }

    let snapshot = runtime.application().snapshot();
    for availability in snapshot
        .availability
        .iter()
        .filter(|view| view.readiness == CapitalReadiness::Ready && !view.deficit.is_zero())
    {
        let Some(route) = snapshot.routes.iter().find(|route| {
            route.enabled
                && matches!(
                    route.kind,
                    CapitalRouteKind::InternalTransfer
                        | CapitalRouteKind::AccountTransfer
                        | CapitalRouteKind::EarnRedemptionThenTransfer
                )
                && route.destination == availability.destination
        }) else {
            continue;
        };
        validate_current_transfer_leases(
            state,
            route.source.account_id.as_str(),
            route.destination.account_id.as_str(),
        )?;
        let plan_id = CapitalPlanId::new(format!(
            "capital-plan:{}:{}:{}",
            route.route_id,
            availability.account_watermark.get(),
            availability.risk_watermark.get()
        ))
        .map_err(|error| error.to_string())?;
        let expires_at = UnixNanos::new(
            evaluated_at
                .get()
                .checked_add(state.plan_ttl_nanos)
                .ok_or_else(|| "Capital plan expiry overflows UnixNanos".to_string())?,
        );
        let plan = match runtime
            .application_mut()
            .authorize_plan(AuthorizeCapitalPlan {
                capital_group_id: capital_group_id.clone(),
                plan_id,
                rebalance_decision_id: format!(
                    "capital-rebalance:{}:{}:{}",
                    route.route_id,
                    availability.account_watermark.get(),
                    availability.risk_watermark.get()
                ),
                route_id: route.route_id.clone(),
                source_authority: route.required_source_authority.clone(),
                created_at: evaluated_at,
                expires_at,
            }) {
            Ok(plan) => plan,
            Err(error) => {
                tracing::debug!(event = "capital_plan_not_authorized", route_id = %route.route_id, error = %error);
                continue;
            },
        };
        if plan.status == CapitalPlanStatus::Authorized {
            let plan_id = plan.plan_id.clone();
            runtime
                .execute_capital_plan(plan.plan_id, evaluated_at)
                .await
                .map_err(|error| error.to_string())?;
            schedule_reconciliation(state, &plan_id, evaluated_at)?;
        }
    }

    for route in snapshot
        .routes
        .iter()
        .filter(|route| route.enabled && route.kind == CapitalRouteKind::EarnSubscription)
    {
        let Some(availability) = snapshot
            .availability
            .iter()
            .find(|view| view.destination == route.source)
        else {
            continue;
        };
        validate_current_transfer_leases(
            state,
            route.source.account_id.as_str(),
            route.destination.account_id.as_str(),
        )?;
        let plan_id = CapitalPlanId::new(format!(
            "capital-yield-plan:{}:{}:{}",
            route.route_id,
            availability.account_watermark.get(),
            availability.risk_watermark.get()
        ))
        .map_err(|error| error.to_string())?;
        let expires_at = UnixNanos::new(
            evaluated_at
                .get()
                .checked_add(state.plan_ttl_nanos)
                .ok_or_else(|| "Capital plan expiry overflows UnixNanos".to_string())?,
        );
        let command = AuthorizeCapitalPlan {
            capital_group_id: capital_group_id.clone(),
            plan_id,
            rebalance_decision_id: format!(
                "capital-yield-deployment:{}:{}:{}",
                route.route_id,
                availability.account_watermark.get(),
                availability.risk_watermark.get()
            ),
            route_id: route.route_id.clone(),
            source_authority: route.required_source_authority.clone(),
            created_at: evaluated_at,
            expires_at,
        };
        let plan = match runtime.authorize_earn_subscription_plan(command).await {
            Ok(Some(plan)) => plan,
            Ok(None) => continue,
            Err(error) => {
                tracing::debug!(event = "capital_yield_plan_not_authorized", route_id = %route.route_id, error = %error);
                continue;
            },
        };
        let plan_id = plan.plan_id.clone();
        runtime
            .execute_capital_plan(plan.plan_id, evaluated_at)
            .await
            .map_err(|error| error.to_string())?;
        schedule_reconciliation(state, &plan_id, evaluated_at)?;
    }
    drop(runtime);
    publish_current_view(state).await?;
    Ok(())
}

async fn publish_current_view(state: &CapitalState) -> Result<(), String> {
    loop {
        let event = {
            let runtime = state.runtime.lock().await;
            runtime.application().pending_event().cloned()
        };
        let Some(event) = event else {
            break;
        };
        let event = capital_event(&event);
        state
            .event_publisher
            .publish(&event)
            .map_err(|error| error.to_string())?;
        state
            .runtime
            .lock()
            .await
            .application_mut()
            .acknowledge_event()
            .map_err(|error| error.to_string())?;
    }
    let view = capital_current_view(&state.runtime.lock().await.application().snapshot());
    state
        .view_publisher
        .lock()
        .map_err(|_| "Capital view publisher mutex is poisoned".to_string())?
        .publish(&view)
        .map_err(|error| error.to_string())
}

fn reconciliation_is_due(
    state: &CapitalState,
    plan_id: &CapitalPlanId,
    now: UnixNanos,
) -> Result<bool, String> {
    let schedule = state
        .reconcile_after
        .lock()
        .map_err(|_| "Capital reconciliation schedule mutex is poisoned".to_string())?;
    Ok(schedule
        .get(plan_id.as_str())
        .is_none_or(|next| now >= *next))
}

fn schedule_reconciliation(
    state: &CapitalState,
    plan_id: &CapitalPlanId,
    now: UnixNanos,
) -> Result<(), String> {
    let next = UnixNanos::new(now.get().saturating_add(5_000_000_000));
    state
        .reconcile_after
        .lock()
        .map_err(|_| "Capital reconciliation schedule mutex is poisoned".to_string())?
        .insert(plan_id.to_string(), next);
    Ok(())
}

fn read_location_facts(
    snapshot_root: &Path,
    location: &DomainLocation,
    strategy_id: &str,
    risk_state: kairos_protocol::generated::kairos::risk::v_2::RiskLatestState<'_>,
    risk_policy_version: Generation,
    risk_watermark: Sequence,
) -> Result<kairos_capital::CapitalFacts, String> {
    let account_id = location.account_id.as_str();
    let account_reader = AccountViewReader::open(
        snapshot_root,
        AccountViewKey::new(
            format!("account:{account_id}"),
            account_id,
            AccountViewKind::Current,
        )
        .map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    let account_frame = account_reader.read().map_err(|error| error.to_string())?;
    let account_root = account_frame
        .account_current()
        .map_err(|error| error.to_string())?;
    let segment = account_root
        .segments()
        .iter()
        .find(|segment| segment.segment_key() == location.segment.as_str())
        .ok_or_else(|| {
            format!(
                "Account '{}' has no Capital segment '{}'",
                location.account_id, location.segment
            )
        })?;
    let observed_available = segment
        .balances()
        .iter()
        .find(|balance| {
            balance.asset_code().unwrap_or(balance.asset_id()) == location.asset.as_str()
        })
        .and_then(|balance| balance.available())
        .map(quantity_from_decimal)
        .transpose()?
        .unwrap_or(Quantity::ZERO);
    let earn_holdings = segment
        .earn_holdings()
        .iter()
        .filter(|holding| holding.asset() == location.asset.as_str())
        .filter_map(|holding| {
            holding.redeemable().map(|redeemable| {
                quantity_from_decimal(redeemable).and_then(|redeemable_amount| {
                    Ok(kairos_capital::CapitalEarnHoldingFact {
                        product_id: holding.product_id().to_owned(),
                        principal: quantity_from_decimal(holding.principal())?,
                        redeemable_amount,
                        immediately_redeemable: holding.liquidity()
                            == kairos_protocol::generated::kairos::account::v_2::EarnLiquidity::IMMEDIATE,
                        active: holding.state()
                            == kairos_protocol::generated::kairos::account::v_2::EarnHoldingState::ACTIVE,
                    })
                })
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    let account_watermark = Sequence::new(
        segment
            .snapshot_watermark()
            .max(segment.event_watermark())
            .max(account_frame.envelope_metadata().applied_event_sequence),
    );
    let account_complete = account_root.metadata().completeness()
        == kairos_protocol::generated::kairos::common::v_2::ViewCompleteness::COMPLETE
        && segment.completeness()
            == kairos_protocol::generated::kairos::account::v_2::SegmentCompleteness::COMPLETE;
    let risk_capacity = risk_state
        .limits()
        .iter()
        .filter(|usage| {
            let risk_policy = usage.policy();
            let scope = risk_policy.scope();
            risk_policy.metric() == kairos_protocol::generated::kairos::risk::v_2::Metric::MARGIN
                && scope.account_id().is_none_or(|value| value == account_id)
                && scope.strategy_id().is_none_or(|value| value == strategy_id)
        })
        .map(|usage| quantity_from_decimal(usage.available()))
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .min()
        .unwrap_or(Quantity::ZERO);
    Ok(kairos_capital::CapitalFacts {
        destination: location.clone(),
        observed_available,
        account_watermark,
        account_observed_at: UnixNanos::new(segment.observed_at_unix_nanos()),
        account_complete,
        risk_capacity,
        risk_policy_version,
        risk_watermark,
        earn_holdings,
    })
}

fn quantity_from_decimal(
    value: &kairos_protocol::generated::kairos::common::v_2::Decimal64,
) -> Result<Quantity, String> {
    Quantity::new(value.mantissa(), value.scale()).map_err(|error| error.to_string())
}

fn now_unix_nanos() -> Result<UnixNanos, String> {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| error.to_string())?
        .as_nanos();
    let nanos = u64::try_from(nanos).map_err(|_| "system time exceeds u64 nanos".to_string())?;
    Ok(UnixNanos::new(nanos))
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
    let response_request_id = request.request_id.to_string();
    let response_objective_id = request.objective_id.to_string();
    let response_version = request.version.get();
    let objective = FundingObjective {
        objective_id: request.objective_id,
        version: request.version,
        strategy_id: request.strategy_id,
        destination: location(request.destination),
        desired_available: request.desired_available,
        required_by: request.required_by_unix_nanos,
        expires_at: request.expires_at_unix_nanos,
        priority: priority(request.priority),
        confidence_bps: request.confidence_bps,
        strategy_decision_id: request.strategy_decision_id,
    };
    let result = state
        .runtime
        .lock()
        .await
        .application_mut()
        .publish_funding_objective(PublishFundingObjective {
            capital_group_id: request.capital_group_id,
            objective,
            observed_at: request.observed_at_unix_nanos,
        })
        .map_err(|error| error.to_string());
    if result.is_ok() {
        if let Err(error) = publish_current_view(&state).await {
            return internal_error(&error);
        }
    }
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
    let response_request_id = request.request_id.to_string();
    let response_objective_id = request.objective_id.to_string();
    let response_version = request.expected_version.get();
    let result = state
        .runtime
        .lock()
        .await
        .application_mut()
        .cancel_funding_objective(CancelFundingObjective {
            capital_group_id: request.capital_group_id,
            objective_id: request.objective_id,
            expected_version: request.expected_version,
            observed_at: request.observed_at_unix_nanos,
        })
        .map_err(|error| error.to_string());
    if result.is_ok() {
        if let Err(error) = publish_current_view(&state).await {
            return internal_error(&error);
        }
    }
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
    let response_request_id = request.request_id.to_string();
    let response_demand_id = request.demand_id.to_string();
    let result = async {
        validate_lease_fence(
            &state.account_lease_fences,
            request.destination.account_id.as_str(),
            &request.destination_lease_fence,
        )?;
        let demand = CapitalDemand {
            demand_id: request.demand_id,
            idempotency_key: request.idempotency_key,
            strategy_id: request.strategy_id,
            destination: location(request.destination),
            observed_shortfall: request.observed_shortfall,
            observed_at: request.observed_at_unix_nanos,
            required_by: request.required_by_unix_nanos,
            expires_at: request.expires_at_unix_nanos,
            priority: priority(request.priority),
            confidence_bps: request.confidence_bps,
            account_watermark: request.account_watermark,
            risk_watermark: request.risk_watermark,
            launch_id: request.launch_id,
            instance_id: request.instance_id,
            destination_lease_fence: request.destination_lease_fence,
            causal_references: request.causal_references,
        };
        state
            .runtime
            .lock()
            .await
            .application_mut()
            .observe_demand(ObserveCapitalDemand {
                capital_group_id: request.capital_group_id,
                demand,
            })
            .map_err(|error| error.to_string())
    }
    .await;
    if result.is_ok() {
        if let Err(error) = publish_current_view(&state).await {
            return internal_error(&error);
        }
    }
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
                    "error": null,
                })),
            )
                .into_response()
        },
        Err(error) => (
            StatusCode::OK,
            Json(json!({
                "request_id": response_request_id,
                "demand_id": response_demand_id,
                "status": "rejected",
                "error": {
                    "code": "capital_rejected",
                    "message": error,
                    "retryable": false,
                    "details": {},
                },
            })),
        )
            .into_response(),
    }
}

async fn query_availability(
    State(state): State<Arc<CapitalState>>,
    Json(request): Json<QueryCapitalAvailabilityRequest>,
) -> Response {
    let location = DomainLocation {
        broker: request.location.broker.clone(),
        account_id: request.location.account_id.clone(),
        segment: request.location.segment.clone(),
        asset: request.location.asset.clone(),
    };
    let runtime = state.runtime.lock().await;
    let application = runtime.application();
    let snapshot = application.snapshot();
    if snapshot.capital_group_id != request.capital_group_id {
        return rejected_query("Capital availability belongs to another group");
    }
    let Some(view) = application.availability(&location) else {
        return rejected_query("Capital location has not been evaluated");
    };
    let Some(policy) = snapshot
        .policies
        .iter()
        .find(|policy| policy.destination == location)
    else {
        return internal_error("Capital availability policy disappeared");
    };
    Json(CapitalAvailabilityResponse {
        request_id: request.request_id,
        capital_group_id: request.capital_group_id,
        location: request.location,
        readiness: match view.readiness {
            kairos_capital::CapitalReadiness::WaitingForFacts => {
                CapitalReadinessStatus::WaitingForFacts
            },
            kairos_capital::CapitalReadiness::Degraded => CapitalReadinessStatus::Degraded,
            kairos_capital::CapitalReadiness::Ready => CapitalReadinessStatus::Ready,
        },
        policy_minimum: policy.minimum,
        policy_default_target: policy.default_target,
        policy_maximum: policy.maximum,
        policy_version: view.policy_version,
        active_objective_ids: view.active_objective_ids.clone(),
        active_demand_ids: view.active_demand_ids.clone(),
        desired_target: view.desired_target,
        observed_available: view.observed_available,
        effective_target: view.effective_target,
        deficit: view.deficit,
        account_watermark: view.account_watermark,
        risk_policy_version: view.risk_policy_version,
        risk_watermark: view.risk_watermark,
        evaluated_at_unix_nanos: view.evaluated_at,
        reason: view.reason.clone(),
    })
    .into_response()
}

fn rejected_query(message: &str) -> Response {
    (
        StatusCode::NOT_FOUND,
        Json(json!({"error": message, "retryable": true})),
    )
        .into_response()
}

fn internal_error(message: &str) -> Response {
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(json!({"error": message, "retryable": true})),
    )
        .into_response()
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

fn validate_current_source_lease(state: &CapitalState, account_id: &str) -> Result<(), String> {
    let expected_fence = state
        .account_lease_fences
        .get(account_id)
        .ok_or_else(|| format!("source account {account_id} is outside the Capital group"))?;
    let expected_broker = state
        .account_brokers
        .get(account_id)
        .ok_or_else(|| format!("source account {account_id} has no broker identity"))?;
    let entries = std::fs::read_dir(&state.account_lease_root).map_err(|error| {
        format!(
            "cannot read current Account lease registry '{}': {error}",
            state.account_lease_root.display()
        )
    })?;
    for entry in entries {
        let owner = entry
            .map_err(|error| error.to_string())?
            .path()
            .join("owner.json");
        if !owner.is_file() {
            continue;
        }
        let record: AccountLeaseRecord =
            serde_json::from_slice(&std::fs::read(&owner).map_err(|error| error.to_string())?)
                .map_err(|error| format!("invalid Account lease '{}': {error}", owner.display()))?;
        if record.account_id == account_id && record.broker == *expected_broker {
            if record.launch_instance_id != state.instance_id
                || record.fencing_token != *expected_fence
            {
                return Err(format!(
                    "source account {account_id} is no longer leased by this Capital instance"
                ));
            }
            return Ok(());
        }
    }
    Err(format!(
        "source account {account_id} has no current Account lease"
    ))
}

fn validate_current_transfer_leases(
    state: &CapitalState,
    source_account_id: &str,
    destination_account_id: &str,
) -> Result<(), String> {
    validate_current_source_lease(state, source_account_id)?;
    if source_account_id == destination_account_id {
        return Ok(());
    }
    let source_controller = state.account_controllers.get(source_account_id);
    let destination_controller = state.account_controllers.get(destination_account_id);
    if source_controller != destination_controller {
        return Err("cross-Account transfer no longer has one shared controller".into());
    }
    let controller = source_controller
        .ok_or_else(|| "cross-Account transfer has no controller lease".to_string())?;
    if controller != source_account_id {
        validate_current_source_lease(state, controller)?;
    }
    Ok(())
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
            "error": null,
        })),
    )
        .into_response()
}

fn location(value: kairos_capital_contract::FundingLocation) -> DomainLocation {
    DomainLocation {
        broker: value.broker,
        account_id: value.account_id,
        segment: value.segment,
        asset: value.asset,
    }
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
            "error": {
                "code": "capital_rejected",
                "message": error,
                "retryable": false,
                "details": {},
            },
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
