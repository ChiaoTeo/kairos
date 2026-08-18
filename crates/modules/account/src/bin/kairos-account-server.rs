//! Account process composition root.

use std::path::{Path, PathBuf};
use std::time::Duration;

use axum::{
    body::to_bytes,
    extract::{Request, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    Json, Router,
};

use clap::Parser;

use kairos_account::composition::account::{
    compose_binance_async_account_application, compose_ibkr_async_account_application,
    compose_local_account_application_for_segments, compose_okx_async_account_application,
    default_rest_endpoint, AccountOptions, AccountSegmentBinding,
};
use kairos_account::composition::registry::{AccountBindingRecord, AccountRegistry};
use kairos_account::AccountApplication;
use kairos_account_contract::{
    AccountRestRequest, AccountRestResponse, AccountSegmentsRequest, AccountViewKey,
    AccountViewKind,
};
use kairos_conflux::{
    Conflux, ConfluxConfig, ConfluxEvent, ConfluxHandle, ConfluxSystem, ShutdownMode,
};
use kairos_integration::composition::credentials::CredentialStore;
use kairos_protocol::InstanceIdentity;
use kairos_transport::{AeronBytePublisher, SharedSnapshotWriter};
use kairos_workspace::Workspace;
use serde::de::DeserializeOwned;
use serde_json::json;
use tokio::net::UnixListener;

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
        InstanceIdentity::new(workspace.id(), instance.launch_id(), instance.instance_id());
    let socket = instance.socket(socket_name)?;
    let health = instance.service_health(socket_name)?;
    tracing::info!(event = "workspace_ready", component = "account", workspace = %workspace.root().display(), socket = %socket.display(), "workspace and instance resources resolved");
    let state = instance.state(&["account", &format!("{socket_name}-state.json")])?;
    let snapshot = instance.service_snapshot(socket_name)?;
    let registry = AccountRegistry::load(workspace.existing_path(
        &["config", "accounts", "accounts.toml"],
        &["accounts", "accounts.toml"],
    )?)
    .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
    let credential_store = CredentialStore::load(workspace.existing_path(
        &["config", "credentials", "credentials.toml"],
        &["credentials", "credentials.toml"],
    )?)
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
            .find(|value| value.credential_id == id)
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
    let trade_enabled = {
        trade_access_enabled(
            record.permissions.contains_key("trade"),
            record.credential_role.as_deref(),
        )
    };
    let lease_file = trade_enabled.then_some(&record).map(|value| {
        workspace
            .child(&[
                "state",
                "account-locks",
                &format!(
                    "{}.{}",
                    lease_component(&value.broker),
                    lease_component(&args.account_id)
                ),
                "owner.json",
            ])
            .expect("validated account lease path")
    });
    let refresh_interval = Duration::from_millis(args.refresh_ms);
    let (mut application, mut system) = composition.into_conflux(refresh_interval)?;
    application.configure_publication_identity(transport_identity);
    configure_publication(
        &mut system,
        &args.account_id,
        &snapshot,
        args.aeron_dir.as_deref(),
        &args.aeron_channel,
        args.account_events_stream_id,
    )?;
    run_process(
        application,
        system,
        socket,
        health,
        lease_file,
        args.instance_id,
    )
    .await
}

fn configure_publication(
    system: &mut ConfluxSystem,
    account_id: &str,
    snapshot: &Path,
    aeron_dir: Option<&str>,
    aeron_channel: &str,
    event_stream_id: i32,
) -> Result<(), Box<dyn std::error::Error>> {
    const SLOT_SIZE: usize = 1024 * 1024;
    system
        .mmap_writers
        .ensure_with("account-current".to_owned(), 1, || {
            SharedSnapshotWriter::create(snapshot, SLOT_SIZE).expect("validated snapshot writer")
        })?;
    let runtime_id = format!("account:{account_id}");
    let key = AccountViewKey::new(&runtime_id, account_id, AccountViewKind::ObservedOrders)?;
    let root = snapshot.parent().unwrap_or(snapshot).join("views");
    system
        .account_view_publishers
        .ensure_with("account-observed-orders".to_owned(), 1, || {
            kairos_account_contract::AccountViewPublisher::create(&root, key, SLOT_SIZE)
                .expect("validated Account view publisher")
        })?;
    let publisher = AeronBytePublisher::connect(aeron_dir, aeron_channel, event_stream_id)?;
    system
        .aeron_publishers
        .ensure_with("account-events".to_owned(), 1, || publisher)?;
    Ok(())
}

