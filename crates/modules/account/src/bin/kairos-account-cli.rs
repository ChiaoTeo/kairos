use std::collections::BTreeMap;
use std::str::FromStr;

use clap::{Args, Parser, Subcommand};
use kairos_account::composition::account::{
    AccountOptions, AccountSegmentBinding, compose_binance_async_account_application,
    compose_ibkr_async_account_application, compose_local_account_application_for_segments,
    compose_okx_async_account_application, default_rest_endpoint, inspect_account_credential,
};
use kairos_account::composition::registry::{
    AccountBindingRecord, AccountCredentialBinding, AccountRegistry,
};
use kairos_account::domain::{AccountFill, AccountModel};
use kairos_account_contract::{AccountRestRequest, AccountRestResponse, SimulatedSettlement};
use kairos_conflux::{
    Conflux, ConfluxConfig, ConfluxEvent, CredentialRecord, CredentialStore,
    ExternalAccountCredentialProfile, ShutdownMode,
};
use kairos_protocol::generated::kairos::common::v_2::{Decimal64, ViewCompleteness};
use kairos_workspace::Workspace;
use kairos_workspace::cli::{OutputFormat, render};

async fn inspect_credential(
    options: &AccountOptions,
    workspace: &Workspace,
    egress_scope_id: &str,
) -> Result<ExternalAccountCredentialProfile, String> {
    inspect_account_credential(
        options,
        Some(
            workspace
                .state_root()
                .join("integration")
                .join("provider-quota.mmap"),
        ),
        egress_scope_id,
    )
    .await
}

/// One-shot account inspection and mutation commands.
#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Cli::parse();
    let workspace = Workspace::open(&args.workspace)?;
    let output = args
        .output
        .unwrap_or_else(|| {
            OutputFormat::from_workspace(workspace.cli_format())
                .expect("workspace output format validated")
        })
        .to_string();
    std::env::set_var("KAIROS_CLI_FORMAT", output);
    run_direct(&args, &workspace, args.command.clone()).await?;
    Ok(())
}

#[derive(Debug, Parser)]
#[command(name = "kairos-account-cli", about = "One-shot account commands")]
struct Cli {
    #[arg(long)]
    workspace: String,
    #[arg(long, global = true, value_parser = OutputFormat::from_str)]
    output: Option<OutputFormat>,
    #[arg(long, global = true, default_value = "paper")]
    launch_mode: String,
    #[arg(long, global = true)]
    launch_id: Option<String>,
    #[arg(long, global = true, default_value = "default")]
    instance_id: String,
    /// Explicit runtime socket/resource name. Normally resolved from the
    /// launch instance manifest using the Account identity.
    #[arg(long, global = true)]
    socket_name: Option<String>,
    #[command(flatten)]
    connection: ConnectionArgs,
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Args)]
struct ConnectionArgs {
    #[arg(long, default_value = "binance")]
    provider: String,
    /// Account-owned intermediary identity. Required when `connect` creates
    /// a new binding; it is not inferred from the Integration provider.
    #[arg(long)]
    broker: Option<String>,
    #[arg(long, default_value = "spot")]
    product: String,
    #[arg(long)]
    trading_mode: Option<String>,
    #[arg(long)]
    api_key: Option<String>,
    #[arg(long)]
    secret: Option<String>,
    #[arg(long)]
    credential_id: Option<String>,
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
    account_id: Option<String>,
    #[arg(long)]
    alias: Option<String>,
    #[arg(long, default_value = "spot")]
    segment: String,
    #[arg(long, default_value = "live")]
    environment: String,
    #[arg(long, default_value = "default-egress")]
    egress_scope_id: String,
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
        #[arg(long = "broker")]
        broker: String,
        #[arg(long)]
        integration_provider: String,
        #[arg(long, default_value = "live")]
        environment: String,
        #[arg(long, default_value = "spot")]
        segment: String,
        #[arg(long, default_value = "spot")]
        product: String,
        #[arg(long)]
        trading_mode: Option<String>,
        #[arg(long)]
        account_model: Option<String>,
        #[arg(long)]
        exchange: Option<String>,
        #[arg(long = "field")]
        fields: Vec<String>,
    },
    Modify {
        #[arg(long)]
        account_id: String,
        #[arg(long = "broker")]
        broker: Option<String>,
        #[arg(long)]
        integration_provider: Option<String>,
        #[arg(long)]
        exchange: Option<String>,
        #[arg(long)]
        alias: Option<String>,
        #[arg(long)]
        environment: Option<String>,
        #[arg(long)]
        segment: Option<String>,
        #[arg(long)]
        product: Option<String>,
        #[arg(long)]
        trading_mode: Option<String>,
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
        #[arg(long = "balance")]
        initial_balances: Vec<String>,
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
        #[arg(long = "broker")]
        provider: String,
    },
    Doctor {
        #[arg(long)]
        account_id: Option<String>,
    },
    Connect,
    #[command(name = "snapshot", alias = "current")]
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
    #[command(name = "observed-orders", alias = "open-orders")]
    OpenOrders {
        #[arg(long)]
        symbol: Option<String>,
        #[arg(long)]
        limit: Option<usize>,
    },
    Refresh {
        #[arg(long = "segment")]
        segments: Vec<String>,
    },
    Reconcile {
        #[arg(long = "segment")]
        segments: Vec<String>,
    },
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
    quantity: String,
    #[arg(long)]
    price: String,
    #[arg(long)]
    settlement_asset: Option<String>,
    #[arg(long)]
    settlement_delta: Option<String>,
    #[arg(long)]
    fee_asset: Option<String>,
    #[arg(long)]
    fee: Option<String>,
    #[arg(long, default_value = "buy")]
    side: String,
    #[arg(long)]
    order_id: Option<String>,
}

