use std::collections::BTreeMap;
use std::path::PathBuf;

use clap::{Args, Parser, Subcommand};
use kairos_account::application::account::{AccountDataQuery, AccountFill, Intent};
use kairos_account::application::account::{
    AccountMarketProfileRequest, ReconcileAccount, RefreshAccount,
};
use kairos_account::application::account::{AccountSession, LoginAccount};
use kairos_account::domain::AccountModel;
use kairos_account::composition::account::{
    compose_account_application_for_segments, compose_binance_earn, compose_binance_transfer,
    inspect_account_credential, AccountBindingRecord, AccountCredentialBinding, AccountOptions,
    AccountRegistry, CredentialRecord, CredentialStore,
};
use kairos_integration::domain::account::{
    ExternalAccountIdentity, ExternalAccountSegment, ExternalDecimal,
};
use kairos_integration::{
    EarnProductType, EarnRedeemRequest, EarnSubscribeRequest, TransferRequest,
};
use kairos_workspace::{control::RestControlClient, Workspace};

/// One-shot account inspection and mutation commands.
///
/// These commands compose the account application directly.  The `server`
/// group is the optional control-plane client for an already running process.
#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Cli::parse();
    let workspace = Workspace::open(&args.workspace)?;
    let output = args
        .output
        .as_deref()
        .unwrap_or(workspace.cli_format())
        .to_owned();
    std::env::set_var("KAIROS_CLI_FORMAT", output);
    match args.command.clone() {
        Command::Server { command } => {
            let client = RestControlClient::new(workspace.process_socket("account")?);
            let value = match command {
                ServerCommand::Status => client.health().await?,
                ServerCommand::Snapshot { symbol } => {
                    client
                        .request_json(
                            "GET",
                            &format!("/v1/snapshot{}", optional_symbol_query(symbol)),
                            None,
                        )
                        .await?
                }
                ServerCommand::Balances {
                    segment,
                    include_zero,
                    page,
                    page_size,
                } => {
                    client
                        .request_json(
                            "GET",
                            &format!(
                                "/v1/balances{}",
                                account_query_string(
                                    &segment,
                                    None,
                                    None,
                                    include_zero,
                                    page,
                                    page_size
                                )
                            ),
                            None,
                        )
                        .await?
                }
                ServerCommand::Positions { segment, symbol } => {
                    client
                        .request_json(
                            "GET",
                            &format!(
                                "/v1/positions{}",
                                account_query_string(&segment, symbol, None, false, None, None)
                            ),
                            None,
                        )
                        .await?
                }
                ServerCommand::OpenOrders { symbol, limit } => {
                    client
                        .request_json(
                            "GET",
                            &format!(
                                "/v1/open-orders{}",
                                account_query_string(&[], symbol, limit, false, None, None)
                            ),
                            None,
                        )
                        .await?
                }
                ServerCommand::Orders { order_id } => {
                    client
                        .request_json(
                            "GET",
                            &format!(
                                "/v1/orders{}",
                                order_id
                                    .map(|value| format!("?order_id={value}"))
                                    .unwrap_or_default()
                            ),
                            None,
                        )
                        .await?
                }
                ServerCommand::Intents => client.request_json("GET", "/v1/intents", None).await?,
                ServerCommand::Refresh => client.request_json("POST", "/v1/refresh", None).await?,
                ServerCommand::Reconcile => {
                    client.request_json("POST", "/v1/reconcile", None).await?
                }
                ServerCommand::MarketProfiles => {
                    client
                        .request_json("GET", "/v1/market-profiles", None)
                        .await?
                }
                ServerCommand::Capabilities => {
                    client.request_json("GET", "/v1/capabilities", None).await?
                }
                ServerCommand::Fees => client.request_json("GET", "/v1/fees", None).await?,
                ServerCommand::Login => client.request_json("POST", "/v1/login", None).await?,
                ServerCommand::Logout { session_file } => {
                    let body = std::fs::read(session_file)?;
                    client
                        .request_json("POST", "/v1/logout", Some(&body))
                        .await?
                }
                ServerCommand::Fills { fill } => {
                    let body = serde_json::to_string(&fill.to_domain())?;
                    client
                        .request_json("POST", "/v1/fills", Some(body.as_bytes()))
                        .await?
                }
                ServerCommand::SubmitIntent { request_id, file } => {
                    let intent: Intent = serde_json::from_slice(&std::fs::read(file)?)?;
                    let body = serde_json::to_vec(&serde_json::json!({
                        "request_id": request_id,
                        "intent": intent,
                    }))?;
                    client
                        .request_json("POST", "/v1/intents/submit", Some(&body))
                        .await?
                }
                ServerCommand::Stop => client.request_json("POST", "/v1/stop", None).await?,
            };
            print_json(value);
        }
        command => run_direct(&args, &workspace, command)?,
    }
    Ok(())
}

#[derive(Debug, Parser)]
#[command(
    name = "kairos-account-cli",
    about = "One-shot account commands and server control"
)]
struct Cli {
    #[arg(long)]
    workspace: String,
    #[arg(long, global = true, value_parser = ["text", "json"])]
    output: Option<String>,
    #[command(flatten)]
    connection: ConnectionArgs,
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Args)]
struct ConnectionArgs {
    #[arg(long, default_value = "binance")]
    provider: String,
    #[arg(long, default_value = "spot")]
    product: String,
    #[arg(long, env = "BINANCE_API_KEY")]
    api_key: Option<String>,
    #[arg(long, env = "BINANCE_API_SECRET")]
    secret: Option<String>,
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
    account_id: Option<String>,
    #[arg(long)]
    alias: Option<String>,
    #[arg(long, default_value = "spot")]
    segment: String,
    #[arg(long, default_value = "live")]
    environment: String,
}

