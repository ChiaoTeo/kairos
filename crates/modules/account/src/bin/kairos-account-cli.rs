use std::str::FromStr;

use clap::{Args, Parser, Subcommand};
use kairos_account::domain::AccountFill;
use kairos_account::{
    AccountCredentialProbeRequest, AccountProviderConnectionArgs, BindCredentialRequest,
    CliAccountApplication, ConnectAccountProviderRequest, ConnectedAccountApplication,
    CreateCredentialRequest, ModifyAccountRequest, RegisterAccountRequest, SimulateAccountRequest,
};
use kairos_account_contract::{AccountSegmentsRequest, SimulatedSettlement};
use kairos_workspace::Workspace;
use kairos_workspace::cli::{OutputFormat, render};

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
    match args.command.clone() {
        Command::Standalone(command) => run_standalone(&args, &workspace, command).await?,
        Command::Connected(command) => run_connected(&args, &workspace, command).await?,
    }
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
    #[command(name = "standalone", subcommand)]
    Standalone(StandaloneCommand),
    #[command(name = "connected", subcommand)]
    Connected(ConnectedCommand),
}

#[derive(Clone, Debug, Subcommand)]
enum StandaloneCommand {
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
    #[command(name = "snapshot", alias = "current")]
    Snapshot,
    #[command(alias = "balance")]
    Balances {
        #[arg(long)]
        include_zero: bool,
    },
    Positions,
    #[command(name = "observed-orders", alias = "open-orders")]
    OpenOrders,
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
}

