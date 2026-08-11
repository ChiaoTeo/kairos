use clap::Parser;
use kairos_execution::application::ExecutionApplication;
use kairos_execution::composition::{
    compose_execution_routes, ExecutionConnectionOptions, ExecutionSimulator,
    QueuedExecutionPreflight, SharedExecutionSnapshotPublisher, SharedIntentSnapshotPublisher,
    SimulationConfig, SocketExecutionPreflight, SqlxExecutionStore,
};
use kairos_execution::credentials::load_workspace_credential;
use kairos_execution::{ExecutionProcess, SqlxExecutionAudit};
use kairos_workspace::workspace::{Workspace, WorkspaceProcessLock};
use serde::Deserialize;

#[tokio::main(flavor = "multi_thread", worker_threads = 4)]
async fn main() {
    kairos_workspace::logging::init("execution");
    let result = run().await;
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
    tracing::info!(event = "process_start", component = "execution", instance_id = %args.instance_id, launch_id = %args.launch_id, provider = %args.provider, "starting execution server");
    let workspace = Workspace::open(args.workspace.clone())?;
    let instance = workspace.instance(&args.launch_mode, &args.launch_id, &args.instance_id)?;
    instance.prepare()?;
    let _process_lock = instance.process_lock("execution")?;
    let route_options = args.connection_options_list(&workspace)?;
    let _provider_process_locks =
        acquire_exclusive_provider_process_locks(&workspace, &route_options)?;
    let state = instance.state(&["execution", "execution-state.sqlite"])?;
    let audit = instance.state(&["execution", "execution-audit.sqlite"])?;
    let execution_snapshot = instance.service_snapshot("execution")?;
    let intent_snapshot = instance.service_snapshot("intent")?;
    if let Some(parent) = execution_snapshot.parent() {
        std::fs::create_dir_all(parent)?;
    }
    if let Some(parent) = intent_snapshot.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let connections = compose_execution_routes(&route_options)?;
    tracing::info!(
        event = "integrations_composed",
        component = "execution",
        route_count = route_options.len(),
        "execution integrations composed"
    );
    let async_order_entry = connections.async_order_entry;
    let async_order_query = connections.async_order_query;
    let async_execution_streams = connections.async_execution_streams;
    let application = ExecutionApplication::with_dependencies_and_query_and_stream(
        "execution",
        connections.order_entry,
        connections.order_query,
        connections.execution_stream,
        Some(Box::new(SqlxExecutionStore::new(state)?)),
    )?;
    let mut application = application;
    let manifest = instance.component_manifest()?;
    let simulated = route_options.iter().all(|options| {
        matches!(
            options.provider.trim().to_ascii_lowercase().as_str(),
            "simulated" | "paper"
        )
    });
    let preflight =
        SocketExecutionPreflight::from_manifest(manifest)?.with_simulated_settlement(simulated);
    application.attach_preflight(Box::new(QueuedExecutionPreflight::start(
        Box::new(preflight),
        128,
    )?));
    application.configure_live_trading(!simulated, args.confirm_live);
    let socket = instance.socket("execution")?;
    let process =
        ExecutionProcess::with_audit(application, socket, SqlxExecutionAudit::new(audit)?)
            .with_async_order_entry(async_order_entry)
            .with_async_order_query(async_order_query)
            .with_async_execution_routes(async_execution_streams);
    let process = if simulated {
        process.with_simulator(ExecutionSimulator::new(SimulationConfig::default())?)
    } else {
        process
    };
    process
        .with_snapshot_publisher(SharedExecutionSnapshotPublisher::create(
            execution_snapshot,
            1024 * 1024,
            format!("execution:{}", args.instance_id),
        )?)
        .with_intent_snapshot_publisher(SharedIntentSnapshotPublisher::create(
            intent_snapshot,
            1024 * 1024,
            format!("execution:{}", args.instance_id),
        )?)
        .run()
        .await
}

