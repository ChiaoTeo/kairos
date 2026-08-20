use clap::Parser;
use kairos_conflux::load_workspace_credential;
use kairos_execution::composition::{
    ExecutionConnectionOptions, ExecutionHostConfig, ExecutionWriterFence, build_execution_host,
};
use kairos_workspace::workspace::{Workspace, WorkspaceProcessLock};
use secrecy::ExposeSecret;
use serde::Deserialize;

#[tokio::main(flavor = "multi_thread", worker_threads = 4)]
async fn main() {
    kairos_workspace::logging::init("execution");
    let result = tokio::task::LocalSet::new().run_until(run()).await;
    if let Err(error) = &result {
        tracing::error!(event = "process_failed", component = "execution", error = %error, "execution server failed");
    }
    kairos_workspace::logging::shutdown();
    if let Err(error) = result {
        eprintln!("kairos-execution-server: {error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    tracing::info!(event = "process_start", component = "execution", instance_id = %args.instance_id, launch_id = %args.launch_id, "starting execution server");
    let workspace = Workspace::open(args.workspace.clone())?;
    let instance = workspace.instance(&args.launch_mode, &args.launch_id, &args.instance_id)?;
    instance.prepare()?;
    let _process_lock = instance.process_lock("execution")?;
    let route_options = args.connection_options_list(&workspace)?;
    let simulated = route_options.iter().all(|options| {
        matches!(
            options.participant_id.trim().to_ascii_lowercase().as_str(),
            "simulated" | "paper"
        )
    });
    let _provider_process_locks =
        acquire_exclusive_provider_process_locks(&workspace, &route_options)?;
    // Paper/backtest has a single instance-owned simulator and deliberately
    // does not impersonate a live provider writer lease.
    let writer_fences = if simulated {
        Vec::new()
    } else {
        acquire_execution_writer_leases(&workspace, &args.launch_mode, &route_options)?
    };
    let state = instance.state(&["execution", "execution-state.sqlite"])?;
    let audit = instance.state(&["execution", "execution-audit.sqlite"])?;
    let view_root = instance.snapshot(&[])?;
    let transport_identity = kairos_primitives::runtime::InstanceIdentity::new(
        workspace.id(),
        instance.launch_id(),
        instance.instance_id(),
    )?;
    let reference_database = workspace.child(&["state", "reference", "reference.sqlite"])?;
    let manifest = instance.component_manifest()?;
    let socket = instance.socket("execution")?;
    build_execution_host(ExecutionHostConfig {
        actor_id: "execution".into(),
        route_options,
        writer_fences,
        state_path: state,
        audit_path: audit,
        reference_database,
        manifest_path: manifest,
        socket_path: socket,
        view_root,
        transport_identity,
        source_id: format!("execution:{}", args.instance_id),
        simulated,
        backtest: args.launch_mode == "backtest",
        confirm_live: args.confirm_live,
        aeron_dir: args.aeron_dir,
        aeron_channel: args.aeron_channel,
        execution_events_stream_id: args.execution_events_stream_id,
    })?
    .run()
    .await?;
    Ok(())
}

fn acquire_exclusive_provider_process_locks(
    workspace: &Workspace,
    routes: &[ExecutionConnectionOptions],
) -> Result<Vec<WorkspaceProcessLock>, Box<dyn std::error::Error>> {
    let identities = routes
        .iter()
        .filter(|route| route.participant_id.trim().eq_ignore_ascii_case("ibkr"))
        .map(|route| {
            format!(
                "ibkr|{}|{}|client-id:{}",
                route.host.trim().to_ascii_lowercase(),
                route.port,
                route.client_id
            )
        })
        .collect::<std::collections::BTreeSet<_>>();
    identities
        .into_iter()
        .map(|identity| {
            workspace
                .exclusive_process_lock("ibkr-client", &identity)
                .map_err(|error| {
                    std::io::Error::new(
                        error.kind(),
                        format!("IBKR client identity is already allocated ({identity}): {error}"),
                    )
                    .into()
                })
        })
        .collect()
}

fn acquire_execution_writer_leases(
    workspace: &Workspace,
    environment: &str,
    routes: &[ExecutionConnectionOptions],
) -> Result<Vec<ExecutionWriterFence>, Box<dyn std::error::Error>> {
    let identities = routes
        .iter()
        .map(|route| {
            let identity = format!(
                    "participant:{}|environment:{}|principal:{}|account:{}|segment:{}|product:{}|trading-mode:{}",
                    route.participant_id.trim().to_ascii_lowercase(),
                    environment.trim().to_ascii_lowercase(),
                    route.principal_scope_id.trim(),
                    route.account_id.trim(),
                    route.segment_key.trim(),
                    route.product.trim().to_ascii_lowercase(),
                    route.trading_mode.as_deref().unwrap_or("").trim().to_ascii_lowercase(),
                );
            (identity, route.account_id.clone(), route.segment_key.clone())
        })
        .collect::<std::collections::BTreeSet<_>>();
    identities
        .into_iter()
        .map(|(identity, account_id, segment_key)| {
            let lease = workspace
                .fenced_lease("execution-writer", &identity)
                .map_err(|error| {
                    std::io::Error::new(
                        error.kind(),
                        format!("Execution writer lease is unavailable ({identity}): {error}"),
                    )
                })?;
            ExecutionWriterFence::new(account_id, segment_key, lease)
                .map_err(|error| std::io::Error::other(error).into())
        })
        .collect()
}

#[derive(Debug, Parser)]
#[command(name = "kairos-execution", about = "Run the Execution actor process")]
struct Args {
    #[arg(long)]
    workspace: String,
    #[arg(long, visible_alias = "launch-mode", default_value = "paper")]
    launch_mode: String,
    #[arg(long)]
    launch_id: String,
    #[arg(long, default_value = "default")]
    instance_id: String,
    #[arg(long)]
    confirm_live: bool,
    #[arg(long, env = "AERON_DIR")]
    aeron_dir: Option<String>,
    #[arg(long, default_value = kairos_transport::DEFAULT_CHANNEL)]
    aeron_channel: String,
    #[arg(
        long,
        default_value_t = kairos_transport::stream_ids::EXECUTION_EVENTS,
        value_parser = clap::value_parser!(i32).range(1..)
    )]
    execution_events_stream_id: i32,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ExecutionRouteConfig {
    route_id: String,
    #[serde(default = "default_true")]
    required: bool,
    account_id: String,
    segment_key: String,
    participant_id: String,
    product: String,
    #[serde(default)]
    trading_mode: Option<String>,
    #[serde(default)]
    credential_id: Option<String>,
    #[serde(default)]
    base_url: Option<String>,
    #[serde(default)]
    websocket_url: Option<String>,
    #[serde(default)]
    isolated_symbol: Option<String>,
    #[serde(default)]
    instruments: Vec<ExecutionInstrumentRouteConfig>,
    #[serde(default)]
    request_weight_per_minute: Option<u32>,
    #[serde(default)]
    cancel_reserve_weight: Option<u32>,
    #[serde(default)]
    order_event_queue_capacity: Option<usize>,
    #[serde(default)]
    egress_scope_id: Option<String>,
    #[serde(default)]
    principal_scope_id: Option<String>,
    #[serde(default)]
    orders_per_10_seconds: Option<u32>,
    #[serde(default)]
    orders_per_day: Option<u32>,
    #[serde(default)]
    host: Option<String>,
    #[serde(default)]
    port: Option<u16>,
    #[serde(default)]
    client_id: Option<i32>,
    #[serde(default)]
    initial_margin_rate_bps: Option<u32>,
    #[serde(default)]
    margin_rule_id: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ExecutionInstrumentRouteConfig {
    instrument_id: String,
    provider_symbol: String,
    #[serde(default)]
    destination_market_id: Option<String>,
}

#[derive(Debug, Deserialize)]
struct NormalizedLaunchConfig {
    #[serde(default)]
    accounts: Vec<String>,
    execution: NormalizedExecutionConfig,
}

#[derive(Debug, Deserialize)]
struct NormalizedExecutionConfig {
    #[serde(default = "default_true")]
    enabled: bool,
    routes: Vec<ExecutionRouteConfig>,
}

fn default_true() -> bool {
    true
}

impl Args {
    fn connection_options_list(
        &self,
        workspace: &Workspace,
    ) -> Result<Vec<ExecutionConnectionOptions>, Box<dyn std::error::Error>> {
        let instance = workspace.instance(&self.launch_mode, &self.launch_id, &self.instance_id)?;
        let config_path = instance.normalized_config()?;
        let config: NormalizedLaunchConfig = serde_json::from_slice(&std::fs::read(&config_path)?)
            .map_err(|error| {
                format!(
                    "invalid normalized launch configuration {}: {error}",
                    config_path.display()
                )
            })?;
        if !config.execution.enabled {
            return Err("Execution process cannot start when execution.enabled is false".into());
        }
        let routes = config.execution.routes;
        if routes.is_empty() {
            return Err("execution.routes must contain at least one execution route".into());
        }
        if !config.accounts.is_empty() {
            for route in &routes {
                if !config.accounts.contains(&route.account_id) {
                    return Err(format!(
                        "execution route {} references an account not enabled by the launch: {}",
                        route.route_id, route.account_id
                    )
                    .into());
                }
            }
        }
        routes
            .into_iter()
            .map(|route| self.route_connection_options(workspace, route))
            .collect()
    }

    fn route_connection_options(
        &self,
        workspace: &Workspace,
        route: ExecutionRouteConfig,
    ) -> Result<ExecutionConnectionOptions, Box<dyn std::error::Error>> {
        if route.route_id.trim().is_empty()
            || route.account_id.trim().is_empty()
            || route.segment_key.trim().is_empty()
            || route.participant_id.trim().is_empty()
            || route.product.trim().is_empty()
        {
            return Err(
                "execution route_id, account_id, segment_key, participant_id, and product are required"
                    .into(),
            );
        }
        let credentials_root =
            workspace.existing_path(&["config", "credentials"], &["credentials"])?;
        let stored = load_workspace_credential(
            &credentials_root,
            &route.participant_id,
            route.credential_id.as_deref(),
        )?;
        let (default_base_url, default_websocket_url) =
            provider_endpoints(&route.participant_id, &route.product);
        Ok(ExecutionConnectionOptions {
            route_id: route.route_id.clone(),
            required: route.required,
            account_id: route.account_id,
            segment_key: route.segment_key,
            participant_id: route.participant_id,
            product: route.product,
            trading_mode: route.trading_mode,
            api_key: stored
                .as_ref()
                .map(|value| value.api_key.clone())
                .unwrap_or_default()
                .into(),
            secret: stored
                .as_ref()
                .map(|value| value.secret.expose_secret().to_owned())
                .unwrap_or_default()
                .into(),
            passphrase: stored
                .as_ref()
                .map(|value| value.passphrase.clone())
                .unwrap_or_default()
                .into(),
            base_url: route.base_url.unwrap_or_else(|| default_base_url.into()),
            websocket_url: route
                .websocket_url
                .unwrap_or_else(|| default_websocket_url.into()),
            isolated_symbol: route.isolated_symbol,
            instruments: route
                .instruments
                .into_iter()
                .map(
                    |value| kairos_execution::composition::ExecutionInstrumentRoute {
                        instrument_id: value.instrument_id,
                        provider_symbol: value.provider_symbol,
                        destination_market_id: value.destination_market_id,
                    },
                )
                .collect(),
            request_weight_per_minute: route.request_weight_per_minute.unwrap_or(1_000),
            cancel_reserve_weight: route.cancel_reserve_weight.unwrap_or(50),
            order_event_queue_capacity: route.order_event_queue_capacity.unwrap_or(1_024),
            shared_quota_ledger_path: Some(
                workspace
                    .state_root()
                    .join("integration")
                    .join("provider-quota.mmap"),
            ),
            egress_scope_id: route
                .egress_scope_id
                .unwrap_or_else(|| "default-egress".into()),
            principal_scope_id: route
                .principal_scope_id
                .unwrap_or_else(|| route.route_id.clone()),
            orders_per_10_seconds: route.orders_per_10_seconds.unwrap_or(50),
            orders_per_day: route.orders_per_day.unwrap_or(160_000),
            host: route.host.unwrap_or_else(|| "127.0.0.1".into()),
            port: route.port.unwrap_or(4002),
            client_id: route.client_id.unwrap_or(0),
            initial_margin_rate_bps: route.initial_margin_rate_bps,
            margin_rule_id: route.margin_rule_id,
        })
    }
}

fn provider_endpoints(provider: &str, product: &str) -> (&'static str, &'static str) {
    match (
        provider.trim().to_ascii_lowercase().as_str(),
        product.trim().to_ascii_lowercase().as_str(),
    ) {
        ("binance", "spot") => (
            "https://api.binance.com",
            "wss://ws-api.binance.com:443/ws-api/v3",
        ),
        ("binance", "usd-m-futures") => ("https://fapi.binance.com", "wss://fstream.binance.com"),
        ("binance", "coin-m-futures") => ("https://dapi.binance.com", "wss://dstream.binance.com"),
        ("binance", "options" | "option") => (
            "https://eapi.binance.com",
            "wss://nbstream.binance.com/eoptions/private/stream",
        ),
        ("binance", "cross-margin" | "isolated-margin") => {
            ("https://api.binance.com", "wss://stream.binance.com:9443")
        },
        ("okx" | "okex", "spot" | "margin" | "swap" | "futures" | "option" | "options") => {
            ("https://www.okx.com", "wss://ws.okx.com:8443/ws/v5/private")
        },
        ("simulated" | "paper", _) | ("ibkr", "spot" | "equity") => ("", ""),
        _ => ("", ""),
    }
}

#[cfg(test)]
mod tests {
    use kairos_execution::composition::ExecutionConnectionOptions;
    use kairos_workspace::workspace::Workspace;
    use secrecy::SecretString;

    use super::{
        Args, ExecutionRouteConfig, acquire_exclusive_provider_process_locks,
        acquire_execution_writer_leases, provider_endpoints,
    };

    #[test]
    fn route_config_accepts_credential_references_and_rejects_inline_secrets() {
        let routes: Vec<ExecutionRouteConfig> = serde_json::from_str(
            r#"[{"route_id":"okx-main","account_id":"main","segment_key":"swap","participant_id":"okx","product":"swap","credential_id":"okx-main"}]"#,
        )
        .unwrap();
        assert_eq!(routes[0].credential_id.as_deref(), Some("okx-main"));
        assert!(serde_json::from_str::<Vec<ExecutionRouteConfig>>(
            r#"[{"route_id":"okx-main","account_id":"main","segment_key":"swap","participant_id":"okx","product":"swap","api_key":"secret"}]"#,
        )
        .is_err());
    }

    #[test]
    fn server_loads_explicit_routes_from_the_normalized_launch_config() {
        let root = tempfile::tempdir().unwrap();
        let workspace = Workspace::init(root.path().join("workspace"), "test").unwrap();
        let instance = workspace.instance("paper", "launch", "instance").unwrap();
        instance.prepare().unwrap();
        std::fs::create_dir_all(instance.paths().config_root()).unwrap();
        std::fs::write(
            instance.normalized_config().unwrap(),
            r#"{"accounts":["secondary"],"execution":{"enabled":true,"routes":[{"route_id":"secondary-okx","account_id":"secondary","segment_key":"swap","participant_id":"simulated","product":"swap"}]}}"#,
        )
        .unwrap();
        let args = Args {
            workspace: workspace.root().display().to_string(),
            launch_mode: "paper".into(),
            launch_id: "launch".into(),
            instance_id: "instance".into(),
            confirm_live: false,
            aeron_dir: None,
            aeron_channel: kairos_transport::DEFAULT_CHANNEL.into(),
            execution_events_stream_id: kairos_transport::stream_ids::EXECUTION_EVENTS,
        };
        let routes = args.connection_options_list(&workspace).unwrap();
        assert_eq!(routes.len(), 1);
        assert_eq!(routes[0].account_id, "secondary");
        assert_eq!(routes[0].segment_key, "swap");
        assert_eq!(routes[0].participant_id, "simulated");

        std::fs::write(
            instance.normalized_config().unwrap(),
            r#"{"accounts":["main"],"execution":{"enabled":true,"routes":[{"route_id":"secondary-okx","account_id":"secondary","segment_key":"swap","participant_id":"simulated","product":"swap"}]}}"#,
        )
        .unwrap();
        let error = args
            .connection_options_list(&workspace)
            .unwrap_err()
            .to_string();
        assert!(error.contains("account not enabled by the launch"));
    }

    #[test]
    fn ibkr_client_identity_is_exclusive_before_provider_composition() {
        let root = tempfile::tempdir().unwrap();
        let workspace = Workspace::init(root.path().join("workspace"), "test").unwrap();
        let route = ExecutionConnectionOptions {
            route_id: "ibkr-main".into(),
            required: true,
            account_id: "DU123".into(),
            segment_key: "equity".into(),
            participant_id: "ibkr".into(),
            product: "equity".into(),
            trading_mode: None,
            api_key: SecretString::from(String::new()),
            secret: SecretString::from(String::new()),
            passphrase: SecretString::from(String::new()),
            base_url: String::new(),
            websocket_url: String::new(),
            isolated_symbol: None,
            instruments: Vec::new(),
            request_weight_per_minute: 1,
            cancel_reserve_weight: 0,
            order_event_queue_capacity: 16,
            shared_quota_ledger_path: None,
            egress_scope_id: "unused".into(),
            principal_scope_id: "ibkr-client-7".into(),
            orders_per_10_seconds: 1,
            orders_per_day: 1,
            host: "127.0.0.1".into(),
            port: 4002,
            client_id: 7,
            initial_margin_rate_bps: None,
            margin_rule_id: None,
        };
        let first = acquire_exclusive_provider_process_locks(&workspace, &[route.clone()]).unwrap();
        let error = acquire_exclusive_provider_process_locks(&workspace, &[route.clone()])
            .unwrap_err()
            .to_string();
        assert!(error.contains("already allocated"));
        drop(first);

        let first = acquire_execution_writer_leases(&workspace, "live", &[route.clone()]).unwrap();
        assert_eq!(first[0].token(), 1);
        let error = acquire_execution_writer_leases(&workspace, "live", &[route.clone()])
            .unwrap_err()
            .to_string();
        assert!(error.contains("writer lease is unavailable"));
        drop(first);
        let second = acquire_execution_writer_leases(&workspace, "live", &[route]).unwrap();
        assert_eq!(second[0].token(), 2);
    }

    #[test]
    fn participant_defaults_select_native_private_endpoints() {
        assert_eq!(provider_endpoints("okx", "spot").0, "https://www.okx.com");
        assert!(
            provider_endpoints("okx", "spot")
                .1
                .contains("/ws/v5/private")
        );
        assert_eq!(
            provider_endpoints("binance", "options"),
            (
                "https://eapi.binance.com",
                "wss://nbstream.binance.com/eoptions/private/stream"
            )
        );
    }
}
