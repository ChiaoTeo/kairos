use std::collections::BTreeMap;
use std::time::Duration;

use clap::Parser;
use kairos_risk::composition::{RiskHostConfig, build_risk_host};
use kairos_risk::{Amount, EnforcementMode, Metric, PolicyScope, RiskPolicy};
use kairos_workspace::workspace::Workspace;
use serde::Deserialize;

#[tokio::main(flavor = "current_thread")]
async fn main() {
    kairos_workspace::logging::init("risk");
    let result = run().await;
    if let Err(error) = &result {
        tracing::error!(event = "process_failed", component = "risk", error = %error, "risk server failed");
    }
    kairos_workspace::logging::shutdown();
    if let Err(error) = result {
        eprintln!("kairos-risk-server: {error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    tracing::info!(event = "process_start", component = "risk", instance_id = %args.instance_id, launch_id = %args.launch_id, "starting risk server");
    let workspace = Workspace::open(args.workspace)?;
    let instance = workspace.instance(&args.launch_mode, &args.launch_id, &args.instance_id)?;
    instance.prepare()?;
    let transport_identity = kairos_primitives::runtime::InstanceIdentity::new(
        workspace.id(),
        instance.launch_id(),
        instance.instance_id(),
    )?;
    let _process_lock = instance.process_lock("risk")?;
    let socket = instance.socket("risk")?;
    let health = instance.health("risk")?;
    let state = instance.state(&["risk", "risk-state.json"])?;
    let view_root = instance.snapshot(&[])?;
    let normalized_path = instance.normalized_config()?;
    let policies = load_risk_policies(&workspace, &normalized_path, &args.launch_mode)?;
    let host = build_risk_host(RiskHostConfig {
        actor_id: format!("risk:{}", args.instance_id),
        policies,
        state_path: Some(state),
        socket_path: socket,
        health_file: Some(health),
        interval: Duration::from_millis(args.interval_ms),
        replay_clock: args.launch_mode == "backtest",
        view_root,
        aeron_dir: args.aeron_dir,
        event_channel: args.aeron_channel,
        event_stream_id: args.risk_events_stream_id,
        identity: transport_identity,
    })?;
    tokio::task::LocalSet::new().run_until(host.run()).await?;
    Ok(())
}

fn load_risk_policies(
    workspace: &Workspace,
    normalized_path: &std::path::Path,
    launch_mode: &str,
) -> Result<Vec<RiskPolicy>, Box<dyn std::error::Error>> {
    let normalized: NormalizedLaunchConfig =
        serde_json::from_slice(&std::fs::read(&normalized_path)?)?;
    let profile_name = normalized
        .risk_profile
        .as_deref()
        .or_else(|| matches!(launch_mode, "backtest" | "paper").then_some("simulation-default"));
    let policies = if profile_name == Some("simulation-default") {
        if !matches!(launch_mode, "backtest" | "paper") {
            return Err("simulation-default Risk profile is forbidden for a live launch".into());
        }
        vec![RiskPolicy {
            policy_id: kairos_primitives::risk::PolicyId::new("simulation-default-notional")?,
            version: 1.into(),
            scope: PolicyScope {
                account_id: None,
                strategy_id: None,
                instrument_id: None,
                exchange_id: None,
            },
            metric: Metric::Notional,
            // The policy is intentionally permissive but real: every replay
            // order still passes Risk authorization and creates a reservation.
            limit: Amount::new(1_000_000_000_000, 0)?,
            enforcement: EnforcementMode::Reject,
            valid_from_unix_nanos: 0.into(),
            valid_until_unix_nanos: None,
            window_nanos: None,
        }]
    } else if let Some(profile_name) = profile_name {
        let config: RiskConfig = workspace.read_section("risk")?;
        config
            .profiles
            .get(profile_name)
            .ok_or_else(|| format!("unknown Risk profile: {profile_name}"))?
            .policies
            .clone()
    } else {
        return Err("Risk profile is required for a live launch".into());
    };
    for policy in &policies {
        policy.validate()?;
    }
    Ok(policies)
}

#[derive(Debug, Parser)]
#[command(name = "kairos-risk", about = "Run the Risk actor process")]
struct Args {
    #[arg(long)]
    workspace: String,

    #[arg(long, visible_alias = "launch-mode", default_value = "paper")]
    launch_mode: String,

    #[arg(long)]
    launch_id: String,

    #[arg(long, default_value = "default")]
    instance_id: String,

    #[arg(long, default_value_t = 1_000, value_parser = clap::value_parser!(u64).range(1..))]
    interval_ms: u64,

    #[arg(long, env = "AERON_DIR")]
    aeron_dir: Option<String>,

    #[arg(long, default_value = kairos_conflux::DEFAULT_AERON_CHANNEL)]
    aeron_channel: String,

    #[arg(
        long,
        default_value_t = kairos_conflux::output_stream_ids::RISK_EVENTS
    )]
    risk_events_stream_id: i32,
}