#[derive(Clone, Debug, Subcommand)]
enum Command {
    Fill {
        #[command(flatten)]
        fill: FillArgs,
    },
    Browse {
        #[arg(long)]
        query: Option<String>,
    },
    Inspect {
        #[arg(long)]
        account_id: String,
    },
    Model {
        #[command(subcommand)]
        command: ModelCommand,
    },
    List,
    Show {
        #[arg(long)]
        account_id: String,
    },
    Register {
        #[arg(long)]
        account_id: String,
        #[arg(long)]
        provider: String,
        #[arg(long, default_value = "live")]
        environment: String,
        #[arg(long, default_value = "spot")]
        segment: String,
        #[arg(long)]
        account_model: Option<String>,
        #[arg(long)]
        venue: Option<String>,
        #[arg(long = "field")]
        fields: Vec<String>,
    },
    Modify {
        #[arg(long)]
        account_id: String,
        #[arg(long)]
        provider: Option<String>,
        #[arg(long)]
        venue: Option<String>,
        #[arg(long)]
        alias: Option<String>,
        #[arg(long)]
        environment: Option<String>,
        #[arg(long)]
        segment: Option<String>,
        #[arg(long)]
        account_model: Option<String>,
        #[arg(long)]
        credential_id: Option<String>,
        #[arg(long)]
        credential_role: Option<String>,
        #[arg(long)]
        status: Option<String>,
        #[arg(long)]
        fee_rate: Option<String>,
        #[arg(long)]
        clear_credential: bool,
        #[arg(long = "field")]
        fields: Vec<String>,
    },
    Simulate {
        #[arg(long)]
        account_id: String,
        #[arg(long, default_value = "spot")]
        segment: String,
        #[arg(long)]
        account_model: Option<String>,
        #[arg(long = "balance")]
        initial_balances: Vec<String>,
        #[arg(long)]
        fee_rate: Option<String>,
    },
    Transfer {
        #[arg(long)]
        source_segment: String,
        #[arg(long)]
        destination_segment: String,
        #[arg(long)]
        asset: String,
        #[arg(long)]
        amount_mantissa: i64,
        #[arg(long, default_value_t = 0)]
        amount_scale: u8,
    },
    Earn {
        #[command(subcommand)]
        command: EarnCommand,
    },
    Remove {
        #[arg(long)]
        account_id: String,
        #[arg(long)]
        force: bool,
    },
    CredentialList,
    CredentialAdd {
        #[arg(long)]
        account_id: String,
        #[arg(long)]
        name: String,
        #[arg(long)]
        credential_id: String,
        #[arg(long, default_value = "readonly")]
        role: String,
        #[arg(long, default_value_t = true)]
        check: bool,
        #[arg(long)]
        force: bool,
    },
    CredentialCreate {
        #[arg(long)]
        credential_id: String,
        #[arg(long)]
        provider: String,
        #[arg(long, default_value = "readonly")]
        role: String,
        #[arg(long, help = "Optional; prefer KAIROS_CREDENTIAL_<ID>_API_KEY")]
        api_key: Option<String>,
        #[arg(long, help = "Optional; prefer KAIROS_CREDENTIAL_<ID>_API_SECRET")]
        secret: Option<String>,
        #[arg(long, default_value = "")]
        passphrase: String,
    },
    CredentialShow {
        #[arg(long)]
        credential_id: String,
        #[arg(long)]
        reveal_secrets: bool,
    },
    CredentialDelete {
        #[arg(long)]
        credential_id: String,
        #[arg(long)]
        force: bool,
    },
    Schemas,
    Schema {
        #[arg(long)]
        provider: String,
    },
    Doctor,
    TradeLockList,
    TradeLockAcquire {
        #[arg(long)]
        account_id: String,
        #[arg(long, default_value = "cli")]
        owner: String,
    },
    TradeLockRelease {
        #[arg(long)]
        account_id: String,
        #[arg(long)]
        owner: Option<String>,
    },
    TradeLockStatus {
        #[arg(long)]
        account_id: String,
    },
    Connect,
    Login,
    Logout {
        #[arg(long)]
        session_file: PathBuf,
    },
    Snapshot {
        #[arg(long)]
        symbol: Option<String>,
    },
    #[command(alias = "balance")]
    Balances {
        #[arg(long = "segment")]
        segments: Vec<String>,
        #[arg(long)]
        include_zero: bool,
        #[arg(long, default_value_t = 1)]
        page: usize,
        #[arg(long = "page-size", default_value_t = 50)]
        page_size: usize,
    },
    Positions {
        #[arg(long = "segment")]
        segments: Vec<String>,
        #[arg(long)]
        symbol: Option<String>,
    },
    OpenOrders {
        #[arg(long)]
        symbol: Option<String>,
        #[arg(long)]
        limit: Option<usize>,
    },
    Orders,
    Intents,
    MarketProfiles,
    Capabilities,
    Fees,
    MarketProfile {
        #[arg(long)]
        market_id: String,
        #[arg(long)]
        source_symbol: String,
    },
    Refresh,
    Reconcile,
    SubmitIntent {
        #[arg(long)]
        request_id: String,
        #[arg(long)]
        file: PathBuf,
    },
    Server {
        #[command(subcommand)]
        command: ServerCommand,
    },
}

#[derive(Clone, Debug, Subcommand)]
enum ServerCommand {
    Status,
    Snapshot {
        #[arg(long)]
        symbol: Option<String>,
    },
    Balances {
        #[arg(long = "segment")]
        segment: Vec<String>,
        #[arg(long)]
        include_zero: bool,
        #[arg(long)]
        page: Option<usize>,
        #[arg(long = "page-size")]
        page_size: Option<usize>,
    },
    Positions {
        #[arg(long = "segment")]
        segment: Vec<String>,
        #[arg(long)]
        symbol: Option<String>,
    },
    OpenOrders {
        #[arg(long)]
        symbol: Option<String>,
        #[arg(long)]
        limit: Option<usize>,
    },
    Orders {
        #[arg(long)]
        order_id: Option<String>,
    },
    Intents,
    Refresh,
    Reconcile,
    MarketProfiles,
    Capabilities,
    Fees,
    Login,
    Logout {
        #[arg(long)]
        session_file: PathBuf,
    },
    Fills {
        #[command(flatten)]
        fill: FillArgs,
    },
    SubmitIntent {
        #[arg(long)]
        request_id: String,
        #[arg(long)]
        file: PathBuf,
    },
    Stop,
}

#[derive(Clone, Debug, Subcommand)]
enum ModelCommand {
    Switch {
        #[arg(long)]
        account_id: String,
        #[arg(long)]
        target: String,
        #[arg(long, default_value = "")]
        reason: String,
    },
}

