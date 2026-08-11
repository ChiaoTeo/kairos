//! Account process composition root.

use std::time::Duration;

use clap::Parser;

use kairos_account::composition::account::{
    attach_account_stream, compose_account_application_for_segments,
    compose_binance_async_account_application, compose_blocking_account_stream,
    compose_okx_async_account_application, AccountOptions,
};
use kairos_account::composition::MmapAccountPublisher;
use kairos_protocol::InstanceIdentity;
use kairos_workspace::account::{AccountBindingRecord, AccountRegistry, CredentialStore};
use kairos_workspace::Workspace;

#[tokio::main(flavor = "current_thread")]
async fn main() {
    kairos_workspace::logging::init("account");
    let result = run().await;
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
    let _process_lock = instance.process_lock("account")?;
    let transport_identity =
        InstanceIdentity::new(workspace.id(), instance.launch_id(), instance.instance_id());
    let socket_name = args.socket_name.as_deref().unwrap_or("account");
    let socket = instance.socket(socket_name)?;
    let health = instance.service_health("account")?;
    tracing::info!(event = "workspace_ready", component = "account", workspace = %workspace.root().display(), socket = %socket.display(), "workspace and instance resources resolved");
    let state = instance.state(&["account", &format!("{socket_name}-state.json")])?;
    let snapshot = instance.service_snapshot("account")?;
    let registry = AccountRegistry::load(workspace.child(&["accounts", "accounts.toml"])?)
        .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
    let credential_store =
        CredentialStore::load(workspace.child(&["credentials", "credentials.toml"])?)
            .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
    let record = registry
        .accounts
        .iter()
        .find(|record| record.account_id == args.account_id)
        .cloned();
    let segments = record
        .as_ref()
        .map(|record| record.segments.clone())
        .filter(|segments| !segments.is_empty())
        .unwrap_or_else(|| vec![args.segment.clone()]);
    let credential_id = record.as_ref().and_then(|value| {
        value.credential_id.clone().or_else(|| {
            value
                .credentials
                .first()
                .map(|binding| binding.credential_id.clone())
        })
    });
    let credential = credential_id.as_deref().and_then(|id| {
        credential_store
            .credentials
            .iter()
            .find(|value| value.credential_id == id)
    });
    let api_key = if args.api_key.is_empty() {
        credential
            .and_then(|value| value.api_key_value())
            .unwrap_or_default()
    } else {
        args.api_key.clone()
    };
    let secret = if args.secret.is_empty() {
        credential
            .and_then(|value| value.secret_value())
            .unwrap_or_default()
    } else {
        args.secret.clone()
    };
    let passphrase = if args.passphrase.trim().is_empty() {
        credential
            .and_then(|value| value.passphrase_value())
            .unwrap_or_default()
    } else {
        args.passphrase.clone()
    };
    let options = args.options(record.as_ref(), api_key, secret, passphrase);
    let shared_quota_ledger = workspace
        .state_root()
        .join("integration")
        .join("provider-quota.mmap");
    let native_binance_account = options.provider.eq_ignore_ascii_case("binance")
        && segments.iter().all(|segment| {
            matches!(
                segment.trim().to_ascii_lowercase().as_str(),
                "spot" | "funding"
            )
        });
    let native_okx_account = matches!(
        options.provider.trim().to_ascii_lowercase().as_str(),
        "okx" | "okex"
    );
    let mut composition = if native_binance_account {
        compose_binance_async_account_application(
            &options,
            &segments,
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
            &segments,
            Some(state),
            args.account_stream_endpoint.as_deref(),
            Some(shared_quota_ledger.clone()),
            &args.egress_scope_id,
        )?
    } else {
        compose_account_application_for_segments(&options, &segments, Some(state))?
    };
    composition
        .application
        .set_trade_enabled(record.as_ref().is_none_or(|value| {
            value.permissions.contains_key("trade")
                || value
                    .credential_role
                    .as_deref()
                    .is_some_and(|role| !role.eq_ignore_ascii_case("readonly"))
        }));
    if args.account_stream_endpoint.is_some() {
        for segment in &segments {
            if native_binance_account || native_okx_account {
                continue;
            }
            let mut stream_options = options.clone();
            stream_options.product = segment.clone();
            if composition.try_add_async_account_stream(
                &stream_options,
                args.account_stream_endpoint
                    .as_deref()
                    .expect("checked account stream endpoint"),
                segment,
                Some(shared_quota_ledger.clone()),
                &args.egress_scope_id,
            )? {
                continue;
            }
            let stream_connection = compose_blocking_account_stream(
                &stream_options,
                args.account_stream_endpoint.as_deref(),
                segment,
            )?;
            attach_account_stream(
                &mut composition.application,
                kairos_integration::blocking::IntegrationAccountStream::new(stream_connection)
                    .buffered(),
            );
        }
    }
    let lease_file = record.as_ref().map(|value| {
        workspace
            .child(&[
                "state",
                "account-locks",
                &format!(
                    "{}.{}",
                    lease_component(&value.provider),
                    lease_component(&args.account_id)
                ),
                "owner.json",
            ])
            .expect("validated account lease path")
    });
    let process = composition.into_process(
        args.account_id,
        socket.to_string_lossy().into_owned(),
        Duration::from_millis(args.refresh_ms),
        Some(health),
        Some(Box::new(MmapAccountPublisher::create(
            snapshot,
            1024 * 1024,
            "account",
            transport_identity,
        )?)),
    )?;
    let process = match lease_file {
        Some(path) => process.with_trade_lease(path, args.instance_id.clone()),
        None => process,
    };
    process.run().await?;
    Ok(())
}

#[derive(Debug, Parser)]
#[command(name = "kairos-account", about = "Run the Account actor process")]
struct Args {
    #[arg(long, default_value = "binance")]
    provider: String,
    #[arg(long, default_value = "spot")]
    product: String,
    #[arg(long, env = "BINANCE_API_KEY", default_value = "")]
    api_key: String,
    #[arg(long, env = "BINANCE_API_SECRET", default_value = "")]
    secret: String,
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
    account_id: String,
    #[arg(long, default_value = "spot")]
    segment: String,
    #[arg(long, default_value = "live")]
    environment: String,
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
}

fn lease_component(value: &str) -> String {
    value
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() {
                character.to_ascii_lowercase()
            } else {
                '_'
            }
        })
        .collect::<String>()
        .split('_')
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join("_")
}

impl Args {
    fn options(
        &self,
        record: Option<&AccountBindingRecord>,
        api_key: String,
        secret: String,
        passphrase: String,
    ) -> AccountOptions {
        let provider = record
            .map(|value| value.provider.clone())
            .unwrap_or_else(|| self.provider.clone());
        let product = record
            .and_then(|value| value.segments.first().cloned())
            .unwrap_or_else(|| self.product.clone());
        let environment = record
            .map(|value| value.environment.clone())
            .unwrap_or_else(|| self.environment.clone());
        AccountOptions {
            provider,
            product,
            api_key: api_key.into(),
            secret: secret.into(),
            passphrase: passphrase.into(),
            base_url: self.base_url.clone(),
            account_id: self.account_id.clone(),
            segment: record
                .and_then(|value| value.segments.first().cloned())
                .unwrap_or_else(|| self.segment.clone()),
            environment,
            account_model: record.and_then(|value| value.account_model.clone()),
            initial_balances: record
                .map(|value| value.initial_balances.clone())
                .unwrap_or_default(),
            host: self.host.clone(),
            port: self.port,
            client_id: self.client_id,
        }
    }
}