async fn run_process(
    application: AccountApplication,
    system: ConfluxSystem,
    socket: PathBuf,
    health_file: PathBuf,
    lease_file: Option<PathBuf>,
    instance_id: String,
) -> Result<(), Box<dyn std::error::Error>> {
    remove_socket(&socket)?;
    if let Some(parent) = socket.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    let listener = UnixListener::bind(&socket)?;
    let (conflux, handle) = Conflux::new(
        application,
        system,
        ConfluxConfig {
            ingress_capacity: 256,
            ..ConfluxConfig::default()
        },
    )?;
    let actor = tokio::task::spawn_local(conflux.run());
    let router = Router::new()
        .fallback(account_http_handler)
        .with_state(AccountHost {
            handle: handle.clone(),
            lease_file,
            instance_id,
        });
    let server = tokio::spawn(async move { axum::serve(listener, router).await });

    let startup = handle
        .handle(ConfluxEvent::Rest(AccountRestRequest::Health))
        .await
        .map_err(|_| "Account Conflux startup health failed")?;
    let status = match startup {
        Some(AccountRestResponse::Health(Ok(health))) => health.status,
        _ => return Err("Account Actor omitted its startup health response".into()),
    };
    write_health(&health_file, &status).await?;
    kairos_workspace::logging::record_gauge("kairos.process.ready", u64::from(status == "ready"));

    let outcome = actor.await.map_err(|error| error.to_string())??;
    server.abort();
    let _ = server.await;
    remove_socket(&socket)?;
    write_health(&health_file, "stopped").await?;
    tracing::info!(event = "process_stopped", component = "account", phase = ?outcome.phase, discarded_inputs = outcome.discarded_inputs, "Account Conflux process stopped");
    Ok(())
}

#[derive(Clone)]
struct AccountHost {
    handle: ConfluxHandle<AccountApplication>,
    lease_file: Option<PathBuf>,
    instance_id: String,
}

async fn account_http_handler(State(host): State<AccountHost>, request: Request) -> Response {
    let method = request.method().as_str().to_owned();
    let path = request.uri().path().to_owned();
    let body = match to_bytes(
        request.into_body(),
        kairos_workspace::control::MAX_HTTP_BODY_BYTES,
    )
    .await
    {
        Ok(body) => body,
        Err(_) => return json_error(StatusCode::PAYLOAD_TOO_LARGE, "request body too large"),
    };
    let request = match decode_request(&method, &path, &body) {
        Ok(request) => request,
        Err(response) => return response,
    };
    match request {
        HostRequest::Stop => {
            host.handle.shutdown(ShutdownMode::Drain);
            (StatusCode::ACCEPTED, Json(json!({"status":"stopping"}))).into_response()
        }
        HostRequest::Rest(request) => match host.handle.handle(ConfluxEvent::Rest(request)).await {
            Ok(Some(response)) => encode_response(response, lease_valid(&host)),
            Ok(None) => json_error(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Account Actor omitted its REST response",
            ),
            Err(_) => json_error(
                StatusCode::SERVICE_UNAVAILABLE,
                "account process is stopping",
            ),
        },
    }
}

enum HostRequest {
    Rest(AccountRestRequest),
    Stop,
}