#[derive(Clone, Debug, Args)]
struct FillArgs {
    #[arg(long)]
    fill_id: Option<String>,
    #[arg(long)]
    segment: String,
    #[arg(long)]
    instrument_id: String,
    #[arg(long)]
    quantity_mantissa: i64,
    #[arg(long, default_value_t = 0)]
    quantity_scale: u8,
    #[arg(long)]
    price_mantissa: i64,
    #[arg(long, default_value_t = 0)]
    price_scale: u8,
    #[arg(long)]
    settlement_asset: Option<String>,
    #[arg(long)]
    settlement_delta_mantissa: Option<i64>,
    #[arg(long, default_value_t = 0)]
    settlement_delta_scale: u8,
    #[arg(long)]
    fee_asset: Option<String>,
    #[arg(long)]
    fee_mantissa: Option<i64>,
    #[arg(long, default_value_t = 0)]
    fee_scale: u8,
    #[arg(long, default_value = "buy")]
    side: String,
    #[arg(long)]
    order_id: Option<String>,
}

impl FillArgs {
    fn to_domain(&self) -> AccountFill {
        AccountFill {
            fill_id: self.fill_id.clone(),
            order_id: self.order_id.clone(),
            segment_key: self.segment.clone(),
            instrument_id: self.instrument_id.clone(),
            quantity: kairos_account::domain::DecimalValue::new(
                self.quantity_mantissa,
                self.quantity_scale,
            ),
            price: kairos_account::domain::DecimalValue::new(self.price_mantissa, self.price_scale),
            side: match self.side.to_ascii_lowercase().as_str() {
                "sell" => kairos_account::domain::FillSide::Sell,
                _ => kairos_account::domain::FillSide::Buy,
            },
            settlement_asset: self.settlement_asset.clone(),
            settlement_delta: self.settlement_delta_mantissa.map(|value| {
                kairos_account::domain::DecimalValue::new(value, self.settlement_delta_scale)
            }),
            fee_asset: self.fee_asset.clone(),
            fee_amount: self
                .fee_mantissa
                .map(|value| kairos_account::domain::DecimalValue::new(value, self.fee_scale)),
            occurred_at_unix_nanos: 0,
        }
    }
}

#[derive(Clone, Debug, Subcommand)]
enum EarnCommand {
    Products {
        #[arg(long)]
        asset: Option<String>,
        #[arg(long)]
        product_type: Option<String>,
    },
    Positions {
        #[arg(long)]
        asset: Option<String>,
    },
    Rewards {
        #[arg(long)]
        asset: Option<String>,
    },
    Subscribe {
        #[arg(long)]
        product_id: String,
        #[arg(long, default_value = "flexible")]
        product_type: String,
        #[arg(long)]
        amount: String,
        #[arg(long)]
        auto_renew: Option<bool>,
    },
    Redeem {
        #[arg(long)]
        product_id: String,
        #[arg(long, default_value = "flexible")]
        product_type: String,
        #[arg(long)]
        amount: Option<String>,
        #[arg(long)]
        destination_account: Option<String>,
    },
}

