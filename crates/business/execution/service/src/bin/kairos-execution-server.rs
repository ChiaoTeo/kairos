use clap::Parser;
use kairos_execution::application::ExecutionApplication;
use kairos_execution::composition::{
    compose_execution_stream, compose_order_entry, compose_order_query, ExecutionConnectionOptions,
    QueuedExecutionPreflight, SharedExecutionSnapshotPublisher, SharedIntentSnapshotPublisher,
    SocketExecutionPreflight, SqliteExecutionAudit, SqliteExecutionStore,
};
use kairos_execution::credentials::load_workspace_credential;
use kairos_execution::ExecutionProcess;
use kairos_workspace::workspace::Workspace;

#[tokio::main(flavor = "multi_thread", worker_threads = 4)]
async fn main() {
    kairos_workspace::logging::init("execution");
    if let Err(error) = run().await {
        tracing::error!(event = "process_failed", component = "execution", error = %error, "execution server failed");
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
    let options = args.connection_options(&workspace)?;
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
    let connection = compose_order_entry(&options)?;
    let query = compose_order_query(&options)?;
    let stream = compose_execution_stream(&options)?;
    tracing::info!(event = "integrations_composed", component = "execution", provider = %options.provider, product = %options.product, "execution integrations composed");
    let application = ExecutionApplication::with_dependencies_and_query_and_stream(
        "execution",
        Some(connection),
        query,
        stream,
        Some(Box::new(SqliteExecutionStore::new(state)?)),
    )?;
    let mut application = application;
    let manifest = instance.component_manifest()?;
    let preflight = SocketExecutionPreflight::from_manifest(manifest)?;
    application.attach_preflight(Box::new(QueuedExecutionPreflight::start(
        Box::new(preflight),
        128,
    )?));
    application.configure_live_trading(
        !matches!(
            options.provider.trim().to_ascii_lowercase().as_str(),
            "simulated" | "paper"
        ),
        args.confirm_live,
    );
    let socket = instance.socket("execution")?;
    ExecutionProcess::with_audit(application, socket, SqliteExecutionAudit::new(audit))
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
    #[arg(long, default_value = "https://api.binance.com")]
    base_url: String,
    #[arg(long, default_value = "127.0.0.1")]
    host: String,
    #[arg(long, default_value_t = 4002)]
    port: u16,
    #[arg(long, default_value_t = 0)]
    client_id: i32,
    #[arg(long)]
    confirm_live: bool,
}

impl Args {
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
        Ok(ExecutionConnectionOptions {
            provider: self.provider.clone(),
            product: self.product.clone(),
            api_key: if self.api_key.is_empty() {
                stored
                    .as_ref()
                    .map(|value| value.api_key.clone())
                    .unwrap_or_default()
            } else {
                self.api_key.clone()
            },
            secret: if self.secret.is_empty() {
                stored
                    .as_ref()
                    .map(|value| value.secret.clone())
                    .unwrap_or_default()
            } else {
                self.secret.clone()
            },
            passphrase: if self.passphrase.is_empty() {
                stored
                    .as_ref()
                    .map(|value| value.passphrase.clone())
                    .unwrap_or_default()
            } else {
                self.passphrase.clone()
            },
            base_url: self.base_url.clone(),
            host: self.host.clone(),
            port: self.port,
            client_id: self.client_id,
        })
    }
}