#[derive(Clone, Debug, Subcommand)]
enum ConnectedCommand {
    Fill {
        #[command(flatten)]
        fill: FillArgs,
    },
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
                .map(kairos_primitives::execution::OrderId::new)
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
                .map(kairos_primitives::reference::Currency::new)
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
                .map(kairos_primitives::reference::Currency::new)
                .transpose()
                .map_err(|error| error.to_string())?,
            fee_amount: self
                .fee
                .as_deref()
                .map(str::parse)
                .transpose()
                .map_err(|error: kairos_primitives::DomainTypeError| error.to_string())?,
            occurred_at_unix_nanos: kairos_primitives::time::UnixNanos::new(0),
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

async fn run_standalone(
    args: &Cli,
    workspace: &Workspace,
    command: StandaloneCommand,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut app = CliAccountApplication::open(workspace)?;
    match &command {
        StandaloneCommand::List => {
            print_json(app.list_accounts()?);
            return Ok(());
        },
        StandaloneCommand::Browse { query } => {
            print_json(app.browse_accounts(query.as_deref())?);
            return Ok(());
        },
        StandaloneCommand::Model {
            command:
                ModelCommand::Switch {
                    account_id,
                    target,
                    reason,
                },
        } => {
            print_json(app.switch_account_model(account_id, target, reason)?);
            return Ok(());
        },
        StandaloneCommand::Show { account_id } => {
            print_json(app.show_account(account_id)?);
            return Ok(());
        },
        StandaloneCommand::Snapshot => {
            print_json(app.local_snapshot(&required_account_id(args)?)?);
            return Ok(());
        },
        StandaloneCommand::Balances { include_zero } => {
            print_json(app.local_balances(&required_account_id(args)?, *include_zero)?);
            return Ok(());
        },
        StandaloneCommand::Positions => {
            print_json(app.local_positions(&required_account_id(args)?)?);
            return Ok(());
        },
        StandaloneCommand::OpenOrders => {
            print_json(app.local_open_orders(&required_account_id(args)?)?);
            return Ok(());
        },
        StandaloneCommand::Register {
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
            print_json(app.register_account(RegisterAccountRequest {
                account_id: account_id.clone(),
                broker: broker.clone(),
                integration_provider: integration_provider.clone(),
                environment: environment.clone(),
                segment: segment.clone(),
                product: product.clone(),
                trading_mode: trading_mode.clone(),
                account_model: account_model.clone(),
                exchange: exchange.clone(),
                fields: fields.clone(),
            })?);
            return Ok(());
        },
        StandaloneCommand::Modify {
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
            print_json(app.modify_account(ModifyAccountRequest {
                account_id: account_id.clone(),
                broker: broker.clone(),
                integration_provider: integration_provider.clone(),
                exchange: exchange.clone(),
                alias: alias.clone(),
                environment: environment.clone(),
                segment: segment.clone(),
                product: product.clone(),
                trading_mode: trading_mode.clone(),
                account_model: account_model.clone(),
                credential_id: credential_id.clone(),
                credential_role: credential_role.clone(),
                status: status.clone(),
                fee_rate: fee_rate.clone(),
                initial_balances: initial_balances.clone(),
                clear_credential: *clear_credential,
                fields: fields.clone(),
            })?);
            return Ok(());
        },
        StandaloneCommand::Simulate {
            account_id,
            segment,
            account_model,
            initial_balances,
            fee_rate,
        } => {
            print_json(app.simulate_account(SimulateAccountRequest {
                account_id: account_id.clone(),
                account_model: account_model.clone(),
                segment: segment.clone(),
                initial_balances: initial_balances.clone(),
                fee_rate: fee_rate.clone(),
            })?);
            return Ok(());
        },
        StandaloneCommand::Remove { account_id, force } => {
            let _ = force;
            print_json(app.remove_account(account_id)?);
            return Ok(());
        },
        StandaloneCommand::CredentialList => {
            print_json(app.list_credentials()?);
            return Ok(());
        },
        StandaloneCommand::CredentialAdd {
            account_id,
            name,
            credential_id,
            role,
            check,
            force,
        } => {
            print_json(
                app.bind_credential_with_probe(
                    BindCredentialRequest {
                        account_id: account_id.clone(),
                        name: name.clone(),
                        credential_id: credential_id.clone(),
                        role: role.clone(),
                        force: *force,
                    },
                    AccountCredentialProbeRequest {
                        check: *check,
                        egress_scope_id: args.connection.egress_scope_id.clone(),
                        connection: provider_connection_args(&args.connection),
                    },
                )
                .await?,
            );
            return Ok(());
        },
        StandaloneCommand::CredentialCreate {
            credential_id,
            provider,
            role,
            api_key,
            secret,
            passphrase,
        } => {
            print_json(app.create_credential(CreateCredentialRequest {
                credential_id: credential_id.clone(),
                provider: provider.clone(),
                role: role.clone(),
                api_key: api_key.clone(),
                secret: secret.clone(),
                passphrase: passphrase.clone(),
            })?);
            return Ok(());
        },
        StandaloneCommand::CredentialDelete {
            credential_id,
            force,
        } => {
            print_json(app.delete_credential(credential_id, *force)?);
            return Ok(());
        },
        StandaloneCommand::CredentialShow {
            credential_id,
            reveal_secrets,
        } => {
            print_json(app.show_credential(credential_id, *reveal_secrets)?);
            return Ok(());
        },
        StandaloneCommand::Schemas => {
            print_json(app.schemas());
            return Ok(());
        },
        StandaloneCommand::Schema { provider } => {
            print_json(app.schema(provider)?);
            return Ok(());
        },
        StandaloneCommand::Doctor { account_id } => {
            print_json(app.doctor(account_id.as_deref())?);
            return Ok(());
        },
        StandaloneCommand::Connect => {
            print_json(
                app.connect_account_from_provider(ConnectAccountProviderRequest {
                    egress_scope_id: args.connection.egress_scope_id.clone(),
                    connection: provider_connection_args(&args.connection),
                })
                .await?,
            );
            return Ok(());
        },
    }
}

fn print_json(value: serde_json::Value) {
    let format = std::env::var("KAIROS_CLI_FORMAT")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(OutputFormat::Json);
    println!("{}", render(&value, format));
}

fn required_account_id(args: &Cli) -> Result<String, Box<dyn std::error::Error>> {
    args.connection
        .account_id
        .clone()
        .ok_or_else(|| "--account-id is required for this account query".into())
}

fn selected_segment(args: &ConnectionArgs) -> String {
    args.segment.clone()
}

fn provider_connection_args(args: &ConnectionArgs) -> AccountProviderConnectionArgs {
    AccountProviderConnectionArgs {
        provider: args.provider.clone(),
        broker: args.broker.clone(),
        product: args.product.clone(),
        environment: args.environment.clone(),
        account_id: args.account_id.clone(),
        alias: args.alias.clone(),
        credential_id: args.credential_id.clone(),
        trading_mode: args.trading_mode.clone(),
        api_key: args.api_key.clone(),
        secret: args.secret.clone(),
        passphrase: args.passphrase.clone(),
        base_url: args.base_url.clone(),
        segment: selected_segment(args),
        host: args.host.clone(),
        port: args.port,
        client_id: args.client_id,
    }
}

impl ConnectedCommand {
    fn is_mmap_query(&self) -> bool {
        matches!(
            self,
            Self::Snapshot { .. }
                | Self::Balances { .. }
                | Self::Positions { .. }
                | Self::OpenOrders { .. }
        )
    }
}

async fn run_connected(
    args: &Cli,
    workspace: &Workspace,
    command: ConnectedCommand,
) -> Result<(), Box<dyn std::error::Error>> {
    let app = CliAccountApplication::open(workspace)?;
    let account_id = args
        .connection
        .account_id
        .as_deref()
        .ok_or("--account-id is required for an Account connected command")?;
    let account_id = app.resolve_account_id(account_id)?;
    let value = if command.is_mmap_query() {
        let application = connected_account_app(args, workspace, &account_id, true)?;
        read_mmap_query(&application, &account_id, &command)?
    } else {
        let application = connected_account_app(args, workspace, &account_id, false)?;
        run_runtime_control(&application, args, &command).await?
    };
    print_json(value);
    Ok(())
}

fn is_paper_or_simulated(value: &str) -> bool {
    matches!(
        value.trim().to_ascii_lowercase().as_str(),
        "paper" | "simulated"
    )
}

fn connected_account_app(
    args: &Cli,
    workspace: &Workspace,
    account_id: &str,
    require_views: bool,
) -> Result<ConnectedAccountApplication, Box<dyn std::error::Error>> {
    if let Some(launch_id) = args.launch_id.as_deref() {
        let instance = workspace.instance(&args.launch_mode, launch_id, &args.instance_id)?;
        let socket_name =
            resolve_runtime_account_resource(&instance, account_id, args.socket_name.as_deref())?;
        let view_root = if require_views {
            Some(instance.snapshot(&[])?)
        } else {
            None
        };
        return ConnectedAccountApplication::connect(instance.socket(&socket_name)?, view_root);
    }
    let view_root = if require_views {
        Some(workspace.paths().snapshot(&[])?)
    } else {
        None
    };
    ConnectedAccountApplication::connect(
        workspace.process_socket(args.socket_name.as_deref().unwrap_or("account"))?,
        view_root,
    )
}

async fn run_runtime_control(
    application: &ConnectedAccountApplication,
    args: &Cli,
    command: &ConnectedCommand,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    match command {
        ConnectedCommand::Fill { fill } => {
            if !is_paper_or_simulated(args.connection.provider.as_str()) {
                return Err("simulated fill is available only for paper/simulated Account".into());
            }
            application
                .apply_simulated_settlement(fill.to_contract()?)
                .await
        },
        ConnectedCommand::Refresh { .. } => {
            let request = account_segments_request(command)?;
            application.refresh(request).await
        },
        ConnectedCommand::Reconcile { .. } => {
            let request = account_segments_request(command)?;
            application.reconcile(request).await
        },
        _ => unreachable!("runtime control command already matched"),
    }
}

fn account_segments_request(
    command: &ConnectedCommand,
) -> Result<AccountSegmentsRequest, Box<dyn std::error::Error>> {
    let segments = match command {
        ConnectedCommand::Refresh { segments } | ConnectedCommand::Reconcile { segments } => {
            segments
        },
        _ => return Err("command is not an Account segment runtime control".into()),
    };
    let segments = segments
        .iter()
        .cloned()
        .map(kairos_primitives::account::SegmentKey::new)
        .collect::<Result<Vec<_>, _>>()?;
    Ok(AccountSegmentsRequest { segments })
}

fn read_mmap_query(
    application: &ConnectedAccountApplication,
    account_id: &str,
    command: &ConnectedCommand,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    if let ConnectedCommand::OpenOrders { symbol, limit } = command {
        return application.observed_orders(account_id, symbol.as_deref(), *limit);
    }
    match command {
        ConnectedCommand::Snapshot { symbol } => {
            application.snapshot(account_id, symbol.as_deref())
        },
        ConnectedCommand::Balances {
            segments,
            include_zero,
            page,
            page_size,
        } => application.balances(account_id, segments, *include_zero, *page, *page_size),
        ConnectedCommand::Positions { segments, symbol } => {
            application.positions(account_id, segments, symbol.as_deref())
        },
        _ => unreachable!("runtime control command routed to mmap"),
    }
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

#[cfg(test)]
mod cli_tests {
    use clap::Parser;

    use super::{Cli, Command, ConnectedCommand, StandaloneCommand};

    #[test]
    fn rust_command_surface_requires_an_explicit_mode() {
        let parsed = Cli::try_parse_from(["kairos-account-cli", "--workspace", "/tmp", "list"]);
        assert!(
            parsed.is_err(),
            "account Rust CLI must force explicit standalone/connected mode"
        );

        let parsed = Cli::try_parse_from([
            "kairos-account-cli",
            "--workspace",
            "/tmp",
            "standalone",
            "list",
        ]);
        assert!(
            parsed.is_ok(),
            "account standalone command surface must parse: {parsed:?}"
        );

        let parsed = Cli::try_parse_from([
            "kairos-account-cli",
            "--workspace",
            "/tmp",
            "--account-id",
            "main",
            "--launch-id",
            "btc",
            "connected",
            "balances",
        ]);
        assert!(
            parsed.is_ok(),
            "account connected command surface must parse: {parsed:?}"
        );
    }

    #[test]
    fn account_commands_keep_the_explicit_execution_mode() {
        let parsed = Cli::try_parse_from([
            "kairos-account-cli",
            "--workspace",
            "/tmp",
            "--account-id",
            "main",
            "standalone",
            "balances",
        ])
        .expect("standalone local balances parses");
        assert!(matches!(
            parsed.command,
            Command::Standalone(StandaloneCommand::Balances { .. })
        ));

        let parsed = Cli::try_parse_from([
            "kairos-account-cli",
            "--workspace",
            "/tmp",
            "--account-id",
            "main",
            "--launch-id",
            "btc",
            "connected",
            "balances",
        ])
        .expect("connected balances parses");
        assert!(matches!(
            parsed.command,
            Command::Connected(ConnectedCommand::Balances { .. })
        ));
    }
}