fn run_direct(
    args: &Cli,
    workspace: &Workspace,
    command: Command,
) -> Result<(), Box<dyn std::error::Error>> {
    let registry_path = workspace.child(&["accounts", "accounts.toml"])?;
    let mut registry = AccountRegistry::load(&registry_path)?;
    let credentials_path = workspace.child(&["credentials", "credentials.toml"])?;
    let mut credential_store = CredentialStore::load(&credentials_path)?;
    registry.credentials.clear();
    for account in &mut registry.accounts {
        if account.credentials.is_empty() {
            if let Some(credential_id) = account.credential_id.clone() {
                account.credentials.push(AccountCredentialBinding {
                    name: "default".into(),
                    credential_id,
                    role: account
                        .credential_role
                        .clone()
                        .unwrap_or_else(|| "readonly".into()),
                });
            }
        }
    }
    match &command {
        Command::List => {
            print_json(serde_json::to_value(&registry.accounts)?);
            return Ok(());
        }
        Command::Browse { query } => {
            let query = query.as_deref().map(str::to_ascii_lowercase);
            let accounts: Vec<_> = registry
                .accounts
                .iter()
                .filter(|record| {
                    query.as_deref().is_none_or(|query| {
                        record.account_id.to_ascii_lowercase().contains(query)
                            || record.alias.to_ascii_lowercase().contains(query)
                            || record.provider.to_ascii_lowercase().contains(query)
                            || record
                                .segments
                                .iter()
                                .any(|segment| segment.to_ascii_lowercase().contains(query))
                    })
                })
                .cloned()
                .collect();
            print_json(serde_json::json!({
                "accounts": accounts,
                "count": accounts.len(),
            }));
            return Ok(());
        }
        Command::Model {
            command:
                ModelCommand::Switch {
                    account_id,
                    target,
                    reason,
                },
        } => {
            let target_model = AccountModel::parse(target)
                .ok_or_else(|| format!("unsupported account model: {target}"))?;
            let mut record = registry
                .accounts
                .iter()
                .find(|record| record.account_id == *account_id)
                .cloned()
                .ok_or_else(|| format!("account not found: {account_id}"))?;
            let previous = record.account_model.clone();
            if previous
                .as_deref()
                .is_some_and(|value| AccountModel::parse(value) == Some(target_model))
            {
                return Err(format!("account already uses target model: {target}").into());
            }
            record.account_model = Some(target.clone());
            record.status = "reconciling".into();
            registry.upsert_account(record.clone());
            registry.save(&registry_path)?;
            print_json(serde_json::json!({
                "account_id": account_id,
                "from_model": previous,
                "to_model": target,
                "status": "requested",
                "reason": reason,
                "account": record,
            }));
            return Ok(());
        }
        Command::Show { account_id } => {
            let value = registry
                .accounts
                .iter()
                .find(|record| record.account_id == *account_id)
                .ok_or_else(|| format!("account not found: {account_id}"))?;
            print_json(serde_json::to_value(value)?);
            return Ok(());
        }
        Command::Register {
            account_id,
            provider,
            environment,
            segment,
            account_model,
            venue,
            fields,
        } => {
            let values = parse_field_values(fields)?;
            registry.upsert_account(AccountBindingRecord {
                account_id: account_id.clone(),
                alias: account_id.clone(),
                provider: provider.clone(),
                venue: venue.clone(),
                environment: environment.clone(),
                remote_identity: None,
                permissions: BTreeMap::new(),
                segments: vec![segment.clone()],
                account_model: account_model.clone(),
                credential_id: None,
                credentials: Vec::new(),
                credential_role: None,
                status: "configured".into(),
                initial_balances: Vec::new(),
                fee_rate: None,
                values,
            });
            registry.save(&registry_path)?;
            print_json(serde_json::json!({"account_id": account_id, "status": "registered"}));
            return Ok(());
        }
        Command::Modify {
            account_id,
            provider,
            venue,
            alias,
            environment,
            segment,
            account_model,
            credential_id,
            credential_role,
            status,
            fee_rate,
            clear_credential,
            fields,
        } => {
            let mut record = registry
                .accounts
                .iter()
                .find(|value| value.account_id == *account_id)
                .cloned()
                .ok_or_else(|| format!("account not found: {account_id}"))?;
            if let Some(value) = provider {
                record.provider = value.clone();
            }
            if let Some(value) = venue {
                record.venue = Some(value.clone());
            }
            if let Some(value) = alias {
                record.alias = value.clone();
            }
            if let Some(value) = environment {
                record.environment = value.clone();
            }
            if let Some(value) = segment {
                record.segments = vec![value.clone()];
            }
            if account_model.is_some() {
                record.account_model = account_model.clone();
            }
            if credential_id.is_some() {
                record.credential_id = credential_id.clone();
            }
            if credential_role.is_some() {
                record.credential_role = credential_role.clone();
            }
            if status.is_some() {
                record.status = status.clone().unwrap_or_default();
            }
            if fee_rate.is_some() {
                record.fee_rate = fee_rate.clone();
            }
            record.values.extend(parse_field_values(fields)?);
            if *clear_credential {
                record.credential_id = None;
                record.credential_role = None;
                record.credentials.clear();
            }
            registry.upsert_account(record.clone());
            registry.save(&registry_path)?;
            print_json(serde_json::to_value(record)?);
            return Ok(());
        }
        Command::Simulate {
            account_id,
            segment,
            account_model,
            initial_balances,
            fee_rate,
        } => {
            let record = AccountBindingRecord {
                account_id: account_id.clone(),
                alias: account_id.clone(),
                provider: "paper".into(),
                venue: Some("paper".into()),
                environment: "paper".into(),
                remote_identity: None,
                permissions: BTreeMap::new(),
                segments: vec![segment.clone()],
                account_model: account_model.clone(),
                credential_id: None,
                credentials: Vec::new(),
                credential_role: None,
                status: "simulated".into(),
                initial_balances: initial_balances.clone(),
                fee_rate: fee_rate.clone(),
                values: BTreeMap::new(),
            };
            registry.upsert_account(record.clone());
            registry.save(&registry_path)?;
            print_json(serde_json::json!({
                "account": record,
                "mode": "paper",
                "status": "simulated"
            }));
            return Ok(());
        }
        Command::Remove { account_id, force } => {
            if !force
                && registry
                    .locks
                    .iter()
                    .any(|lock| lock.account_id == *account_id)
            {
                return Err(format!(
                    "account has an active trade lock: {account_id}; use --force to remove"
                )
                .into());
            }
            let removed = registry.remove_account(account_id);
            registry.locks.retain(|lock| lock.account_id != *account_id);
            registry.save(&registry_path)?;
            print_json(serde_json::json!({"account_id": account_id, "removed": removed}));
            return Ok(());
        }
        Command::CredentialList => {
            let values: Vec<_> = credential_store
                .credentials
                .iter()
                .map(|record| {
                    serde_json::json!({
                        "credential_id": record.credential_id,
                        "provider": record.provider,
                        "role": record.role,
                        "api_key": redact(&record.api_key),
                    })
                })
                .collect();
            print_json(serde_json::to_value(values)?);
            return Ok(());
        }
        Command::Logout { session_file } => {
            let session: AccountSession = serde_json::from_slice(&std::fs::read(session_file)?)?;
            if session.session_id.trim().is_empty() {
                return Err("session_id is required".into());
            }
            print_json(serde_json::json!({
                "status": "logged_out",
                "session_id": session.session_id,
                "account_id": session.account_id,
            }));
            return Ok(());
        }
        Command::CredentialAdd {
            account_id,
            name,
            credential_id,
            role,
            check,
            force,
        } => {
            let mut record = registry
                .accounts
                .iter()
                .find(|value| value.account_id == *account_id)
                .cloned()
                .ok_or_else(|| format!("account not found: {account_id}"))?;
            let credential = credential_store
                .credentials
                .iter()
                .find(|value| value.credential_id == *credential_id)
                .ok_or_else(|| format!("credential not found: {credential_id}"))?;
            if *check {
                let options = credential_probe_options(args, &record, credential)?;
                let profile = inspect_account_credential(&options)
                    .map_err(|error| format!("credential check failed: {error}"))?;
                let permissions: std::collections::BTreeSet<_> = profile
                    .permissions
                    .iter()
                    .map(|value| value.trim().to_ascii_lowercase())
                    .collect();
                if !permissions.contains("read") {
                    return Err(format!(
                        "credential {credential_id} does not provide read permission"
                    )
                    .into());
                }
                if role.trim().eq_ignore_ascii_case("trade") && !permissions.contains("trade") {
                    return Err(format!(
                        "credential {credential_id} does not provide trade permission"
                    )
                    .into());
                }
                if let (Some(expected), Some(actual)) = (
                    record.remote_identity.as_deref(),
                    profile.remote_identity.as_deref(),
                ) {
                    if expected != actual {
                        return Err(format!(
                            "credential remote identity mismatch: expected {expected}, got {actual}"
                        )
                        .into());
                    }
                }
                record.remote_identity = profile.remote_identity.clone();
                record.permissions = profile
                    .permissions
                    .iter()
                    .map(|permission| (permission.clone(), "granted".into()))
                    .collect();
                if !profile.segments.is_empty() {
                    record.segments = profile.segments.clone();
                }
            }
            if !*force && record.credentials.iter().any(|value| value.name == *name) {
                return Err(format!("account credential name already exists: {name}").into());
            }
            record.credentials.retain(|value| value.name != *name);
            record.credentials.push(AccountCredentialBinding {
                name: name.clone(),
                credential_id: credential_id.clone(),
                role: role.clone(),
            });
            if record.credential_id.is_none() {
                record.credential_id = Some(credential_id.clone());
                record.credential_role = Some(role.clone());
            }
            registry.upsert_account(record.clone());
            registry.save(&registry_path)?;
            print_json(serde_json::json!({
                "account_id": account_id,
                "name": name,
                "credential_id": credential_id,
                "role": role,
                "checked": check,
                "status": "bound",
                "account": record,
            }));
            return Ok(());
        }
        Command::CredentialCreate {
            credential_id,
            provider,
            role,
            api_key,
            secret,
            passphrase,
        } => {
            credential_store.upsert(CredentialRecord {
                credential_id: credential_id.clone(),
                provider: provider.clone(),
                role: role.clone(),
                api_key: api_key.clone().unwrap_or_default(),
                secret: secret.clone().unwrap_or_default(),
                passphrase: passphrase.clone(),
            });
            credential_store.save(&credentials_path)?;
            print_json(serde_json::json!({"credential_id": credential_id, "status": "created"}));
            return Ok(());
        }
        Command::CredentialDelete {
            credential_id,
            force,
        } => {
            if !force
                && registry
                    .accounts
                    .iter()
                    .any(|account| account.credential_id.as_deref() == Some(credential_id))
            {
                return Err(format!(
                    "credential is bound to an account: {credential_id}; use --force to delete"
                )
                .into());
            }
            let removed = credential_store.remove(credential_id);
            credential_store.save(&credentials_path)?;
            print_json(serde_json::json!({"credential_id": credential_id, "removed": removed}));
            return Ok(());
        }
        Command::CredentialShow {
            credential_id,
            reveal_secrets,
        } => {
            let credential = credential_store
                .credentials
                .iter()
                .find(|record| record.credential_id == *credential_id)
                .ok_or_else(|| format!("credential not found: {credential_id}"))?;
            print_json(serde_json::json!({
                "credential_id": credential.credential_id,
                "provider": credential.provider,
                "role": credential.role,
                "api_key": if *reveal_secrets { credential.api_key_value().unwrap_or_default() } else { redact(&credential.api_key) },
                "secret": if *reveal_secrets { credential.secret_value().unwrap_or_default() } else { "***".to_string() },
                "passphrase": if *reveal_secrets { credential.passphrase_value().unwrap_or_default() } else { "***".to_string() },
            }));
            return Ok(());
        }
        Command::Schemas => {
            print_json(serde_json::json!({
                "binance": {"credential_fields": ["api_key", "api_secret"], "segments": ["spot", "cross_margin", "isolated_margin", "usd_m_futures", "coin_m_futures", "funding", "options"]},
                "okx": {"credential_fields": ["api_key", "api_secret", "passphrase"], "segments": ["spot", "cross_margin", "isolated_margin", "swap", "futures", "options"]},
                "ibkr": {"credential_fields": [], "connection_fields": ["host", "port", "client_id"], "segments": ["equity"]},
                "paper": {"credential_fields": [], "segments": ["spot", "margin", "futures"]}
            }));
            return Ok(());
        }
        Command::Schema { provider } => {
            let provider = provider.to_ascii_lowercase();
            let value = match provider.as_str() {
                "binance" => {
                    serde_json::json!({"provider":"binance","credential_fields":["api_key","api_secret"],"segments":["spot","cross_margin","isolated_margin","usd_m_futures","coin_m_futures","funding","options"]})
                }
                "okx" | "okex" => {
                    serde_json::json!({"provider":"okx","credential_fields":["api_key","api_secret","passphrase"],"segments":["spot","cross_margin","isolated_margin","swap","futures","options"]})
                }
                "ibkr" => {
                    serde_json::json!({"provider":"ibkr","credential_fields":[],"connection_fields":["host","port","client_id"],"segments":["equity"]})
                }
                "paper" => {
                    serde_json::json!({"provider":"paper","credential_fields":[],"segments":["spot","margin","futures"]})
                }
                _ => return Err(format!("unsupported provider: {provider}").into()),
            };
            print_json(value);
            return Ok(());
        }
        Command::Doctor => {
            let issues: Vec<_> = registry
                .accounts
                .iter()
                .filter(|account| {
                    account.environment == "live"
                        && !credential_store.credentials.iter().any(|credential| {
                            credential.provider == account.provider
                                || account
                                    .credential_id
                                    .as_deref()
                                    .is_some_and(|id| credential.credential_id == id)
                        })
                })
                .map(|account| {
                    format!(
                        "{}: live account has no matching credential",
                        account.account_id
                    )
                })
                .collect();
            print_json(serde_json::json!({"accounts": registry.accounts, "issues": issues}));
            return Ok(());
        }
        Command::TradeLockList => {
            print_json(serde_json::to_value(&registry.locks)?);
            return Ok(());
        }
        Command::TradeLockAcquire { account_id, owner } => {
            registry.acquire_lock(account_id, owner)?;
            registry.save(&registry_path)?;
            print_json(
                serde_json::json!({"account_id": account_id, "owner": owner, "status":"locked"}),
            );
            return Ok(());
        }
        Command::TradeLockRelease { account_id, owner } => {
            let released = registry.release_lock(account_id, owner.as_deref());
            registry.save(&registry_path)?;
            print_json(serde_json::json!({"account_id": account_id, "released": released}));
            return Ok(());
        }
        Command::TradeLockStatus { account_id } => {
            let value = registry
                .locks
                .iter()
                .find(|lock| lock.account_id == *account_id);
            print_json(serde_json::to_value(value)?);
            return Ok(());
        }
        _ => {}
    }
    let account_id = args.connection.account_id.clone();
    let selected_segment = selected_segment(&args.connection);
    let account_id = account_id
        .or_else(|| match &command {
            Command::Inspect { account_id } => Some(account_id.clone()),
            _ => None,
        })
        // A live account can be connected from a credential alone.  The
        // provider may not expose a stable universal user id (Binance Spot
        // is one example), so the credential reference is the provisional
        // local binding identity, exactly as legacy did before discovery
        // returned a remote identity.
        .or_else(|| {
            if matches!(&command, Command::Connect | Command::Login) {
                args.connection
                    .alias
                    .clone()
                    .or_else(|| args.connection.credential_id.clone())
                    .or_else(|| Some(format!("{}-{}", args.connection.provider, selected_segment)))
            } else {
                None
            }
        })
        .ok_or("--account-id is required for a direct account command")?;
    let account_id = resolve_account_id(&registry, &account_id)?;
    let account_record = registry
        .accounts
        .iter()
        .find(|record| record.account_id == account_id)
        .cloned();
    let provider = account_record
        .as_ref()
        .map(|record| record.provider.clone())
        .unwrap_or_else(|| args.connection.provider.clone());
    let product = account_record
        .as_ref()
        .and_then(|record| record.segments.first().cloned())
        .unwrap_or_else(|| args.connection.product.clone());
    let environment = account_record
        .as_ref()
        .map(|record| record.environment.clone())
        .unwrap_or_else(|| args.connection.environment.clone());
    let credential_id = args.connection.credential_id.clone().or_else(|| {
        account_record
            .as_ref()
            .and_then(|record| record.credential_id.clone())
    });
    let credential = credential_id.as_deref().and_then(|id| {
        credential_store
            .credentials
            .iter()
            .find(|record| record.credential_id == id)
    });
    let paper = matches!(
        provider.trim().to_ascii_lowercase().as_str(),
        "paper" | "simulated"
    );
    let api_key = if paper {
        String::new()
    } else {
        args.connection
            .api_key
            .clone()
            .or_else(|| credential.and_then(|record| record.api_key_value()))
            .ok_or("--api-key, --credential-id, or BINANCE_API_KEY is required")?
    };
    let secret = if paper {
        String::new()
    } else {
        args.connection
            .secret
            .clone()
            .or_else(|| credential.and_then(|record| record.secret_value()))
            .ok_or("--secret, --credential-id, or BINANCE_API_SECRET is required")?
    };
    let passphrase = if args.connection.passphrase.is_empty() {
        credential
            .and_then(|record| record.passphrase_value())
            .unwrap_or_default()
    } else {
        args.connection.passphrase.clone()
    };
    let options = AccountOptions {
        provider,
        product,
        api_key,
        secret,
        passphrase,
        base_url: args.connection.base_url.clone(),
        account_id: account_id.clone(),
        segment: selected_segment.clone(),
        environment,
        account_model: account_record
            .as_ref()
            .and_then(|record| record.account_model.clone()),
        initial_balances: account_record
            .as_ref()
            .map(|record| record.initial_balances.clone())
            .unwrap_or_default(),
        host: args.connection.host.clone(),
        port: args.connection.port,
        client_id: args.connection.client_id,
    };
    if let Command::Transfer {
        source_segment,
        destination_segment,
        asset,
        amount_mantissa,
        amount_scale,
    } = &command
    {
        let mut connection = compose_binance_transfer(&options)?;
        let identity = ExternalAccountIdentity::new(&options.provider, account_id.clone())?;
        let result = connection.transfer(&TransferRequest {
            source: ExternalAccountSegment {
                identity: identity.clone(),
                segment_key: source_segment.clone(),
                environment: options.environment.clone(),
                account_model: None,
            },
            destination: ExternalAccountSegment {
                identity,
                segment_key: destination_segment.clone(),
                environment: options.environment.clone(),
                account_model: None,
            },
            asset: asset.clone(),
            amount: ExternalDecimal::new(*amount_mantissa, *amount_scale),
        })?;
        print_json(serde_json::json!({
            "accepted": result.accepted,
            "reference_id": result.reference_id,
            "reason": result.reason,
        }));
        return Ok(());
    }
    if let Command::Earn {
        command: earn_command,
    } = &command
    {
        let mut connection = compose_binance_earn(&options)?;
        let value = match earn_command {
            EarnCommand::Products {
                asset,
                product_type,
            } => {
                let products = connection.products(
                    asset.as_deref(),
                    product_type
                        .as_deref()
                        .map(parse_earn_product_type)
                        .transpose()?,
                )?;
                serde_json::json!({"products": products.iter().map(earn_product_json).collect::<Vec<_>>()})
            }
            EarnCommand::Positions { asset } => serde_json::json!({
                "positions": connection
                    .positions(asset.as_deref())?
                    .iter()
                    .map(earn_position_json)
                    .collect::<Vec<_>>()
            }),
            EarnCommand::Rewards { asset } => serde_json::json!({
                "rewards": connection
                    .rewards(asset.as_deref())?
                    .iter()
                    .map(earn_reward_json)
                    .collect::<Vec<_>>()
            }),
            EarnCommand::Subscribe {
                product_id,
                product_type,
                amount,
                auto_renew,
            } => earn_action_json(connection.subscribe(&EarnSubscribeRequest {
                product_id: product_id.clone(),
                product_type: parse_earn_product_type(product_type)?,
                amount: amount.clone(),
                auto_renew: *auto_renew,
            })?),
            EarnCommand::Redeem {
                product_id,
                product_type,
                amount,
                destination_account,
            } => earn_action_json(connection.redeem(&EarnRedeemRequest {
                product_id: product_id.clone(),
                product_type: parse_earn_product_type(product_type)?,
                amount: amount.clone(),
                destination_account: destination_account.clone(),
            })?),
        };
        print_json(value);
        return Ok(());
    }
    let state = workspace.child(&["state", "account", "account-state.json"])?;
    let configured_segments = registry
        .accounts
        .iter()
        .find(|record| record.account_id == account_id)
        .map(|record| record.segments.clone())
        .filter(|segments| !segments.is_empty())
        .unwrap_or_else(|| vec![selected_segment.clone()]);
    let mut composition =
        compose_account_application_for_segments(&options, &configured_segments, Some(state))?;
    let trade_enabled = account_record.as_ref().map_or_else(
        || credential.is_none_or(|value| !value.role.eq_ignore_ascii_case("readonly")),
        |record| {
            record.permissions.contains_key("trade")
                || record
                    .credential_role
                    .as_deref()
                    .is_some_and(|role| !role.eq_ignore_ascii_case("readonly"))
        },
    );
    composition.application.set_trade_enabled(trade_enabled);

    if matches!(&command, Command::Login) {
        let result = composition.application.login(LoginAccount {
            account_id: account_id.clone(),
            segments: configured_segments.clone(),
            connection_ids: Vec::new(),
            observed_at_unix_nanos: unix_now_nanos(),
        })?;
        print_json(serde_json::to_value(result)?);
        return Ok(());
    }

    if let Command::SubmitIntent { request_id, file } = command {
        let intent: Intent = serde_json::from_slice(&std::fs::read(file)?)?;
        composition.application.record_intent(intent.clone())?;
        print_json(serde_json::json!({
            "request_id": request_id,
            "intent_id": intent.intent_id,
            "status": "accepted",
        }));
        return Ok(());
    }

    if let Command::MarketProfile {
        market_id,
        source_symbol,
    } = &command
    {
        let profile =
            composition
                .application
                .refresh_market_profile(AccountMarketProfileRequest {
                    account_id: account_id.clone(),
                    segment_key: selected_segment.clone(),
                    market_id: market_id.clone(),
                    source_symbol: source_symbol.clone(),
                })?;
        print_json(serde_json::to_value(profile)?);
        return Ok(());
    }

    let refresh_report = if matches!(&command, Command::Reconcile) {
        composition.application.reconcile_report(ReconcileAccount {
            account_id: account_id.clone(),
            segments: Vec::new(),
        })?
    } else {
        composition.application.refresh_report(RefreshAccount {
            account_id: account_id.clone(),
            segments: Vec::new(),
        })?
    };
    if matches!(&command, Command::Refresh | Command::Reconcile) {
        print_json(serde_json::to_value(refresh_report)?);
        return Ok(());
    }
    if let Command::Fill { fill } = &command {
        composition.application.apply_fill(fill.to_domain())?;
        print_json(serde_json::json!({
            "status": "accepted",
            "snapshot": composition.application.snapshot(),
        }));
        return Ok(());
    }
    if let Command::Inspect { .. } = &command {
        let record = registry
            .accounts
            .iter()
            .find(|record| record.account_id == account_id)
            .cloned();
        let credential_profile = inspect_account_credential(&options).ok();
        print_json(serde_json::json!({
            "account_id": account_id,
            "provider": options.provider,
            "environment": options.environment,
            "configured_segments": configured_segments,
            "account_model": record.as_ref().and_then(|value| value.account_model.clone()),
            "credential_id": record.as_ref().and_then(|value| value.credential_id.clone()),
            "status": record.as_ref().map(|value| value.status.clone()),
            "snapshot": composition.application.snapshot(),
            "market_profiles": composition.application.market_profiles(),
            "credential_profile": credential_profile,
        }));
        return Ok(());
    }
    if matches!(&command, Command::Connect) {
        let credential_profile = inspect_account_credential(&options).ok();
        let connected_role = credential
            .map(|value| value.role.clone())
            .unwrap_or_else(|| "readonly".into());
        let discovered_segments = credential_profile
            .as_ref()
            .map(|value| value.segments.clone())
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| vec![options.segment.clone()]);
        registry.upsert_account(AccountBindingRecord {
            account_id: account_id.clone(),
            alias: args
                .connection
                .alias
                .clone()
                .unwrap_or_else(|| account_id.clone()),
            provider: options.provider.clone(),
            venue: Some(options.provider.clone()),
            environment: options.environment.clone(),
            remote_identity: credential_profile
                .as_ref()
                .and_then(|value| value.remote_identity.clone()),
            permissions: credential_profile
                .as_ref()
                .map(|value| {
                    value
                        .permissions
                        .iter()
                        .map(|permission| (permission.clone(), "granted".into()))
                        .collect()
                })
                .unwrap_or_default(),
            segments: discovered_segments.clone(),
            account_model: None,
            credential_id: args.connection.credential_id.clone(),
            credentials: args
                .connection
                .credential_id
                .as_ref()
                .map(|credential_id| {
                    vec![AccountCredentialBinding {
                        name: "default".into(),
                        credential_id: credential_id.clone(),
                        role: connected_role.clone(),
                    }]
                })
                .unwrap_or_default(),
            credential_role: Some(connected_role),
            status: "connected".into(),
            initial_balances: Vec::new(),
            fee_rate: None,
            values: BTreeMap::new(),
        });
        registry.save(&registry_path)?;
        print_json(serde_json::json!({
            "account_id": account_id,
            "provider": options.provider,
            "segment": options.segment,
            "discovered_segments": discovered_segments,
            "status": "connected",
            "snapshot": composition.application.snapshot(),
            "credential_profile": credential_profile,
        }));
        return Ok(());
    }
    let value = match command {
        Command::Snapshot { symbol } => {
            serde_json::to_value(composition.application.snapshot_query(&AccountDataQuery {
                account_id: Some(account_id.clone()),
                symbol,
                ..Default::default()
            }))?
        }
        Command::Refresh | Command::Reconcile => {
            serde_json::to_value(composition.application.snapshot())?
        }
        Command::Balances {
            segments,
            include_zero,
            page,
            page_size,
        } => {
            let query = AccountDataQuery {
                account_id: Some(account_id.clone()),
                segments,
                include_zero,
                page: Some(page),
                page_size: Some(page_size),
                ..Default::default()
            };
            serde_json::json!({
                "accounts": composition.application.balances_query(&query),
                "rows": composition.application.balance_rows_query(&query),
                "page": page,
                "page_size": page_size,
                "refresh": refresh_report,
            })
        }
        Command::Positions { segments, symbol } => {
            serde_json::json!({"accounts": composition.application.positions_query(&AccountDataQuery {
                account_id: Some(account_id.clone()),
                segments,
                symbol,
                ..Default::default()
            }), "refresh": refresh_report})
        }
        Command::OpenOrders { symbol, limit } => {
            serde_json::json!({"accounts": composition.application.open_orders_query(&AccountDataQuery {
                account_id: Some(account_id.clone()),
                symbol,
                limit,
                ..Default::default()
            }), "refresh": refresh_report})
        }
        Command::Orders => {
            serde_json::json!({"orders": composition.application.orders(kairos_account::application::account::OrderQuery { account_id: Some(account_id), order_id: None })})
        }
        Command::Intents => {
            serde_json::json!({"intents": composition.application.intents(Some(&account_id))})
        }
        Command::MarketProfiles => {
            serde_json::json!({"profiles": composition.application.market_profiles()})
        }
        Command::Capabilities => {
            serde_json::json!({"capabilities": composition.application.capabilities(Some(&account_id))})
        }
        Command::Fees => {
            serde_json::json!({"fees": composition.application.fee_schedules(Some(&account_id))})
        }
        Command::SubmitIntent { .. }
        | Command::Fill { .. }
        | Command::Browse { .. }
        | Command::Inspect { .. }
        | Command::Model { .. }
        | Command::Server { .. }
        | Command::List
        | Command::Show { .. }
        | Command::Register { .. }
        | Command::Modify { .. }
        | Command::Simulate { .. }
        | Command::Transfer { .. }
        | Command::Earn { .. }
        | Command::Remove { .. }
        | Command::CredentialList
        | Command::CredentialAdd { .. }
        | Command::CredentialCreate { .. }
        | Command::CredentialShow { .. }
        | Command::CredentialDelete { .. }
        | Command::Schemas
        | Command::Schema { .. }
        | Command::Doctor
        | Command::TradeLockList
        | Command::TradeLockAcquire { .. }
        | Command::TradeLockRelease { .. }
        | Command::TradeLockStatus { .. }
        | Command::Connect
        | Command::Login
        | Command::Logout { .. }
        | Command::MarketProfile { .. } => unreachable!(),
    };
    print_json(value);
    Ok(())
}

