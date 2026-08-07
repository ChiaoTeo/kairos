//! Account process composition root.

use std::{path::PathBuf, time::Duration};

use clap::Parser;

use kairos_account::composition::FileAccountPublisher;
use kairos_account::composition::account::{
    compose_account_application_for_segments, compose_integration, AccountBindingRecord,
    AccountOptions, AccountRegistry, CredentialStore, IntegrationAccountStreamAdapter,
};
use kairos_account::AccountProcess;
use kairos_integration::application::ConnectionSpec;
use kairos_integration::domain::{
    AccessScope, IntegrationCapability, ProductFamily, TransportKind,
};
use kairos_integration::Integration;
use kairos_workspace::Workspace;

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let workspace = Workspace::open(&args.workspace)?;
    let instance = args
        .launch_id
        .as_deref()
        .map(|launch_id| workspace.instance(&args.launch_mode, launch_id, &args.instance_id))
        .transpose()?;
    if let Some(instance) = &instance {
        instance.prepare()?;
    }
    let socket = instance
        .as_ref()
        .map(|value| value.socket("account"))
        .transpose()?
        .unwrap_or(workspace.process_socket("account")?);
    let health = instance
        .as_ref()
        .map(|value| value.health("account"))
        .transpose()?
        .unwrap_or(workspace.health_file("account")?);
    let state = instance
        .as_ref()
        .map(|value| value.state(&["account", "account-state.json"]))
        .transpose()?
        .unwrap_or(workspace.child(&["state", "account", "account-state.json"])?);
    let snapshot = instance
        .as_ref()
        .map(|value| value.snapshot(&["account", "account.snapshot"]))
        .transpose()?
        .unwrap_or(workspace.child(&["state", "account", "account.snapshot"])?);
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
    let mut composition =
        compose_account_application_for_segments(&options, &segments, Some(PathBuf::from(state)))?;
    composition
        .application
        .set_trade_enabled(record.as_ref().is_none_or(|value| {
            value.permissions.contains_key("trade")
                || value
                    .credential_role
                    .as_deref()
                    .is_some_and(|role| !role.eq_ignore_ascii_case("readonly"))
        }));
    let provider = composition.provider;
    let application = &mut composition.application;
    if args.account_stream_endpoint.is_some() {
        for segment in &segments {
            let mut stream_options = options.clone();
            stream_options.product = segment.clone();
            let (integration, product) = compose_integration(&stream_options)?;
            let integration = compose_account_stream(integration, &args, product, segment)?;
            let stream_connection = integration.connect_account_stream(&ConnectionSpec {
                connection_id: format!("account.{}.{}.private-stream", provider, segment),
                provider: provider.clone(),
                product: Some(product),
                access: AccessScope::Private,
                transport: TransportKind::UserStream,
                capability: IntegrationCapability::AccountStream,
                credential_id: Some(provider.clone()),
                asset_type: None,
            })?;
            application
                .actor_mut()
                .attach_stream(Box::new(IntegrationAccountStreamAdapter::new(
                    kairos_integration::application::IntegrationAccountStream::new(
                        stream_connection,
                    )
                    .buffered(),
                )));
        }
    }
    AccountProcess::new(
        composition.application,
        args.account_id,
        socket.to_string_lossy().into_owned(),
        Duration::from_millis(args.refresh_ms),
        Some(PathBuf::from(health)),
        Some(FileAccountPublisher::new(snapshot, "account")),
    )
    .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?
    .run()
    .await
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
    #[arg(long, default_value = "paper")]
    launch_mode: String,
    #[arg(long)]
    launch_id: Option<String>,
    #[arg(long, default_value = "default")]
    instance_id: String,
    #[arg(long, env = "ACCOUNT_STREAM_ENDPOINT")]
    account_stream_endpoint: Option<String>,
    #[arg(long, default_value_t = 30_000, value_parser = clap::value_parser!(u64).range(1..))]
    refresh_ms: u64,
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
            api_key,
            secret,
            passphrase,
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

fn compose_account_stream(
    integration: Integration,
    args: &Args,
    product: ProductFamily,
    segment_key: &str,
) -> Result<Integration, String> {
    let provider = args.provider.trim().to_ascii_lowercase();
    if provider == "ibkr" {
        return Ok(integration.with_ibkr_account_stream(
            args.host.clone(),
            args.port,
            args.client_id,
            args.account_id.clone(),
            segment_key.to_owned(),
        ));
    }
    let Some(endpoint) = args.account_stream_endpoint.clone() else {
        return Ok(integration);
    };
    let provider = provider;
    let provider = if provider == "okex" {
        "okx"
    } else {
        provider.as_str()
    };
    match provider {
        "binance" if product == ProductFamily::Spot => Ok(integration
            .with_binance_spot_account_stream(
                args.api_key.clone(),
                args.secret.clone(),
                args.base_url.clone(),
                endpoint,
                segment_key.to_owned(),
            )),
        "binance"
            if matches!(
                product,
                ProductFamily::CrossMargin | ProductFamily::IsolatedMargin
            ) =>
        {
            integration
                .with_binance_margin_account_stream(
                    product,
                    args.api_key.clone(),
                    args.secret.clone(),
                    args.base_url.clone(),
                    endpoint,
                    segment_key.to_owned(),
                )
                .map_err(|error| error.to_string())
        }
        "binance"
            if matches!(
                product,
                ProductFamily::UsdMFutures | ProductFamily::CoinMFutures
            ) =>
        {
            integration
                .with_binance_futures_account_stream(
                    product,
                    args.api_key.clone(),
                    args.secret.clone(),
                    args.base_url.clone(),
                    endpoint,
                    segment_key.to_owned(),
                )
                .map_err(|error| error.to_string())
        }
        "okx" => integration
            .with_okx_account_stream(
                product,
                args.api_key.clone(),
                args.secret.clone(),
                args.passphrase.clone(),
                endpoint,
                segment_key.to_owned(),
            )
            .map_err(|error| error.to_string()),
        "binance" => Err(format!(
            "Binance {segment_key} does not provide a configured private account stream"
        )),
        _ => Err(format!("unsupported account stream provider: {provider}")),
    }
}
