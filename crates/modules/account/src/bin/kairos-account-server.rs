//! Account process composition root.

use std::time::Duration;

use clap::Parser;

use kairos_account::composition::account::{
    compose_binance_async_account_application, compose_ibkr_async_account_application,
    compose_local_account_application_for_segments, compose_okx_async_account_application,
    default_rest_endpoint, AccountOptions, AccountSegmentBinding,
};
use kairos_account::composition::registry::{AccountBindingRecord, AccountRegistry};
use kairos_account::composition::{AeronAccountEventPublisher, MmapAccountPublisher};
use kairos_integration::application::credential::CredentialStore;
use kairos_protocol::InstanceIdentity;
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
    let socket_name = args.socket_name.as_deref().unwrap_or("account");
    let _process_lock = instance.process_lock(socket_name)?;
    let transport_identity =
        InstanceIdentity::new(workspace.id(), instance.launch_id(), instance.instance_id());
    let socket = instance.socket(socket_name)?;
    let health = instance.service_health(socket_name)?;
    tracing::info!(event = "workspace_ready", component = "account", workspace = %workspace.root().display(), socket = %socket.display(), "workspace and instance resources resolved");
    let state = instance.state(&["account", &format!("{socket_name}-state.json")])?;
    let snapshot = instance.service_snapshot(socket_name)?;
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
    let segment_bindings = if let Some(record) =
        record.as_ref().filter(|value| !value.segments.is_empty())
    {
        record
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
            .collect::<Result<Vec<_>, _>>()?
    } else {
        let binding = AccountSegmentBinding::new(&args.segment, &args.product);
        vec![args
            .trading_mode
            .as_ref()
            .map_or(binding.clone(), |mode| binding.with_trading_mode(mode))]
    };
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
    let mut options = args.options(record.as_ref(), api_key, secret, passphrase)?;
    options.reference_database = Some(workspace.child(&["reference", "reference.sqlite"])?);
    let shared_quota_ledger = workspace
        .state_root()
        .join("integration")
        .join("provider-quota.mmap");
    let native_binance_account = options.provider.eq_ignore_ascii_case("binance")
        && segment_bindings.iter().all(|segment| {
            matches!(
                segment
                    .provider_product
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
    let mut composition = if native_binance_account {
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
    let trade_enabled = record.as_ref().is_none_or(|value| {
        trade_access_enabled(
            value.permissions.contains_key("trade"),
            value.credential_role.as_deref(),
        )
    });
    composition.application.set_trade_enabled(trade_enabled);
    let lease_file = trade_enabled
        .then(|| record.as_ref())
        .flatten()
        .map(|value| {
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
    let process = composition
        .into_process(
            args.account_id,
            socket.to_string_lossy().into_owned(),
            Duration::from_millis(args.refresh_ms),
            Some(health),
            Some(Box::new(MmapAccountPublisher::create(
                snapshot,
                1024 * 1024,
                "account",
                transport_identity.clone(),
            )?)),
        )?
        .with_event_publisher(AeronAccountEventPublisher::connect(
            args.aeron_dir.as_deref(),
            &args.aeron_channel,
            args.account_events_stream_id,
            "account",
            transport_identity,
        )?);
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
    #[arg(long)]
    trading_mode: Option<String>,
    #[arg(long, default_value = "")]
    api_key: String,
    #[arg(long, default_value = "")]
    secret: String,
    #[arg(long, default_value = "")]
    passphrase: String,
    #[arg(long, default_value = "")]
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
    #[arg(long, env = "AERON_DIR")]
    aeron_dir: Option<String>,
    #[arg(long, default_value = kairos_transport::DEFAULT_CHANNEL)]
    aeron_channel: String,
    #[arg(
        long,
        default_value_t = kairos_transport::stream_ids::ACCOUNT_EVENTS,
        value_parser = clap::value_parser!(i32).range(1..)
    )]
    account_events_stream_id: i32,
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

fn trade_access_enabled(has_trade_permission: bool, credential_role: Option<&str>) -> bool {
    has_trade_permission
        && credential_role.is_none_or(|role| !role.eq_ignore_ascii_case("readonly"))
}

impl Args {
    fn options(
        &self,
        record: Option<&AccountBindingRecord>,
        api_key: String,
        secret: String,
        passphrase: String,
    ) -> Result<AccountOptions, String> {
        let provider = record
            .map(|value| value.provider.clone())
            .unwrap_or_else(|| self.provider.clone());
        let product = record
            .and_then(|value| {
                value
                    .segments
                    .first()
                    .and_then(|segment| value.product_for_segment(segment))
                    .map(str::to_owned)
            })
            .unwrap_or_else(|| self.product.clone());
        let environment = record
            .map(|value| value.environment.clone())
            .unwrap_or_else(|| self.environment.clone());
        let base_url = if self.base_url.trim().is_empty() {
            default_rest_endpoint(&provider, &product)?.to_owned()
        } else {
            self.base_url.clone()
        };
        Ok(AccountOptions {
            provider,
            product,
            api_key: api_key.into(),
            secret: secret.into(),
            passphrase: passphrase.into(),
            base_url,
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
            isolated_margin_symbol: record
                .and_then(|value| value.values.get("isolated_margin_symbol").cloned()),
            reference_database: None,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::trade_access_enabled;

    #[test]
    fn readonly_credential_never_enables_trade_access() {
        assert!(!trade_access_enabled(true, Some("readonly")));
        assert!(!trade_access_enabled(false, Some("readonly")));
    }

    #[test]
    fn writable_role_still_requires_discovered_trade_permission() {
        assert!(trade_access_enabled(true, Some("trading")));
        assert!(trade_access_enabled(true, None));
        assert!(!trade_access_enabled(false, Some("trading")));
        assert!(!trade_access_enabled(false, None));
    }
}