fn print_json(value: serde_json::Value) {
    if std::env::var("KAIROS_CLI_FORMAT").as_deref() == Ok("text") {
        if let serde_json::Value::Object(fields) = &value {
            for (key, item) in fields {
                println!(
                    "{key}: {}",
                    serde_json::to_string(item).expect("JSON serialization")
                );
            }
            return;
        }
    }
    println!(
        "{}",
        serde_json::to_string_pretty(&value).expect("JSON serialization")
    );
}

fn optional_symbol_query(symbol: Option<String>) -> String {
    symbol
        .map(|value| format!("?symbol={value}"))
        .unwrap_or_default()
}

fn selected_segment(args: &ConnectionArgs) -> String {
    if args.segment != "spot" || args.product.eq_ignore_ascii_case("spot") {
        return args.segment.clone();
    }
    args.product.trim().to_ascii_lowercase().replace('-', "_")
}

fn resolve_account_id(
    registry: &AccountRegistry,
    value: &str,
) -> Result<String, Box<dyn std::error::Error>> {
    if registry
        .accounts
        .iter()
        .any(|record| record.account_id == value)
    {
        return Ok(value.to_owned());
    }
    let matches: Vec<_> = registry
        .accounts
        .iter()
        .filter(|record| record.alias == value)
        .map(|record| record.account_id.clone())
        .collect();
    match matches.as_slice() {
        [account_id] => Ok(account_id.clone()),
        [] => Ok(value.to_owned()),
        _ => Err(format!("account alias is ambiguous: {value}").into()),
    }
}