#[derive(Debug, Deserialize)]
struct NormalizedLaunchConfig {
    #[serde(default)]
    risk_profile: Option<String>,
}

#[derive(Debug, Default, Deserialize)]
struct RiskConfig {
    #[serde(default)]
    profiles: BTreeMap<String, RiskProfileConfig>,
}

#[derive(Debug, Deserialize)]
struct RiskProfileConfig {
    policies: Vec<RiskPolicy>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn simulation_uses_a_named_cross_account_default_profile() {
        let root = tempfile::tempdir().unwrap();
        let workspace = Workspace::init(root.path().join("workspace"), "test").unwrap();
        let normalized = root.path().join("normalized.json");
        std::fs::write(&normalized, r#"{"risk_profile":null}"#).unwrap();
        let policies = load_risk_policies(&workspace, &normalized, "paper").unwrap();
        assert_eq!(policies.len(), 1);
        assert!(policies[0].scope.account_id.is_none());
        assert!(policies[0].scope.exchange_id.is_none());
    }

    #[test]
    fn live_launch_requires_an_explicit_profile() {
        let root = tempfile::tempdir().unwrap();
        let workspace = Workspace::init(root.path().join("workspace"), "test").unwrap();
        let normalized = root.path().join("normalized.json");
        std::fs::write(&normalized, r#"{"risk_profile":null}"#).unwrap();
        let error = load_risk_policies(&workspace, &normalized, "live")
            .unwrap_err()
            .to_string();
        assert!(error.contains("Risk profile is required"));
    }

    #[test]
    fn live_launch_rejects_the_builtin_simulation_profile() {
        let root = tempfile::tempdir().unwrap();
        let workspace = Workspace::init(root.path().join("workspace"), "test").unwrap();
        let normalized = root.path().join("normalized.json");
        std::fs::write(&normalized, r#"{"risk_profile":"simulation-default"}"#).unwrap();
        let error = load_risk_policies(&workspace, &normalized, "live")
            .unwrap_err()
            .to_string();
        assert!(error.contains("forbidden for a live launch"));
    }

    #[test]
    fn live_profile_loads_workspace_owned_cross_account_policies() {
        let root = tempfile::tempdir().unwrap();
        let workspace_root = root.path().join("workspace");
        Workspace::init(&workspace_root, "test").unwrap();
        let manifest = workspace_root.join("workspace.toml");
        let mut contents = std::fs::read_to_string(&manifest).unwrap();
        contents.push_str(
            r#"
[risk.profiles.production]

[[risk.profiles.production.policies]]
policy_id = "production-notional"
version = 1
scope = {}
metric = "notional"
limit = "1000000"
enforcement = "reject"
valid_from_unix_nanos = 0
"#,
        );
        std::fs::write(&manifest, contents).unwrap();
        let workspace = Workspace::open(&workspace_root).unwrap();
        let normalized = root.path().join("normalized.json");
        std::fs::write(&normalized, r#"{"risk_profile":"production"}"#).unwrap();

        let policies = load_risk_policies(&workspace, &normalized, "live").unwrap();
        assert_eq!(policies.len(), 1);
        assert_eq!(policies[0].policy_id.as_str(), "production-notional");
        assert!(policies[0].scope.account_id.is_none());
    }
}
