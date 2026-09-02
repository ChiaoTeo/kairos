//! Account process composition root.

use std::path::{Path, PathBuf};
use std::time::Duration;

use clap::Parser;
use kairos_account::composition::account::{
    AccountOptions, AccountSegmentBinding, compose_binance_async_account_application,
    compose_ibkr_async_account_application, compose_local_account_application_for_segments,
    compose_okx_async_account_application, default_rest_endpoint,
};
use kairos_account::composition::registry::{AccountBindingRecord, AccountRegistry};
use kairos_account::{AccountApplication, AccountRpcService};
use kairos_account_contract::{
    ACCOUNT_MAP_SIZE, AccountControlRpcServer, AeronEndpoint, account_indexed_environment_path,
    account_indexed_identity,
};
use kairos_conflux::{
    AeronOutputDeclaration, Conflux, ConfluxConfig, ConfluxSystem, IndexedEnvironmentOptions,
    IndexedOutputDeclaration, JsonRpcRuntimeConfig,
};
use kairos_credentials::CredentialStore;
use kairos_primitives::account::AccountId;
use kairos_primitives::runtime::InstanceIdentity;
use kairos_workspace::Workspace;

#[tokio::main(flavor = "current_thread")]
async fn main() {
    kairos_workspace::logging::init("account");
    let result = tokio::task::LocalSet::new().run_until(run()).await;
    if let Err(error) = &result {
        tracing::error!(event = "process_failed", component = "account", error = %error, "account server failed");
    }
    kairos_workspace::logging::shutdown();
    if let Err(error) = result {
        eprintln!("kairos-account-server: {error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    tracing::info!(event = "process_start", component = "account", account_id = %args.account_id, instance_id = %args.instance_id, launch_id = %args.launch_id, "starting account server");
    let workspace = Workspace::open(&args.workspace)?;
    let instance = workspace.instance(&args.launch_mode, &args.launch_id, &args.instance_id)?;
    instance.prepare()?;
    let socket_name = args.socket_name.as_deref().unwrap_or("account");
    let _process_lock = instance.process_lock(socket_name)?;
    let transport_identity =
        InstanceIdentity::new(workspace.id(), instance.launch_id(), instance.instance_id())?;
    let socket = instance.socket(socket_name)?;
    let health = instance.service_health(socket_name)?;
    tracing::info!(event = "workspace_ready", component = "account", workspace = %workspace.root().display(), socket = %socket.display(), "workspace and instance resources resolved");
    let state = instance.state(&["account", &format!("{socket_name}-state.json")])?;
    let view_root = instance.snapshot(&[])?;
    let registry = AccountRegistry::load(workspace.existing_path(
        &["config", "accounts", "accounts.toml"],
        &["accounts", "accounts.toml"],
    )?)
    .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
    let credential_store = CredentialStore::load(workspace.existing_credentials_root()?)
        .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
    let record = registry
        .accounts
        .iter()
        .find(|record| record.account_id == args.account_id)
        .cloned()
        .ok_or_else(|| format!("account binding is not configured: {}", args.account_id))?;
    if record.segments.is_empty() {
        return Err(format!("account binding has no segments: {}", record.account_id).into());
    }
    let segment_bindings = record
        .segments
        .iter()
        .map(|segment_key| {
            record
                .product_for_segment(segment_key)
                .map(|product| {
                    let binding = AccountSegmentBinding::new(segment_key, product);
                    record
                        .segment_trading_modes
                        .get(segment_key)
                        .map_or(binding.clone(), |mode| binding.with_trading_mode(mode))
                })
                .ok_or_else(|| format!("account segment {segment_key} has no provider product"))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let credential_id = record.credential_id.clone().or_else(|| {
        record
            .credentials
            .first()
            .map(|binding| binding.credential_id.clone())
    });
    let credential = credential_id.as_deref().and_then(|id| {
        credential_store
            .credentials
            .iter()
            .find(|value| value.credential_id() == id)
    });
    let api_key = credential
        .and_then(|value| value.api_key_value())
        .unwrap_or_default();
    let secret = credential
        .and_then(|value| value.secret_value())
        .unwrap_or_default();
    let passphrase = credential
        .and_then(|value| value.passphrase_value())
        .unwrap_or_default();
    let mut options = args.options(&record, api_key, secret, passphrase)?;
    options.reference_database =
        Some(workspace.child(&["state", "reference", "reference.sqlite"])?);
    let shared_quota_ledger = workspace
        .state_root()
        .join("integration")
        .join("provider-quota.mmap");
    let native_binance_account = options.provider.eq_ignore_ascii_case("binance")
        && segment_bindings.iter().all(|segment| {
            matches!(
                segment
                    .provider_segment
                    .trim()
                    .to_ascii_lowercase()
                    .replace('_', "-")
                    .as_str(),
                "spot"
                    | "funding"
                    | "cross-margin"
                    | "isolated-margin"
                    | "usd-m-futures"
                    | "coin-m-futures"
                    | "options"
            )
        });
    let native_okx_account = matches!(
        options.provider.trim().to_ascii_lowercase().as_str(),
        "okx" | "okex"
    );
    let native_ibkr_account = options.provider.trim().eq_ignore_ascii_case("ibkr");
    let _provider_process_lock = native_ibkr_account
        .then(|| {
            let identity = format!(
                "ibkr|{}|{}|client-id:{}",
                options.host.trim().to_ascii_lowercase(),
                options.port,
                options.client_id
            );
            workspace
                .exclusive_process_lock("ibkr-client", &identity)
                .map_err(|error| {
                    std::io::Error::new(
                        error.kind(),
                        format!("IBKR client identity is already allocated ({identity}): {error}"),
                    )
                })
        })
        .transpose()?;
    let composition = if native_binance_account {
        compose_binance_async_account_application(
            &options,
            &segment_bindings,
            Some(state),
            args.account_stream_endpoint
                .as_deref()
                .unwrap_or("wss://ws-api.binance.com:443/ws-api/v3"),
            Some(shared_quota_ledger.clone()),
            &args.egress_scope_id,
        )?
    } else if native_okx_account {
        compose_okx_async_account_application(
            &options,
            &segment_bindings,
            Some(state),
            args.account_stream_endpoint.as_deref(),
            Some(shared_quota_ledger.clone()),
            &args.egress_scope_id,
        )?
    } else if native_ibkr_account {
        compose_ibkr_async_account_application(&options, &segment_bindings, Some(state))?
    } else if matches!(
        options.provider.trim().to_ascii_lowercase().as_str(),
        "paper" | "simulated"
    ) {
        compose_local_account_application_for_segments(&options, &segment_bindings, Some(state))?
    } else {
        return Err(format!(
            "production Account requires a provider-native async source; provider={} segments={} are not migrated",
            options.provider,
            segment_bindings
                .iter()
                .map(|value| value.segment_key.as_str())
                .collect::<Vec<_>>()
                .join(",")
        )
        .into());
    };
    let refresh_interval = Duration::from_millis(args.refresh_ms);
    let (mut application, system) = composition.into_conflux(refresh_interval)?;
    application.configure_publication_identity(transport_identity.clone());
    let producer_incarnation = application.conflux_producer_incarnation();
    let system = configure_publication(
        system,
        &args.account_id,
        &view_root,
        &transport_identity,
        producer_incarnation,
        args.aeron_dir.as_deref(),
        &args.aeron_channel,
        args.account_events_stream_id,
    )?;
    run_process(application, system, socket, health).await
}

fn configure_publication(
    system: ConfluxSystem,
    account_id: &str,
    view_root: &Path,
    identity: &InstanceIdentity,
    producer_incarnation: u64,
    aeron_dir: Option<&str>,
    aeron_channel: &str,
    event_stream_id: i32,
) -> Result<ConfluxSystem, Box<dyn std::error::Error>> {
    let endpoint = AeronEndpoint::from_parts(aeron_dir, aeron_channel, event_stream_id)?;
    let mut system = system;
    let account_id = AccountId::new(account_id)?;
    let path = account_indexed_environment_path(view_root, identity, &account_id)?;
    system.outputs().indexed.declare(
        "account-current",
        IndexedOutputDeclaration {
            options: IndexedEnvironmentOptions::new(path, ACCOUNT_MAP_SIZE)?,
            identity: account_indexed_identity(identity, &account_id, producer_incarnation),
            revision: 1,
        },
    )?;
    system.outputs().aeron.declare(
        "account-events".to_owned(),
        AeronOutputDeclaration {
            endpoint,
            revision: 1,
        },
    )?;
    Ok(system)
}

async fn run_process(
    application: AccountApplication,
    system: ConfluxSystem,
    socket: PathBuf,
    health_file: PathBuf,
) -> Result<(), Box<dyn std::error::Error>> {
    let (conflux, handle) = Conflux::new(
        application,
        system,
        ConfluxConfig {
            ingress_capacity: 256,
            ..ConfluxConfig::default()
        },
    )?;
    let outcome = conflux
        .with_json_rpc(
            handle.clone(),
            AccountRpcService::<AccountApplication>::new(
                handle.rpc_actor_invocation(Duration::from_secs(30)),
            )
            .into_rpc(),
            JsonRpcRuntimeConfig::uds(socket).with_health_file(Some(health_file)),
        )
        .run()
        .await?;
    tracing::info!(event = "process_stopped", component = "account", phase = ?outcome.phase, discarded_inputs = outcome.discarded_inputs, "Account Conflux process stopped");
    Ok(())
}

#[derive(Debug, Parser)]
#[command(name = "kairos-account", about = "Run the Account actor process")]
struct Args {
    #[arg(long)]
    account_id: String,
    #[arg(long)]
    workspace: String,
    #[arg(long, visible_alias = "launch-mode", default_value = "paper")]
    launch_mode: String,
    #[arg(long)]
    launch_id: String,
    #[arg(long, default_value = "default")]
    instance_id: String,
    #[arg(long)]
    socket_name: Option<String>,
    #[arg(long, env = "ACCOUNT_STREAM_ENDPOINT")]
    account_stream_endpoint: Option<String>,
    #[arg(long, default_value = "default-egress")]
    egress_scope_id: String,
    #[arg(long, default_value_t = 30_000, value_parser = clap::value_parser!(u64).range(1..))]
    refresh_ms: u64,
    #[arg(long, env = "AERON_DIR")]
    aeron_dir: Option<String>,
    #[arg(long)]
    aeron_channel: String,
    #[arg(
        long,
        default_value_t = kairos_conflux::output_stream_ids::ACCOUNT_EVENTS,
        value_parser = clap::value_parser!(i32).range(1..)
    )]
    account_events_stream_id: i32,
}

impl Args {
    fn options(
        &self,
        record: &AccountBindingRecord,
        api_key: String,
        secret: String,
        passphrase: String,
    ) -> Result<AccountOptions, String> {
        let provider = record.integration_provider.clone();
        let product = record
            .segments
            .first()
            .and_then(|segment| record.product_for_segment(segment))
            .map(str::to_owned)
            .ok_or_else(|| {
                "account binding must configure at least one segment product".to_string()
            })?;
        let environment = record.environment.clone();
        let base_url = if let Some(value) = record.values.get("base_url") {
            value.clone()
        } else {
            default_rest_endpoint(&provider, &product)?.to_owned()
        };
        Ok(AccountOptions {
            provider,
            product,
            api_key: api_key.into(),
            secret: secret.into(),
            passphrase: passphrase.into(),
            base_url,
            account_id: self.account_id.clone(),
            segment: record.segments.first().cloned().expect("validated segment"),
            environment,
            account_model: record.account_model.clone(),
            initial_balances: record.initial_balances.clone(),
            host: record
                .values
                .get("host")
                .cloned()
                .unwrap_or_else(|| "127.0.0.1".into()),
            port: record
                .values
                .get("port")
                .map(|value| value.parse())
                .transpose()
                .map_err(|_| "account binding port must be an unsigned 16-bit integer")?
                .unwrap_or(4002),
            client_id: record
                .values
                .get("client_id")
                .map(|value| value.parse())
                .transpose()
                .map_err(|_| "account binding client_id must be an integer")?
                .unwrap_or(0),
            isolated_margin_symbol: record.values.get("isolated_margin_symbol").cloned(),
            reference_database: None,
        })
    }
}