fn unix_now_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_nanos().min(u64::MAX as u128) as u64)
        .unwrap_or_default()
}

fn account_query_string(
    segments: &[String],
    symbol: Option<String>,
    limit: Option<usize>,
    include_zero: bool,
    page: Option<usize>,
    page_size: Option<usize>,
) -> String {
    let mut values = Vec::new();
    values.extend(segments.iter().map(|value| format!("segment={value}")));
    if let Some(value) = symbol {
        values.push(format!("symbol={value}"));
    }
    if let Some(value) = limit {
        values.push(format!("limit={value}"));
    }
    if include_zero {
        values.push("include_zero=true".into());
    }
    if let Some(value) = page {
        values.push(format!("page={value}"));
    }
    if let Some(value) = page_size {
        values.push(format!("page_size={value}"));
    }
    if values.is_empty() {
        String::new()
    } else {
        format!("?{}", values.join("&"))
    }
}

fn parse_field_values(
    values: &[String],
) -> Result<BTreeMap<String, String>, Box<dyn std::error::Error>> {
    let mut fields = BTreeMap::new();
    for value in values {
        let (key, field_value) = value
            .split_once('=')
            .ok_or_else(|| format!("field must be key=value: {value}"))?;
        let key = key.trim();
        if key.is_empty() {
            return Err(format!("field key is empty: {value}").into());
        }
        fields.insert(key.to_owned(), field_value.to_owned());
    }
    Ok(fields)
}