fn decode_request(method: &str, path: &str, body: &[u8]) -> Result<HostRequest, Response> {
    match (method, path) {
        ("POST", "/v1/stop") => Ok(HostRequest::Stop),
        ("GET", "/v1/health") => Ok(HostRequest::Rest(AccountRestRequest::Health)),
        ("POST", "/v1/simulation/settlements") => Ok(HostRequest::Rest(
            AccountRestRequest::ApplySimulatedSettlement(decode(body)?),
        )),
        ("POST", "/v1/mark-to-market") => Ok(HostRequest::Rest(AccountRestRequest::MarkToMarket(
            decode(body)?,
        ))),
        ("POST", "/v1/time/advance") => Ok(HostRequest::Rest(AccountRestRequest::AdvanceTime(
            decode(body)?,
        ))),
        ("POST", "/v1/refresh") => Ok(HostRequest::Rest(AccountRestRequest::Refresh(
            decode_segments(body)?,
        ))),
        ("POST", "/v1/reconcile") => Ok(HostRequest::Rest(AccountRestRequest::Reconcile(
            decode_segments(body)?,
        ))),
        (_, "/v1/health") => Err(json_error(
            StatusCode::METHOD_NOT_ALLOWED,
            "health accepts only GET",
        )),
        (_, "/v1/stop") => Err(json_error(
            StatusCode::METHOD_NOT_ALLOWED,
            "stop accepts only POST",
        )),
        _ => Err(json_error(
            StatusCode::NOT_FOUND,
            "unknown Account endpoint",
        )),
    }
}

fn decode_segments(body: &[u8]) -> Result<AccountSegmentsRequest, Response> {
    if body.is_empty() {
        Ok(AccountSegmentsRequest::default())
    } else {
        decode(body)
    }
}

fn decode<T: DeserializeOwned>(body: &[u8]) -> Result<T, Response> {
    serde_json::from_slice(body)
        .map_err(|error| json_error(StatusCode::BAD_REQUEST, &error.to_string()))
}

fn encode_response(response: AccountRestResponse, lease_valid: bool) -> Response {
    match response {
        AccountRestResponse::Health(Ok(mut value)) => {
            value.lease_valid = Some(lease_valid);
            (StatusCode::OK, Json(json!(value))).into_response()
        }
        AccountRestResponse::Health(Err(error))
        | AccountRestResponse::ApplySimulatedSettlement(Err(error))
        | AccountRestResponse::MarkToMarket(Err(error))
        | AccountRestResponse::AdvanceTime(Err(error))
        | AccountRestResponse::Refresh(Err(error))
        | AccountRestResponse::Reconcile(Err(error)) => {
            (StatusCode::CONFLICT, Json(json!({"error": error}))).into_response()
        }
        AccountRestResponse::ApplySimulatedSettlement(Ok(value))
        | AccountRestResponse::MarkToMarket(Ok(value)) => {
            (StatusCode::OK, Json(json!(value))).into_response()
        }
        AccountRestResponse::AdvanceTime(Ok(value)) => {
            (StatusCode::OK, Json(json!(value))).into_response()
        }
        AccountRestResponse::Refresh(Ok(value)) | AccountRestResponse::Reconcile(Ok(value)) => {
            (StatusCode::OK, Json(json!(value))).into_response()
        }
    }
}

fn lease_valid(host: &AccountHost) -> bool {
    let Some(path) = &host.lease_file else {
        return true;
    };
    let Ok(value) = std::fs::read(path).and_then(|bytes| {
        serde_json::from_slice::<serde_json::Value>(&bytes).map_err(std::io::Error::other)
    }) else {
        return false;
    };
    value
        .get("launch_instance_id")
        .and_then(serde_json::Value::as_str)
        == Some(host.instance_id.as_str())
        && std::fs::metadata(path)
            .and_then(|metadata| metadata.modified())
            .and_then(|modified| {
                std::time::SystemTime::now()
                    .duration_since(modified)
                    .map_err(std::io::Error::other)
            })
            .is_ok_and(|age| age <= Duration::from_secs(60))
}

fn json_error(status: StatusCode, message: &str) -> Response {
    (status, Json(json!({"error": message}))).into_response()
}

fn remove_socket(path: &Path) -> Result<(), std::io::Error> {
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

async fn write_health(path: &Path, status: &str) -> Result<(), std::io::Error> {
    if let Some(parent) = path.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    let temporary = path.with_extension("tmp");
    tokio::fs::write(&temporary, serde_json::to_vec(&json!({"status": status}))?).await?;
    tokio::fs::rename(temporary, path).await
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