impl FillArgs {
    fn to_domain(&self) -> Result<AccountFill, String> {
        Ok(AccountFill {
            fill_id: kairos_account::domain::FillId::new(
                self.fill_id
                    .clone()
                    .ok_or("--fill-id is required for idempotency")?,
            )
            .map_err(|error| error.to_string())?,
            order_id: self
                .order_id
                .clone()
                .map(kairos_primitives::OrderId::new)
                .transpose()
                .map_err(|error| error.to_string())?,
            segment_key: kairos_account::domain::SegmentKey::new(self.segment.clone())
                .map_err(|error| error.to_string())?,
            instrument_id: kairos_account::domain::InstrumentId::new(self.instrument_id.clone())
                .map_err(|error| error.to_string())?,
            quantity: self
                .quantity
                .parse()
                .map_err(|error: kairos_primitives::DomainTypeError| error.to_string())?,
            price: self
                .price
                .parse()
                .map_err(|error: kairos_primitives::DomainTypeError| error.to_string())?,
            side: match self.side.to_ascii_lowercase().as_str() {
                "sell" => kairos_account::domain::OrderSide::Sell,
                _ => kairos_account::domain::OrderSide::Buy,
            },
            settlement_asset: self
                .settlement_asset
                .clone()
                .map(kairos_primitives::Currency::new)
                .transpose()
                .map_err(|error| error.to_string())?,
            settlement_delta: self
                .settlement_delta
                .as_deref()
                .map(str::parse)
                .transpose()
                .map_err(|error: kairos_primitives::DomainTypeError| error.to_string())?,
            fee_asset: self
                .fee_asset
                .clone()
                .map(kairos_primitives::Currency::new)
                .transpose()
                .map_err(|error| error.to_string())?,
            fee_amount: self
                .fee
                .as_deref()
                .map(str::parse)
                .transpose()
                .map_err(|error: kairos_primitives::DomainTypeError| error.to_string())?,
            occurred_at_unix_nanos: kairos_primitives::UnixNanos::new(0),
        })
    }

    fn to_contract(&self) -> Result<SimulatedSettlement, String> {
        let fill = self.to_domain()?;
        Ok(SimulatedSettlement {
            fill_id: fill.fill_id,
            order_id: fill.order_id,
            segment_key: fill.segment_key,
            instrument_id: fill.instrument_id,
            quantity: fill.quantity,
            price: fill.price,
            side: fill.side,
            settlement_asset: fill.settlement_asset,
            settlement_delta: fill.settlement_delta,
            fee_asset: fill.fee_asset,
            fee_amount: fill.fee_amount,
            occurred_at_unix_nanos: fill.occurred_at_unix_nanos,
        })
    }
}