fn credential_probe_options(
    args: &Cli,
    account: &AccountBindingRecord,
    credential: &CredentialRecord,
) -> Result<AccountOptions, Box<dyn std::error::Error>> {
    let paper = matches!(
        account.provider.trim().to_ascii_lowercase().as_str(),
        "paper" | "simulated"
    );
    let api_key = if paper {
        String::new()
    } else {
        credential
            .api_key_value()
            .ok_or("credential has no API key")?
    };
    let secret = if paper {
        String::new()
    } else {
        credential
            .secret_value()
            .ok_or("credential has no API secret")?
    };
    let passphrase = credential.passphrase_value().unwrap_or_default();
    Ok(AccountOptions {
        provider: account.provider.clone(),
        product: account
            .segments
            .first()
            .cloned()
            .unwrap_or_else(|| args.connection.product.clone()),
        api_key,
        secret,
        passphrase,
        base_url: args.connection.base_url.clone(),
        account_id: account.account_id.clone(),
        segment: account
            .segments
            .first()
            .cloned()
            .unwrap_or_else(|| selected_segment(&args.connection)),
        environment: account.environment.clone(),
        account_model: account.account_model.clone(),
        initial_balances: account.initial_balances.clone(),
        host: args.connection.host.clone(),
        port: args.connection.port,
        client_id: args.connection.client_id,
    })
}