fn acquire_exclusive_provider_process_locks(
    workspace: &Workspace,
    routes: &[ExecutionConnectionOptions],
) -> Result<Vec<WorkspaceProcessLock>, Box<dyn std::error::Error>> {
    let identities = routes
        .iter()
        .filter(|route| route.provider.trim().eq_ignore_ascii_case("ibkr"))
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
    #[arg(long, default_value = "default")]
    route_id: String,
    #[arg(long, default_value_t = true)]
    route_required: bool,
    /// Non-secret JSON array of execution routes. Credentials are referenced
    /// by `credential_id` and loaded inside this process.
    #[arg(long)]
    routes_json: Option<String>,
    #[arg(long, default_value = "main")]
    account_id: String,
    #[arg(long, default_value = "spot")]
    segment_key: String,
    #[arg(long, default_value = "simulated")]
    provider: String,
    #[arg(long, default_value = "spot")]
    product: String,
    #[arg(long, env = "BINANCE_API_KEY", default_value = "")]
    api_key: String,
    #[arg(long, env = "BINANCE_API_SECRET", default_value = "")]
    secret: String,
    #[arg(long)]
    credential_id: Option<String>,
    #[arg(long, env = "OKX_PASSPHRASE", default_value = "")]
    passphrase: String,
    /// REST endpoint override; defaults from provider and product.
    #[arg(long, default_value = "")]
    base_url: String,
    /// Private WebSocket endpoint override; defaults from provider and product.
    #[arg(long, default_value = "")]
    websocket_url: String,
    #[arg(long)]
    isolated_symbol: Option<String>,
    #[arg(long, default_value_t = 1_000)]
    request_weight_per_minute: u32,
    #[arg(long, default_value_t = 50)]
    cancel_reserve_weight: u32,
    #[arg(long, default_value_t = 1_024)]
    order_event_queue_capacity: usize,
    #[arg(long, default_value = "default-egress")]
    egress_scope_id: String,
    #[arg(long, default_value = "execution-default")]
    principal_scope_id: String,
    #[arg(long, default_value_t = 50)]
    orders_per_10_seconds: u32,
    #[arg(long, default_value_t = 160_000)]
    orders_per_day: u32,
    #[arg(long, default_value = "127.0.0.1")]
    host: String,
    #[arg(long, default_value_t = 4002)]
    port: u16,
    #[arg(long, default_value_t = 0)]
    client_id: i32,
    #[arg(long)]
    confirm_live: bool,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ExecutionRouteConfig {
    route_id: String,
    #[serde(default = "default_true")]
    required: bool,
    #[serde(default)]
    account_id: Option<String>,
    #[serde(default)]
    segment_key: Option<String>,
    provider: String,
    product: String,
    #[serde(default)]
    credential_id: Option<String>,
    #[serde(default)]
    base_url: Option<String>,
    #[serde(default)]
    websocket_url: Option<String>,
    #[serde(default)]
    isolated_symbol: Option<String>,
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
}

fn default_true() -> bool {
    true
}

impl Args {
    fn connection_options_list(
        &self,
        workspace: &Workspace,
    ) -> Result<Vec<ExecutionConnectionOptions>, Box<dyn std::error::Error>> {
        let Some(routes_json) = self.routes_json.as_deref() else {
            return Ok(vec![self.connection_options(workspace)?]);
        };
        let routes: Vec<ExecutionRouteConfig> = serde_json::from_str(routes_json)?;
        if routes.is_empty() {
            return Err("--routes-json must contain at least one execution route".into());
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
            || route.provider.trim().is_empty()
            || route.product.trim().is_empty()
        {
            return Err("execution route_id, provider, and product are required".into());
        }
        let stored =
            load_workspace_credential(workspace, &route.provider, route.credential_id.as_deref())?;
        let (default_base_url, default_websocket_url) =
            provider_endpoints(&route.provider, &route.product);
        Ok(ExecutionConnectionOptions {
            route_id: route.route_id.clone(),
            required: route.required,
            account_id: route.account_id.unwrap_or_else(|| self.account_id.clone()),
            segment_key: route
                .segment_key
                .unwrap_or_else(|| self.segment_key.clone()),
            provider: route.provider,
            product: route.product,
            api_key: stored
                .as_ref()
                .map(|value| value.api_key.clone())
                .unwrap_or_default()
                .into(),
            secret: stored
                .as_ref()
                .map(|value| value.secret.clone())
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
            isolated_symbol: route
                .isolated_symbol
                .or_else(|| self.isolated_symbol.clone()),
            request_weight_per_minute: route
                .request_weight_per_minute
                .unwrap_or(self.request_weight_per_minute),
            cancel_reserve_weight: route
                .cancel_reserve_weight
                .unwrap_or(self.cancel_reserve_weight),
            order_event_queue_capacity: route
                .order_event_queue_capacity
                .unwrap_or(self.order_event_queue_capacity),
            shared_quota_ledger_path: Some(
                workspace
                    .state_root()
                    .join("integration")
                    .join("provider-quota.mmap"),
            ),
            egress_scope_id: route
                .egress_scope_id
                .unwrap_or_else(|| self.egress_scope_id.clone()),
            principal_scope_id: route
                .principal_scope_id
                .unwrap_or_else(|| route.route_id.clone()),
            orders_per_10_seconds: route
                .orders_per_10_seconds
                .unwrap_or(self.orders_per_10_seconds),
            orders_per_day: route.orders_per_day.unwrap_or(self.orders_per_day),
            host: route.host.unwrap_or_else(|| self.host.clone()),
            port: route.port.unwrap_or(self.port),
            client_id: route.client_id.unwrap_or(self.client_id),
        })
    }

    fn connection_options(
        &self,
        workspace: &Workspace,
    ) -> Result<ExecutionConnectionOptions, Box<dyn std::error::Error>> {
        let stored = self.credential_id.as_deref().map_or_else(
            || load_workspace_credential(workspace, &self.provider, None),
            |credential_id| {
                load_workspace_credential(workspace, &self.provider, Some(credential_id))
            },
        )?;
        let (default_base_url, default_websocket_url) =
            provider_endpoints(&self.provider, &self.product);
        Ok(ExecutionConnectionOptions {
            route_id: self.route_id.clone(),
            required: self.route_required,
            account_id: self.account_id.clone(),
            segment_key: self.segment_key.clone(),
            provider: self.provider.clone(),
            product: self.product.clone(),
            api_key: if self.api_key.is_empty() {
                stored
                    .as_ref()
                    .map(|value| value.api_key.clone())
                    .unwrap_or_default()
            } else {
                self.api_key.clone()
            }
            .into(),
            secret: if self.secret.is_empty() {
                stored
                    .as_ref()
                    .map(|value| value.secret.clone())
                    .unwrap_or_default()
            } else {
                self.secret.clone()
            }
            .into(),
            passphrase: if self.passphrase.is_empty() {
                stored
                    .as_ref()
                    .map(|value| value.passphrase.clone())
                    .unwrap_or_default()
            } else {
                self.passphrase.clone()
            }
            .into(),
            base_url: if self.base_url.trim().is_empty() {
                default_base_url.into()
            } else {
                self.base_url.clone()
            },
            websocket_url: if self.websocket_url.trim().is_empty() {
                default_websocket_url.into()
            } else {
                self.websocket_url.clone()
            },
            isolated_symbol: self.isolated_symbol.clone(),
            request_weight_per_minute: self.request_weight_per_minute,
            cancel_reserve_weight: self.cancel_reserve_weight,
            order_event_queue_capacity: self.order_event_queue_capacity,
            shared_quota_ledger_path: Some(
                workspace
                    .state_root()
                    .join("integration")
                    .join("provider-quota.mmap"),
            ),
            egress_scope_id: self.egress_scope_id.clone(),
            principal_scope_id: self.principal_scope_id.clone(),
            orders_per_10_seconds: self.orders_per_10_seconds,
            orders_per_day: self.orders_per_day,
            host: self.host.clone(),
            port: self.port,
            client_id: self.client_id,
        })
    }
}

fn provider_endpoints(provider: &str, product: &str) -> (&'static str, &'static str) {
    match (
        provider.trim().to_ascii_lowercase().as_str(),
        product.trim().to_ascii_lowercase().as_str(),
    ) {
        ("binance", "usd-m-futures" | "swap") => {
            ("https://fapi.binance.com", "wss://fstream.binance.com")
        }
        ("binance", "coin-m-futures" | "futures") => {
            ("https://dapi.binance.com", "wss://dstream.binance.com")
        }
        ("binance", "options" | "option") => (
            "https://eapi.binance.com",
            "wss://nbstream.binance.com/eoptions/private/stream",
        ),
        ("binance", "cross-margin" | "margin" | "isolated-margin") => {
            ("https://api.binance.com", "wss://stream.binance.com:9443")
        }
        ("okx" | "okex", _) => ("https://www.okx.com", "wss://ws.okx.com:8443/ws/v5/private"),
        _ => (
            "https://api.binance.com",
            "wss://ws-api.binance.com:443/ws-api/v3",
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::{
        acquire_exclusive_provider_process_locks, provider_endpoints, ExecutionRouteConfig,
    };
    use kairos_execution::composition::ExecutionConnectionOptions;
    use kairos_workspace::workspace::Workspace;
    use secrecy::SecretString;

    #[test]
    fn route_json_accepts_credential_references_and_rejects_inline_secrets() {
        let routes: Vec<ExecutionRouteConfig> = serde_json::from_str(
            r#"[{"route_id":"okx-main","provider":"okx","product":"swap","credential_id":"okx-main"}]"#,
        )
        .unwrap();
        assert_eq!(routes[0].credential_id.as_deref(), Some("okx-main"));
        assert!(serde_json::from_str::<Vec<ExecutionRouteConfig>>(
            r#"[{"route_id":"okx-main","provider":"okx","product":"swap","api_key":"secret"}]"#,
        )
        .is_err());
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
            provider: "ibkr".into(),
            product: "equity".into(),
            api_key: SecretString::from(String::new()),
            secret: SecretString::from(String::new()),
            passphrase: SecretString::from(String::new()),
            base_url: String::new(),
            websocket_url: String::new(),
            isolated_symbol: None,
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
        };
        let first = acquire_exclusive_provider_process_locks(&workspace, &[route.clone()]).unwrap();
        let error = acquire_exclusive_provider_process_locks(&workspace, &[route])
            .unwrap_err()
            .to_string();
        assert!(error.contains("already allocated"));
        drop(first);
    }

    #[test]
    fn participant_defaults_select_native_private_endpoints() {
        assert_eq!(provider_endpoints("okx", "spot").0, "https://www.okx.com");
        assert!(provider_endpoints("okx", "spot")
            .1
            .contains("/ws/v5/private"));
        assert_eq!(
            provider_endpoints("binance", "options"),
            (
                "https://eapi.binance.com",
                "wss://nbstream.binance.com/eoptions/private/stream"
            )
        );
    }
}