async fn run_direct(
    args: &Cli,
    workspace: &Workspace,
    command: Command,
) -> Result<(), Box<dyn std::error::Error>> {
    let registry_path = workspace.child(&["config", "accounts", "accounts.toml"])?;
    let registry_read_path = workspace.existing_path(
        &["config", "accounts", "accounts.toml"],
        &["accounts", "accounts.toml"],
    )?;
    let mut registry = AccountRegistry::load(&registry_read_path)?;
    let credentials_path = workspace.child(&["config", "credentials", "credentials.toml"])?;
    let credentials_read_path = workspace.existing_path(
        &["config", "credentials", "credentials.toml"],
        &["credentials", "credentials.toml"],
    )?;
    let mut credential_store = CredentialStore::load(&credentials_read_path)?;
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
    if is_mmap_query(&command) {
        let account_id = args
            .connection
            .account_id
            .as_deref()
            .ok_or("--account-id is required for an Account mmap query")?;
        let account_id = resolve_account_id(&registry, account_id)?;
        let value = read_mmap_query(args, workspace, &account_id, &command)?;
        print_json(value);
        return Ok(());
    }
    if is_runtime_control(&command) {
        let account_id = args
            .connection
            .account_id
            .as_deref()
            .ok_or("--account-id is required for an Account runtime command")?;
        let account_id = resolve_account_id(&registry, account_id)?;
        let value = run_runtime_control(args, workspace, &account_id, &command).await?;
        print_json(value);
        return Ok(());
    }
    match &command {
        Command::List => {
            print_json(serde_json::to_value(&registry.accounts)?);
            return Ok(());
        },
        Command::Browse { query } => {
            let query = query.as_deref().map(str::to_ascii_lowercase);
            let accounts: Vec<_> = registry
                .accounts
                .iter()
                .filter(|record| {
                    query.as_deref().is_none_or(|query| {
                        record.account_id.to_ascii_lowercase().contains(query)
                            || record.alias.to_ascii_lowercase().contains(query)
                            || record.broker.to_ascii_lowercase().contains(query)
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
        },
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
        },
        Command::Show { account_id } => {
            let value = registry
                .accounts
                .iter()
                .find(|record| record.account_id == *account_id)
                .ok_or_else(|| format!("account not found: {account_id}"))?;
            print_json(serde_json::to_value(value)?);
            return Ok(());
        },
        Command::Register {
            account_id,
            broker,
            integration_provider,
            environment,
            segment,
            product,
            trading_mode,
            account_model,
            exchange,
            fields,
        } => {
            let values = parse_field_values(fields)?;
            registry.upsert_account(AccountBindingRecord {
                account_id: account_id.clone(),
                alias: account_id.clone(),
                broker: broker.clone(),
                integration_provider: integration_provider.clone(),
                exchange: exchange.clone(),
                environment: environment.clone(),
                remote_identity: None,
                permissions: BTreeMap::new(),
                segments: vec![segment.clone()],
                segment_products: BTreeMap::from([(segment.clone(), product.clone())]),
                segment_trading_modes: trading_mode
                    .as_ref()
                    .map(|value| BTreeMap::from([(segment.clone(), value.clone())]))
                    .unwrap_or_default(),
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
        },
        Command::Modify {
            account_id,
            broker,
            integration_provider,
            exchange,
            alias,
            environment,
            segment,
            product,
            trading_mode,
            account_model,
            credential_id,
            credential_role,
            status,
            fee_rate,
            initial_balances,
            clear_credential,
            fields,
        } => {
            let mut record = registry
                .accounts
                .iter()
                .find(|value| value.account_id == *account_id)
                .cloned()
                .ok_or_else(|| format!("account not found: {account_id}"))?;
            if let Some(value) = broker {
                record.broker = value.clone();
            }
            if let Some(value) = integration_provider {
                record.integration_provider = value.clone();
            }
            if let Some(value) = exchange {
                record.exchange = Some(value.clone());
            }
            if let Some(value) = alias {
                record.alias = value.clone();
            }
            if let Some(value) = environment {
                record.environment = value.clone();
            }
            if let Some(value) = segment {
                record.segments = vec![value.clone()];
                let provider_product = product
                    .clone()
                    .ok_or("--product is required when changing --segment")?;
                record.segment_products = BTreeMap::from([(value.clone(), provider_product)]);
                record.segment_trading_modes.clear();
            } else if let Some(value) = product {
                let segment_key = record
                    .segments
                    .first()
                    .cloned()
                    .ok_or("account has no segment to assign the product")?;
                record.segment_products.insert(segment_key, value.clone());
            }
            if let Some(value) = trading_mode {
                let segment_key = record
                    .segments
                    .first()
                    .cloned()
                    .ok_or("account has no segment to assign the trading mode")?;
                record
                    .segment_trading_modes
                    .insert(segment_key, value.clone());
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
            if !initial_balances.is_empty() {
                record.initial_balances = initial_balances.clone();
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
        },
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
                broker: "paper".into(),
                integration_provider: "paper".into(),
                exchange: Some("paper".into()),
                environment: "paper".into(),
                remote_identity: None,
                permissions: BTreeMap::new(),
                segments: vec![segment.clone()],
                segment_products: BTreeMap::from([(segment.clone(), "paper".into())]),
                segment_trading_modes: BTreeMap::new(),
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
        },
        Command::Remove { account_id, force } => {
            let _ = force;
            let removed = registry.remove_account(account_id);
            registry.save(&registry_path)?;
            print_json(serde_json::json!({"account_id": account_id, "removed": removed}));
            return Ok(());
        },
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
        },
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
                let profile =
                    inspect_credential(&options, workspace, &args.connection.egress_scope_id)
                        .await
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
        },
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
        },
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
        },
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
        },
        Command::Schemas => {
            print_json(serde_json::json!({
                "binance": {"credential_fields": ["api_key", "api_secret"], "segments": ["spot", "cross_margin", "isolated_margin", "usd_m_futures", "coin_m_futures", "funding", "options"]},
                "okx": {"credential_fields": ["api_key", "api_secret", "passphrase"], "segments": ["spot", "cross_margin", "isolated_margin", "swap", "futures", "options"]},
                "ibkr": {"credential_fields": [], "connection_fields": ["host", "port", "client_id"], "segments": ["equity"]},
                "paper": {"credential_fields": [], "segments": ["spot", "margin", "futures"]}
            }));
            return Ok(());
        },
        Command::Schema { provider } => {
            let provider = provider.to_ascii_lowercase();
            let value = match provider.as_str() {
                "binance" => {
                    serde_json::json!({"provider":"binance","credential_fields":["api_key","api_secret"],"segments":["spot","cross_margin","isolated_margin","usd_m_futures","coin_m_futures","funding","options"]})
                },
                "okx" | "okex" => {
                    serde_json::json!({"provider":"okx","credential_fields":["api_key","api_secret","passphrase"],"segments":["spot","cross_margin","isolated_margin","swap","futures","options"]})
                },
                "ibkr" => {
                    serde_json::json!({"provider":"ibkr","credential_fields":[],"connection_fields":["host","port","client_id"],"segments":["equity"]})
                },
                "paper" => {
                    serde_json::json!({"provider":"paper","credential_fields":[],"segments":["spot","margin","futures"]})
                },
                _ => return Err(format!("unsupported provider: {provider}").into()),
            };
            print_json(value);
            return Ok(());
        },
        Command::Doctor { account_id } => {
            let selected_account_id = account_id
                .as_deref()
                .map(|value| resolve_account_id(&registry, value))
                .transpose()?;
            let selected_accounts = registry
                .accounts
                .iter()
                .filter(|account| {
                    selected_account_id
                        .as_deref()
                        .is_none_or(|value| value == account.account_id)
                })
                .collect::<Vec<_>>();
            let issues: Vec<_> = registry
                .accounts
                .iter()
                .filter(|account| {
                    selected_account_id
                        .as_deref()
                        .is_none_or(|value| value == account.account_id)
                })
                .filter(|account| {
                    account.environment == "live"
                        && !credential_store.credentials.iter().any(|credential| {
                            credential.provider == account.integration_provider
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
            let runtime = if args.launch_id.is_some() {
                selected_accounts
                    .iter()
                    .map(|account| {
                        runtime_account_diagnostic(args, workspace, account)
                            .map(|value| (account.account_id.clone(), value))
                    })
                    .collect::<Result<serde_json::Map<_, _>, _>>()?
            } else {
                serde_json::Map::new()
            };
            print_json(serde_json::json!({
                "accounts": selected_accounts,
                "issues": issues,
                "runtime": runtime,
            }));
            return Ok(());
        },
        _ => {},
    }
    let account_id = args.connection.account_id.clone();
    let selected_segment = selected_segment(&args.connection);
    let account_id = account_id
        // A live account can be connected from a credential alone.  The
        // provider may not expose a stable universal user id (Binance Spot
        // is one example), so the credential reference is the provisional
        // local binding identity, exactly as legacy did before discovery
        // returned a remote identity.
        .or_else(|| {
            if matches!(&command, Command::Connect) {
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
        .map(|record| record.integration_provider.clone())
        .unwrap_or_else(|| args.connection.provider.clone());
    let product = account_record
        .as_ref()
        .and_then(|record| {
            record
                .segments
                .first()
                .and_then(|segment| record.product_for_segment(segment))
                .map(str::to_owned)
        })
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
            .ok_or("an API key is required; provide --api-key or an Integration credential")?
    };
    let secret = if paper {
        String::new()
    } else {
        args.connection
            .secret
            .clone()
            .or_else(|| credential.and_then(|record| record.secret_value()))
            .ok_or("an API secret is required; provide --secret or an Integration credential")?
    };
    let passphrase = if args.connection.passphrase.is_empty() {
        credential
            .and_then(|record| record.passphrase_value())
            .unwrap_or_default()
    } else {
        args.connection.passphrase.clone()
    };
    let base_url = if args.connection.base_url.trim().is_empty() {
        default_rest_endpoint(&provider, &product)?.to_owned()
    } else {
        args.connection.base_url.clone()
    };
    let options = AccountOptions {
        provider,
        product,
        api_key: api_key.into(),
        secret: secret.into(),
        passphrase: passphrase.into(),
        base_url,
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
        isolated_margin_symbol: account_record
            .as_ref()
            .and_then(|value| value.values.get("isolated_margin_symbol").cloned()),
        reference_database: Some(workspace.child(&["state", "reference", "reference.sqlite"])?),
    };
    let state = workspace.child(&["state", "account", "account-state.json"])?;
    let configured_segment_keys = registry
        .accounts
        .iter()
        .find(|record| record.account_id == account_id)
        .map(|record| record.segments.clone())
        .filter(|segments| !segments.is_empty())
        .unwrap_or_else(|| vec![selected_segment.clone()]);
    let configured_segments = if let Some(record) = account_record.as_ref() {
        configured_segment_keys
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
        let binding = AccountSegmentBinding::new(&selected_segment, &options.product);
        vec![
            args.connection
                .trading_mode
                .as_ref()
                .map_or(binding.clone(), |mode| binding.with_trading_mode(mode)),
        ]
    };
    let native_binance_account = options.provider.eq_ignore_ascii_case("binance")
        && configured_segments.iter().all(|segment| {
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
            workspace.exclusive_process_lock("ibkr-client", &identity)
        })
        .transpose()
        .map_err(|error| error.to_string())?;
    let shared_quota_ledger = workspace
        .state_root()
        .join("integration")
        .join("provider-quota.mmap");
    let composition = if native_binance_account {
        compose_binance_async_account_application(
            &options,
            &configured_segments,
            Some(state),
            "wss://ws-api.binance.com:443/ws-api/v3",
            Some(shared_quota_ledger.clone()),
            &args.connection.egress_scope_id,
        )?
    } else if native_okx_account {
        compose_okx_async_account_application(
            &options,
            &configured_segments,
            Some(state),
            None,
            Some(shared_quota_ledger),
            &args.connection.egress_scope_id,
        )?
    } else if native_ibkr_account {
        compose_ibkr_async_account_application(&options, &configured_segments, Some(state))?
    } else {
        compose_local_account_application_for_segments(&options, &configured_segments, Some(state))?
    };
    let settlement = if let Command::Fill { fill } = &command {
        if !paper {
            return Err("simulated fill is available only for paper/simulated Account".into());
        }
        Some(fill.to_contract()?)
    } else {
        None
    };
    let (application, system) = composition.into_conflux(std::time::Duration::from_secs(30))?;
    let (conflux, handle) = Conflux::new(application, system, ConfluxConfig::default())?;
    let commands = async move {
        let ready = handle
            .handle(ConfluxEvent::Rest(AccountRestRequest::Health))
            .await
            .map_err(|_| "Account Conflux stopped during startup".to_string())?;
        match ready {
            Some(AccountRestResponse::Health(Ok(_))) => {},
            Some(AccountRestResponse::Health(Err(error))) => return Err(error.message),
            _ => return Err("Account Actor omitted its health response".into()),
        }
        if let Some(settlement) = settlement {
            let response = handle
                .handle(ConfluxEvent::Rest(
                    AccountRestRequest::ApplySimulatedSettlement(settlement),
                ))
                .await
                .map_err(|_| "Account Conflux stopped during settlement".to_string())?;
            match response {
                Some(AccountRestResponse::ApplySimulatedSettlement(Ok(_))) => {},
                Some(AccountRestResponse::ApplySimulatedSettlement(Err(error))) => {
                    return Err(error.message);
                },
                _ => return Err("Account Actor omitted its settlement response".into()),
            }
        }
        handle.shutdown(ShutdownMode::Drain);
        Ok::<(), String>(())
    };
    let (outcome, commands) = tokio::join!(conflux.run(), commands);
    commands?;
    outcome.map_err(|error| error.to_string())?;
    if matches!(&command, Command::Fill { .. }) {
        print_json(serde_json::json!({
            "status": "accepted",
        }));
        return Ok(());
    }
    if matches!(&command, Command::Connect) {
        let broker = args
            .connection
            .broker
            .clone()
            .ok_or("--broker is required when connect creates an Account binding")?;
        let credential_profile =
            inspect_credential(&options, workspace, &args.connection.egress_scope_id)
                .await
                .ok();
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
            broker,
            integration_provider: options.provider.clone(),
            exchange: Some(options.provider.clone()),
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
            segment_products: discovered_segments
                .iter()
                .map(|value| (value.clone(), value.clone()))
                .collect(),
            segment_trading_modes: args
                .connection
                .trading_mode
                .as_ref()
                .map(|mode| {
                    discovered_segments
                        .iter()
                        .map(|segment| (segment.clone(), mode.clone()))
                        .collect()
                })
                .unwrap_or_default(),
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
            "credential_profile": credential_profile,
        }));
        return Ok(());
    }
    unreachable!("all Account CLI commands return from their dedicated path")
}

fn print_json(value: serde_json::Value) {
    let format = std::env::var("KAIROS_CLI_FORMAT")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(OutputFormat::Json);
    println!("{}", render(&value, format));
}

fn selected_segment(args: &ConnectionArgs) -> String {
    args.segment.clone()
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

fn is_mmap_query(command: &Command) -> bool {
    matches!(
        command,
        Command::Snapshot { .. }
            | Command::Balances { .. }
            | Command::Positions { .. }
            | Command::OpenOrders { .. }
    )
}

fn is_runtime_control(command: &Command) -> bool {
    matches!(command, Command::Refresh { .. } | Command::Reconcile { .. })
}

async fn run_runtime_control(
    args: &Cli,
    workspace: &Workspace,
    account_id: &str,
    command: &Command,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    let launch_id = args
        .launch_id
        .as_deref()
        .ok_or("--launch-id is required for an Account runtime command")?;
    let instance = workspace.instance(&args.launch_mode, launch_id, &args.instance_id)?;
    let socket_name =
        resolve_runtime_account_resource(&instance, account_id, args.socket_name.as_deref())?;
    let (path, segments) = match command {
        Command::Refresh { segments } => ("/v1/refresh", segments),
        Command::Reconcile { segments } => ("/v1/reconcile", segments),
        _ => return Err("command is not an Account runtime control".into()),
    };
    let body = serde_json::to_vec(&serde_json::json!({
        "account_id": account_id,
        "segments": segments,
    }))?;
    Ok(
        kairos_workspace::control::RestControlClient::new(instance.socket(&socket_name)?)
            .request_json("POST", path, Some(&body))
            .await?,
    )
}

fn runtime_account_diagnostic(
    args: &Cli,
    workspace: &Workspace,
    account: &AccountBindingRecord,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    let launch_id = args
        .launch_id
        .as_deref()
        .ok_or("--launch-id is required for runtime diagnostics")?;
    let instance = workspace.instance(&args.launch_mode, launch_id, &args.instance_id)?;
    let manifest: serde_json::Value =
        serde_json::from_slice(&std::fs::read(instance.component_manifest()?)?)?;
    let endpoint = manifest
        .get("accounts")
        .and_then(|accounts| accounts.get(&account.account_id));
    let health: Option<serde_json::Value> = endpoint
        .and_then(|value| value.get("health"))
        .and_then(serde_json::Value::as_str)
        .and_then(|path| std::fs::read(path).ok())
        .and_then(|payload| serde_json::from_slice(&payload).ok());
    let (current, current_error) = match read_mmap_query(
        args,
        workspace,
        &account.account_id,
        &Command::Snapshot { symbol: None },
    ) {
        Ok(value) => (Some(value), None),
        Err(error) => (None, Some(error.to_string())),
    };
    Ok(serde_json::json!({
        "account_id": account.account_id,
        "credential_role": account.credential_role,
        "permissions": account.permissions,
        "required_segments": endpoint
            .and_then(|value| value.get("required_segments"))
            .cloned()
            .unwrap_or_else(|| serde_json::json!([])),
        "health": health,
        "current": current,
        "error": current_error,
    }))
}

fn decimal_text(value: &Decimal64) -> String {
    let scale = value.scale() as usize;
    let negative = value.mantissa() < 0;
    let digits = i128::from(value.mantissa()).abs().to_string();
    if scale == 0 {
        return format!("{}{digits}", if negative { "-" } else { "" });
    }
    let padded = format!("{:0>width$}", digits, width = scale + 1);
    let split = padded.len() - scale;
    format!(
        "{}{}.{}",
        if negative { "-" } else { "" },
        &padded[..split],
        &padded[split..]
    )
}

fn optional_decimal(value: Option<&Decimal64>) -> serde_json::Value {
    value
        .map(decimal_text)
        .map(serde_json::Value::String)
        .unwrap_or(serde_json::Value::Null)
}

fn read_mmap_query(
    args: &Cli,
    workspace: &Workspace,
    account_id: &str,
    command: &Command,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    let launch_id = args
        .launch_id
        .as_deref()
        .ok_or("--launch-id is required for an Account mmap query")?;
    let instance = workspace.instance(&args.launch_mode, launch_id, &args.instance_id)?;
    if let Command::OpenOrders { symbol, limit } = command {
        let view_root = instance.snapshot(&[])?;
        let key = kairos_account_contract::AccountViewKey::new(
            format!("account:{account_id}"),
            account_id,
            kairos_account_contract::AccountViewKind::ObservedOrders,
        )?;
        let frame =
            kairos_account_contract::view::AccountViewReader::open(view_root, key)?.read()?;
        let view = frame.observed_orders()?;
        let metadata = view.metadata();
        if view.account_id() != account_id
            || metadata.completeness() != ViewCompleteness::COMPLETE
            || metadata.generation() != frame.generation()
            || metadata.applied_revision() != Some(frame.envelope_metadata().applied_event_sequence)
        {
            return Err(
                "Account observed-orders mmap identity, completeness, or watermark mismatch".into(),
            );
        }
        let mut orders = Vec::new();
        for segment in view.segments() {
            for order in segment.orders() {
                if symbol.as_deref().is_some_and(|needle| {
                    !order.instrument_id().eq_ignore_ascii_case(needle)
                        && !order.market_id().eq_ignore_ascii_case(needle)
                }) {
                    continue;
                }
                orders.push(serde_json::json!({
                    "segment_key": segment.segment_key(),
                    "observation_id": order.observation_id(),
                    "source_id": order.source_id(),
                    "execution_order_id": order.execution_order_id(),
                    "remote_order_id": order.remote_order_id(),
                    "instrument_id": order.instrument_id(),
                    "market_id": order.market_id(),
                    "side": order.side().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                    "quantity": decimal_text(order.quantity()),
                    "filled_quantity": decimal_text(order.filled_quantity()),
                    "status": order.status().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                    "observed_at_unix_nanos": order.observed_at_unix_nanos(),
                }));
                if limit.is_some_and(|limit| orders.len() >= limit) {
                    break;
                }
            }
            if limit.is_some_and(|limit| orders.len() >= limit) {
                break;
            }
        }
        return Ok(serde_json::json!({
            "account_id": account_id,
            "generation": frame.generation(),
            "orders": orders,
        }));
    }

    let view_root = instance.snapshot(&[])?;
    let key = kairos_account_contract::AccountViewKey::new(
        format!("account:{account_id}"),
        account_id,
        kairos_account_contract::AccountViewKind::Current,
    )?;
    let frame = kairos_account_contract::view::AccountViewReader::open(view_root, key)?.read()?;
    let view = frame.account_current()?;
    let metadata = view.metadata();
    if view.account_id() != account_id
        || metadata.completeness() != ViewCompleteness::COMPLETE
        || metadata.generation() != frame.generation()
        || metadata.applied_revision() != Some(frame.envelope_metadata().applied_event_sequence)
    {
        return Err("Account current mmap identity, completeness, or watermark mismatch".into());
    }
    let segment_filter: &[String] = match command {
        Command::Balances { segments, .. } | Command::Positions { segments, .. } => segments,
        _ => &[],
    };
    let symbol_filter = match command {
        Command::Snapshot { symbol } | Command::Positions { symbol, .. } => symbol.as_deref(),
        _ => None,
    };
    let include_zero = matches!(
        command,
        Command::Balances {
            include_zero: true,
            ..
        }
    );
    let mut segments = Vec::new();
    for segment in view.segments() {
        if !segment_filter.is_empty()
            && !segment_filter
                .iter()
                .any(|value| value == segment.segment_key())
        {
            continue;
        }
        let balances = segment
            .balances()
            .iter()
            .filter(|balance| include_zero || balance.total().mantissa() != 0)
            .map(|balance| {
                serde_json::json!({
                    "asset_id": balance.asset_id(),
                    "asset_code": balance.asset_code(),
                    "total": decimal_text(balance.total()),
                    "available": optional_decimal(balance.available()),
                    "locked": optional_decimal(balance.locked()),
                    "borrowed": optional_decimal(balance.borrowed()),
                    "interest": optional_decimal(balance.interest()),
                })
            })
            .collect::<Vec<_>>();
        let positions = segment
            .positions()
            .iter()
            .filter(|position| {
                symbol_filter.is_none_or(|needle| {
                    position.instrument_id().eq_ignore_ascii_case(needle)
                        || position.market_id().eq_ignore_ascii_case(needle)
                })
            })
            .map(|position| {
                serde_json::json!({
                    "instrument_id": position.instrument_id(),
                    "market_id": position.market_id(),
                    "quantity": decimal_text(position.quantity()),
                    "average_price": optional_decimal(position.average_price()),
                    "mark_price": optional_decimal(position.mark_price()),
                    "unrealized_pnl": optional_decimal(position.unrealized_pnl()),
                    "realized_pnl": optional_decimal(position.realized_pnl()),
                    "observed_at_unix_nanos": position.observed_at_unix_nanos(),
                })
            })
            .collect::<Vec<_>>();
        segments.push(serde_json::json!({
            "segment_key": segment.segment_key(),
            "environment": segment.environment(),
            "broker": segment.broker(),
            "status": segment.status().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
            "freshness": segment.freshness().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
            "sync_mode": segment.sync_mode().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
            "sync_lifecycle": segment.sync_lifecycle().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
            "completeness": segment.completeness().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
            "snapshot_watermark": segment.snapshot_watermark(),
            "event_watermark": segment.event_watermark(),
            "channel_epoch": segment.channel_epoch(),
            "last_event_at_unix_nanos": segment.last_event_at_unix_nanos(),
            "last_success_at_unix_nanos": segment.last_success_at_unix_nanos(),
            "last_error": segment.last_error(),
            "recovery_buffer_depth": segment.recovery_buffer_depth(),
            "observed_at_unix_nanos": segment.observed_at_unix_nanos(),
            "state_generation": segment.state_generation(),
            "balances": balances,
            "positions": positions,
        }));
    }
    let mut result = serde_json::json!({
        "account_id": account_id,
        "generation": frame.generation(),
        "event_sequence": frame.envelope_metadata().applied_event_sequence,
        "producer_incarnation": frame.envelope_metadata().producer_incarnation,
        "segments": segments,
    });
    if let Command::Balances {
        page, page_size, ..
    } = command
    {
        result["page"] = (*page).into();
        result["page_size"] = (*page_size).into();
    }
    Ok(result)
}

fn resolve_runtime_account_resource(
    instance: &kairos_workspace::InstanceWorkspace,
    account_id: &str,
    explicit: Option<&str>,
) -> Result<String, Box<dyn std::error::Error>> {
    if let Some(value) = explicit {
        return Ok(value.to_owned());
    }

    let manifest_path = instance.component_manifest()?;
    if let Ok(bytes) = std::fs::read(&manifest_path) {
        let manifest: serde_json::Value = serde_json::from_slice(&bytes)?;
        if let Some(socket_name) = manifest
            .get("accounts")
            .and_then(|accounts| accounts.get(account_id))
            .and_then(|account| account.get("socket_name"))
            .and_then(serde_json::Value::as_str)
        {
            return Ok(socket_name.to_owned());
        }
    }

    Err(format!(
        "launch instance manifest has no runtime Account resource for {account_id}; use --socket-name for a manually managed process"
    )
    .into())
}

fn credential_probe_options(
    args: &Cli,
    account: &AccountBindingRecord,
    credential: &CredentialRecord,
) -> Result<AccountOptions, Box<dyn std::error::Error>> {
    let paper = matches!(
        account
            .integration_provider
            .trim()
            .to_ascii_lowercase()
            .as_str(),
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
    let product = account
        .segments
        .first()
        .and_then(|segment| account.product_for_segment(segment))
        .map(str::to_owned)
        .unwrap_or_else(|| args.connection.product.clone());
    let base_url = if args.connection.base_url.trim().is_empty() {
        default_rest_endpoint(&account.integration_provider, &product)?.to_owned()
    } else {
        args.connection.base_url.clone()
    };
    Ok(AccountOptions {
        provider: account.integration_provider.clone(),
        product,
        api_key: api_key.into(),
        secret: secret.into(),
        passphrase: passphrase.into(),
        base_url,
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
        isolated_margin_symbol: account.values.get("isolated_margin_symbol").cloned(),
        reference_database: None,
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
    use clap::Parser;

    use super::Cli;

    #[test]
    fn command_surface_builds_without_duplicate_aliases() {
        let parsed = Cli::try_parse_from(["kairos-account-cli", "--workspace", "/tmp", "list"]);
        assert!(
            parsed.is_ok(),
            "account CLI command surface must parse: {parsed:?}"
        );
    }
}