fn parse_earn_product_type(value: &str) -> Result<EarnProductType, Box<dyn std::error::Error>> {
    match value.trim().to_ascii_lowercase().as_str() {
        "flexible" => Ok(EarnProductType::Flexible),
        "locked" => Ok(EarnProductType::Locked),
        _ => Err(format!("unsupported earn product type: {value}").into()),
    }
}

fn earn_product_json(value: &kairos_integration::EarnProduct) -> serde_json::Value {
    serde_json::json!({
        "product_id": value.product_id,
        "asset": value.asset,
        "product_type": format!("{:?}", value.product_type).to_ascii_lowercase(),
        "annual_rate": value.annual_rate,
        "min_amount": value.min_amount,
        "max_amount": value.max_amount,
        "status": value.status,
        "duration_days": value.duration_days,
    })
}

fn earn_position_json(value: &kairos_integration::EarnPosition) -> serde_json::Value {
    serde_json::json!({
        "product_id": value.product_id,
        "asset": value.asset,
        "amount": value.amount,
        "rewards": value.rewards,
        "annual_rate": value.annual_rate,
        "status": value.status,
        "updated_at_unix_millis": value.updated_at_unix_millis,
    })
}

fn earn_reward_json(value: &kairos_integration::EarnReward) -> serde_json::Value {
    serde_json::json!({
        "asset": value.asset,
        "amount": value.amount,
        "product_id": value.product_id,
        "occurred_at_unix_millis": value.occurred_at_unix_millis,
    })
}

fn earn_action_json(value: kairos_integration::EarnActionResult) -> serde_json::Value {
    serde_json::json!({
        "accepted": value.accepted,
        "action_id": value.action_id,
        "status": value.status,
        "reason": value.reason,
    })
}

fn redact(value: &str) -> String {
    if value.len() <= 4 {
        return "****".into();
    }
    format!("{}****{}", &value[..2], &value[value.len() - 2..])
}

#[cfg(test)]
mod cli_tests {
    use super::Cli;
    use clap::Parser;

    #[test]
    fn command_surface_builds_without_duplicate_aliases() {
        let parsed = Cli::try_parse_from(["kairos-account-cli", "--workspace", "/tmp", "list"]);
        assert!(
            parsed.is_ok(),
            "account CLI command surface must parse: {parsed:?}"
        );
    }
}
