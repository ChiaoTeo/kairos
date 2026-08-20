use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use clap::Parser;
use kairos_capital::composition::{
    CapitalConnectionAccount, CapitalHostConfig, build_capital_host,
    compose_capital_integration_connections, compose_persistent_capital_process,
    validate_capital_transfer_product,
};
use kairos_capital::{
    CapitalGroupConfig, CapitalGroupId, CapitalGroupMember, CapitalPolicy, CapitalRouteKind,
    CapitalSettlementClass, CapitalTransferRoute, EvaluateCapitalGroup,
    FundingLocation as DomainLocation, UpdateCapitalPolicy, UpdateCapitalRoute,
};
use kairos_primitives::account::{AccountId, BrokerId, SegmentKey};
use kairos_primitives::decimal::Quantity;
use kairos_primitives::runtime::{InstanceIdentity, StrategyId};
use kairos_primitives::time::{Generation, UnixNanos};
use kairos_workspace::workspace::Workspace;
use serde::Deserialize;

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
    socket: PathBuf,
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
    #[serde(default)]
    capital_readiness_role: CapitalMemberReadinessRoleConfig,
}

#[derive(Clone, Copy, Debug, Default, Deserialize)]
#[serde(rename_all = "snake_case")]
enum CapitalMemberReadinessRoleConfig {
    #[default]
    Critical,
    Optional,
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
            account_socket: account.socket.clone(),
            permitted_segments: account.permitted_segments.clone(),
            segment_products: account.segment_products.clone(),
        })
        .collect::<Vec<_>>();
    let credential_config = workspace.existing_path(
        &["config", "credentials", "credentials.toml"],
        &["credentials", "credentials.toml"],
    )?;
    let connections = compose_capital_integration_connections(
        &credential_config,
        &args.launch_mode,
        connection_accounts,
    )?;
    let mut runtime = compose_persistent_capital_process(
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
    let identity = InstanceIdentity::new(workspace.id(), &args.launch_id, &args.instance_id)?;
    let host = build_capital_host(CapitalHostConfig {
        runtime,
        conflux: kairos_capital::CapitalConfluxConfig {
            snapshot_root,
            instance_id: args.instance_id,
            identity,
            account_lease_fences,
            account_brokers,
            account_controllers,
            account_lease_root: workspace.state_root().join("account-locks"),
            automatic_execution: normalized.capital.automatic_execution,
            plan_ttl_nanos,
        },
        socket_path: instance.socket("capital")?,
        health_file: Some(instance.health("capital")?),
        aeron_dir: args.aeron_dir,
        event_channel: args.aeron_channel,
        event_stream_id: args.capital_events_stream_id,
        ingress_capacity: 256,
    })?;
    let _outcome = host.run().await?;
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
                readiness_role: match value.capital_readiness_role {
                    CapitalMemberReadinessRoleConfig::Critical => {
                        kairos_capital::CapitalMemberReadinessRole::Critical
                    },
                    CapitalMemberReadinessRoleConfig::Optional => {
                        kairos_capital::CapitalMemberReadinessRole::Optional
                    },
                },
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

fn now_unix_nanos() -> Result<UnixNanos, String> {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| error.to_string())?
        .as_nanos();
    let nanos = u64::try_from(nanos).map_err(|_| "system time exceeds u64 nanos".to_string())?;
    Ok(UnixNanos::new(nanos))
}
