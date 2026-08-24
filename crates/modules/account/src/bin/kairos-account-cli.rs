use std::str::FromStr;

use clap::{Args, Parser, Subcommand};
use kairos_account::domain::AccountFill;
use kairos_account::{
    AccountBalanceItem, AccountBalancesResult, AccountCredentialProbeRequest,
    AccountEarnHoldingsResult, AccountFeesResult, AccountListItem, AccountListResult,
    AccountOpenOrdersResult, AccountOverviewResult, AccountPositionsResult,
    AccountProviderConnectionArgs, AccountQueryCompleteness, BindCredentialRequest,
    CliAccountApplication, ConnectAccountProviderRequest, ConnectedAccountApplication,
    ConnectedAccountCurrentResult, ConnectedAccountOutput, CreateCredentialRequest,
    ModifyAccountRequest, RegisterAccountRequest, SimulateAccountRequest,
};
use kairos_account_contract::{AccountSegmentsRequest, SimulatedSettlement};
use kairos_workspace::Workspace;
use kairos_workspace::cli::{OutputFormat, render, render_compact_table};
use serde::Serialize;

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
    #[arg(
        long,
        visible_alias = "format",
        global = true,
        value_parser = OutputFormat::from_str
    )]
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
    TradingBinding {
        #[arg(long)]
        account_id: String,
        #[arg(long)]
        segment: Option<String>,
        #[arg(long, default_value = "read")]
        access: String,
    },
    #[command(alias = "summary")]
    Overview,
    #[command(name = "snapshot", alias = "current")]
    Snapshot,
    #[command(alias = "balance")]
    Balances {
        #[arg(long = "segment")]
        segments: Vec<String>,
        #[arg(long)]
        include_zero: bool,
    },
    Assets {
        #[arg(long = "segment")]
        segments: Vec<String>,
        #[arg(long)]
        include_zero: bool,
    },
    Positions {
        #[arg(long = "segment")]
        segments: Vec<String>,
        #[arg(long)]
        symbol: Option<String>,
    },
    #[command(name = "earn-holdings", alias = "earn")]
    EarnHoldings {
        #[arg(long)]
        family: Option<String>,
        #[arg(long)]
        asset: Option<String>,
    },
    #[command(name = "open-orders", alias = "observed-orders")]
    OpenOrders {
        #[arg(long = "segment")]
        segments: Vec<String>,
        #[arg(long)]
        symbol: Option<String>,
    },
    Fees {
        #[arg(long)]
        product: String,
        #[arg(long)]
        symbol: Option<String>,
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
        #[arg(
            long,
            hide = true,
            help = "Legacy plaintext input; new writes reject it"
        )]
        api_key: Option<String>,
        #[arg(
            long,
            hide = true,
            help = "Legacy plaintext input; new writes reject it"
        )]
        secret: Option<String>,
        #[arg(
            long,
            hide = true,
            help = "Legacy plaintext input; new writes reject it"
        )]
        passphrase: Option<String>,
        #[arg(long, value_parser = ["env", "file"])]
        api_key_source: Option<String>,
        #[arg(long)]
        api_key_ref: Option<String>,
        #[arg(long, value_parser = ["env", "file"])]
        api_secret_source: Option<String>,
        #[arg(long)]
        api_secret_ref: Option<String>,
        #[arg(long, value_parser = ["env", "file"])]
        passphrase_source: Option<String>,
        #[arg(long)]
        passphrase_ref: Option<String>,
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
            print_account_list(app.list_accounts()?)?;
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
        StandaloneCommand::TradingBinding {
            account_id,
            segment,
            access,
        } => {
            print_json(app.trading_binding(account_id, segment.as_deref(), access)?);
            return Ok(());
        },
        StandaloneCommand::Overview => {
            print_account_overview(app.overview(&required_account_id(args)?).await?)?;
            return Ok(());
        },
        StandaloneCommand::Snapshot => {
            print_json(app.local_snapshot(&required_account_id(args)?)?);
            return Ok(());
        },
        StandaloneCommand::Balances {
            segments,
            include_zero,
        } => {
            print_account_balances(
                app.balances(&required_account_id(args)?, segments, *include_zero)
                    .await?,
            )?;
            return Ok(());
        },
        StandaloneCommand::Assets {
            segments,
            include_zero,
        } => {
            print_account_balances(
                app.assets(&required_account_id(args)?, segments, *include_zero)
                    .await?,
            )?;
            return Ok(());
        },
        StandaloneCommand::Positions { segments, symbol } => {
            print_account_positions(
                app.positions(&required_account_id(args)?, segments, symbol.as_deref())
                    .await?,
            )?;
            return Ok(());
        },
        StandaloneCommand::EarnHoldings { family, asset } => {
            print_account_earn_holdings(
                app.earn_holdings(
                    &required_account_id(args)?,
                    family.as_deref(),
                    asset.as_deref(),
                )
                .await?,
            )?;
            return Ok(());
        },
        StandaloneCommand::OpenOrders { segments, symbol } => {
            print_account_open_orders(
                app.open_orders(&required_account_id(args)?, segments, symbol.as_deref())
                    .await?,
            )?;
            return Ok(());
        },
        StandaloneCommand::Fees { product, symbol } => {
            print_account_fees(
                app.fees(&required_account_id(args)?, product, symbol.as_deref())
                    .await?,
            )?;
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
            api_key_source,
            api_key_ref,
            api_secret_source,
            api_secret_ref,
            passphrase_source,
            passphrase_ref,
        } => {
            print_json(app.create_credential(CreateCredentialRequest {
                credential_id: credential_id.clone(),
                provider: provider.clone(),
                role: role.clone(),
                api_key: api_key.clone(),
                secret: secret.clone(),
                passphrase: passphrase.clone(),
                api_key_source: api_key_source.clone(),
                api_key_ref: api_key_ref.clone(),
                secret_source: api_secret_source.clone(),
                secret_ref: api_secret_ref.clone(),
                passphrase_source: passphrase_source.clone(),
                passphrase_ref: passphrase_ref.clone(),
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

fn print_json(value: impl Serialize) {
    println!("{}", render(&value, selected_output_format()));
}

fn selected_output_format() -> OutputFormat {
    std::env::var("KAIROS_CLI_FORMAT")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(OutputFormat::Json)
}

fn print_account_list(value: AccountListResult) -> Result<(), serde_json::Error> {
    let output = match selected_output_format() {
        OutputFormat::Json => render(&value, OutputFormat::Json),
        OutputFormat::Text | OutputFormat::Table => render_account_list(&value),
    };
    println!("{output}");
    Ok(())
}

fn print_account_overview(value: AccountOverviewResult) -> Result<(), serde_json::Error> {
    let output = match selected_output_format() {
        OutputFormat::Json => render(&value, OutputFormat::Json),
        OutputFormat::Text | OutputFormat::Table => render_account_overview(&value),
    };
    println!("{output}");
    Ok(())
}

fn render_account_overview(value: &AccountOverviewResult) -> String {
    let unified = value
        .profile
        .unified
        .map(|value| if value { "是" } else { "否" })
        .unwrap_or("未能确认");
    let rows = vec![
        vec!["账户".into(), value.identity.account_id.to_string()],
        vec!["券商/托管方".into(), value.identity.broker.to_string()],
        vec![
            "交易所".into(),
            value
                .identity
                .exchange
                .clone()
                .unwrap_or_else(|| "—".into()),
        ],
        vec!["环境".into(), localized_status(&value.identity.environment)],
        vec![
            "集成提供方".into(),
            value.connection.integration_adapter.to_string(),
        ],
        vec![
            "配置账户模式".into(),
            value
                .profile
                .configured_account_model
                .clone()
                .map(|model| localized_model(&model))
                .unwrap_or_else(|| "未配置（采用实测）".into()),
        ],
        vec![
            "实测账户模式".into(),
            value
                .profile
                .observed_account_model
                .clone()
                .map(|model| localized_model(&model))
                .unwrap_or_else(|| "未能确认".into()),
        ],
        vec![
            "Binance 原生模式".into(),
            value
                .profile
                .provider_account_model
                .clone()
                .map(|model| localized_model(&model))
                .unwrap_or_else(|| "未能确认".into()),
        ],
        vec![
            "模式一致性".into(),
            localized_status(&value.profile.model_match),
        ],
        vec!["Binance 统一保证金账户".into(), unified.into()],
        vec![
            "保证金模式".into(),
            value
                .profile
                .margin_mode
                .clone()
                .map(|mode| localized_status(&mode))
                .unwrap_or_else(|| "不适用或未返回".into()),
        ],
        vec![
            "持仓模式".into(),
            value
                .profile
                .position_mode
                .clone()
                .map(|mode| localized_status(&mode))
                .unwrap_or_else(|| "未能查询".into()),
        ],
        vec![
            "费率".into(),
            localized_status(&value.commercial.fee_summary_status),
        ],
        vec![
            "VIP 等级".into(),
            value
                .commercial
                .vip_tier
                .clone()
                .unwrap_or_else(|| "接口不单独返回（实际费率已含账户优惠）".into()),
        ],
        vec![
            "有效权限".into(),
            value
                .permissions
                .effective_capabilities
                .iter()
                .map(|value| localized_status(value))
                .collect::<Vec<_>>()
                .join("、"),
        ],
        vec![
            "非零资产".into(),
            value.facts.non_zero_balance_count.to_string(),
        ],
        vec![
            "保证金资产".into(),
            value.facts.collateral_count.to_string(),
        ],
        vec!["交易持仓".into(), value.facts.position_count.to_string()],
        vec![
            "理财持有".into(),
            optional_count(value.facts.earn_holding_count),
        ],
        vec![
            "未完成订单".into(),
            optional_count(value.facts.open_order_count),
        ],
        vec![
            "数据完整度".into(),
            localized_status(&format!("{:?}", value.health.completeness).to_ascii_lowercase()),
        ],
        vec![
            "健康状态".into(),
            localized_status(&value.health.overall_status),
        ],
        vec!["数据时效".into(), localized_status(&value.health.freshness)],
        vec![
            "账户分区".into(),
            format!(
                "{}/{}",
                value.health.segments_succeeded, value.health.segments_requested
            ),
        ],
    ];
    let mut output = format!(
        "账户 {} · {} · {}\n{}",
        value.identity.account_id,
        localized_status(&value.health.mode),
        localized_status(&value.health.source),
        render_compact_table(&["账户概览", "值"], &rows)
    );
    if !value.profile.segments.is_empty() {
        let segment_rows = value
            .profile
            .segments
            .iter()
            .map(|segment| {
                vec![
                    segment.segment.to_string(),
                    localized_status(&format!("{:?}", segment.completeness).to_ascii_lowercase()),
                    localized_status(&segment.freshness),
                    segment
                        .observed_account_model
                        .clone()
                        .map(|model| localized_model(&model))
                        .unwrap_or_else(|| "不适用".into()),
                    segment
                        .provider_account_model
                        .clone()
                        .map(|model| localized_model(&model))
                        .unwrap_or_else(|| "未返回".into()),
                    segment
                        .observed_at_unix_nanos
                        .map(|value| value.to_string())
                        .unwrap_or_else(|| "—".into()),
                    segment.issue.clone().unwrap_or_else(|| "—".into()),
                ]
            })
            .collect::<Vec<_>>();
        output.push_str("\n\n");
        output.push_str(&render_compact_table(
            &[
                "分区",
                "完整度",
                "时效",
                "实测模式",
                "原生模式",
                "观测时间(NS)",
                "问题",
            ],
            &segment_rows,
        ));
    }
    if !value.health.issues.is_empty() {
        output.push_str("\n\n查询说明/问题：\n");
        output.push_str(
            &value
                .health
                .issues
                .iter()
                .map(|issue| format!("- {}: {}", issue.segment, issue.message))
                .collect::<Vec<_>>()
                .join("\n"),
        );
    }
    output
}

fn optional_count(value: Option<u64>) -> String {
    value
        .map(|value| value.to_string())
        .unwrap_or_else(|| "未能查询".into())
}

fn localized_model(value: &str) -> String {
    match value {
        "portfolio_margin_pro" => "统一账户 Pro（Portfolio Margin Pro）",
        "portfolio_margin" => "统一账户（Portfolio Margin）",
        "contract_unified" => "统一合约账户",
        "unified" => "统一账户",
        "contract" => "合约账户",
        "margin" | "cross_margin" => "保证金账户",
        "no_margin" | "spot" => "现货账户",
        "classic_futures" => "经典合约账户",
        "funding_wallet" => "资金钱包",
        "multiple" => "多个分区模式（见下表）",
        other => other,
    }
    .into()
}

fn localized_status(value: &str) -> String {
    match value {
        "live" => "实盘",
        "standalone" => "独立查询",
        "direct_provider" => "接入直连",
        "local_registry" => "本地配置",
        "complete" => "完整",
        "partial" => "部分完整",
        "unavailable" => "不可用（接入未提供）",
        "unsupported" => "不支持",
        "not_applicable" => "不适用",
        "not_queried" => "尚未查询",
        "ready" => "正常",
        "configured" => "已配置",
        "fresh" => "新鲜",
        "local" => "本地",
        "read" => "只读",
        "trade" => "交易",
        "match" => "一致",
        "mismatch" => "不一致",
        "not_configured" => "未配置，无法比较",
        "not_observed" => "尚未观测",
        "cross" => "全仓",
        "isolated" => "逐仓",
        "one_way" => "单向持仓",
        "hedge" => "双向持仓",
        "query_by_symbol" => "按交易对查询（菜单 6）",
        "included_in_observed_rate" => "接口不单独返回（实际费率已含账户优惠）",
        other => other,
    }
    .into()
}

fn print_account_fees(value: AccountFeesResult) -> Result<(), serde_json::Error> {
    let output = match selected_output_format() {
        OutputFormat::Json => render(&value, OutputFormat::Json),
        OutputFormat::Text | OutputFormat::Table => render_account_fees(&value),
    };
    println!("{output}");
    Ok(())
}

fn render_account_fees(value: &AccountFeesResult) -> String {
    let rows = vec![
        vec!["产品".into(), value.product.clone()],
        vec![
            "交易对".into(),
            value.symbol.clone().unwrap_or_else(|| "—".into()),
        ],
        vec![
            "Maker 费率".into(),
            value
                .maker
                .map(|value| value.to_string())
                .unwrap_or_else(|| "不可用".into()),
        ],
        vec![
            "Taker 费率".into(),
            value
                .taker
                .map(|value| value.to_string())
                .unwrap_or_else(|| "不可用".into()),
        ],
        vec![
            "折扣资产".into(),
            value
                .discount
                .as_ref()
                .and_then(|discount| discount.asset.clone())
                .unwrap_or_else(|| "—".into()),
        ],
        vec![
            "折扣已启用".into(),
            value
                .discount
                .as_ref()
                .and_then(|discount| discount.enabled_for_account)
                .map(|enabled| enabled.to_string())
                .unwrap_or_else(|| "未返回".into()),
        ],
        vec![
            "VIP 等级".into(),
            value
                .vip_tier
                .clone()
                .unwrap_or_else(|| localized_status(&value.vip_tier_status)),
        ],
        vec![
            "数据完整度".into(),
            localized_status(&format!("{:?}", value.completeness).to_ascii_lowercase()),
        ],
    ];
    let mut output = format!(
        "账户 {} · {} · {}\n{}",
        value.account_id,
        localized_status(&value.mode),
        localized_status(&value.source),
        render_compact_table(&["费率字段", "值"], &rows),
    );
    if !value.issues.is_empty() {
        output.push_str("\n\n说明：\n");
        output.push_str(
            &value
                .issues
                .iter()
                .map(|issue| format!("- {issue}"))
                .collect::<Vec<_>>()
                .join("\n"),
        );
    }
    output
}

fn print_account_open_orders(value: AccountOpenOrdersResult) -> Result<(), serde_json::Error> {
    let output = match selected_output_format() {
        OutputFormat::Json => render(&value, OutputFormat::Json),
        OutputFormat::Text | OutputFormat::Table => render_account_open_orders(&value),
    };
    println!("{output}");
    Ok(())
}

fn render_account_open_orders(value: &AccountOpenOrdersResult) -> String {
    let heading = format!(
        "Account {} · {} · {} · completeness {:?} · segments {}/{}",
        value.account_id,
        value.mode,
        value.source.replace('_', " "),
        value.completeness,
        value.segments_succeeded,
        value.segments_requested,
    );
    let rows = value
        .orders
        .iter()
        .map(|order| {
            vec![
                order.segment.to_string(),
                order.symbol.clone(),
                order.side.clone(),
                order.order_type.clone(),
                order.status.clone(),
                order.quantity.to_string(),
                order.filled_quantity.to_string(),
                order
                    .average_fill_price
                    .map(|value| value.to_string())
                    .unwrap_or_else(|| "—".into()),
                order.order_id.clone(),
            ]
        })
        .collect::<Vec<_>>();
    let mut output = if rows.is_empty() && value.completeness == AccountQueryCompleteness::Complete
    {
        format!("{heading}\nNo open orders returned.")
    } else if rows.is_empty() {
        format!("{heading}\nOpen orders could not be established for the requested scope.")
    } else {
        format!(
            "{heading}\n{}",
            render_compact_table(
                &[
                    "SEGMENT",
                    "SYMBOL",
                    "SIDE",
                    "TYPE",
                    "STATUS",
                    "QUANTITY",
                    "FILLED",
                    "AVG_PRICE",
                    "ORDER_ID",
                ],
                &rows,
            )
        )
    };
    let issues = value
        .outcomes
        .iter()
        .filter(|outcome| outcome.outcome != AccountQueryCompleteness::Complete)
        .collect::<Vec<_>>();
    if !issues.is_empty() {
        output.push_str("\n\nQuery outcomes:\n");
        output.push_str(
            &issues
                .iter()
                .map(|outcome| {
                    format!(
                        "- {}: {:?}: {}",
                        outcome.segment,
                        outcome.outcome,
                        outcome.message.as_deref().unwrap_or("no details")
                    )
                })
                .collect::<Vec<_>>()
                .join("\n"),
        );
    }
    output
}

fn print_account_earn_holdings(value: AccountEarnHoldingsResult) -> Result<(), serde_json::Error> {
    let output = match selected_output_format() {
        OutputFormat::Json => render(&value, OutputFormat::Json),
        OutputFormat::Text | OutputFormat::Table => render_account_earn_holdings(&value),
    };
    println!("{output}");
    Ok(())
}

fn render_account_earn_holdings(value: &AccountEarnHoldingsResult) -> String {
    let heading = format!(
        "Account {} · {} · {} · completeness {:?}",
        value.account_id,
        value.mode,
        value.source.replace('_', " "),
        value.completeness,
    );
    let rows = value
        .holdings
        .iter()
        .map(|holding| {
            vec![
                holding.segment.to_string(),
                holding.family.clone(),
                holding.product_id.clone(),
                holding.asset.to_string(),
                holding.principal.to_string(),
                holding
                    .redeemable_amount
                    .map(|value| value.to_string())
                    .unwrap_or_else(|| "—".into()),
                holding
                    .matures_at_unix_nanos
                    .map(|value| value.to_string())
                    .unwrap_or_else(|| "—".into()),
                holding.state.clone(),
                holding
                    .accrued_rewards
                    .iter()
                    .map(|reward| format!("{}:{}", reward.asset, reward.amount))
                    .collect::<Vec<_>>()
                    .join(", "),
            ]
        })
        .collect::<Vec<_>>();
    let mut output = if rows.is_empty() && value.completeness == AccountQueryCompleteness::Complete
    {
        format!("{heading}\nNo Earn or staking holdings returned.")
    } else if rows.is_empty() {
        format!("{heading}\nEarn holdings could not be established for the requested scope.")
    } else {
        format!(
            "{heading}\n{}",
            render_compact_table(
                &[
                    "SEGMENT",
                    "FAMILY",
                    "PRODUCT",
                    "ASSET",
                    "PRINCIPAL",
                    "REDEEMABLE",
                    "MATURITY_NS",
                    "STATE",
                    "REWARDS",
                ],
                &rows,
            )
        )
    };
    if !value.errors.is_empty() {
        output.push_str("\n\nQuery errors:\n");
        output.push_str(
            &value
                .errors
                .iter()
                .map(|error| format!("- {}: {}", error.segment, error.message))
                .collect::<Vec<_>>()
                .join("\n"),
        );
    }
    output
}

fn print_account_balances(value: AccountBalancesResult) -> Result<(), serde_json::Error> {
    let output = match selected_output_format() {
        OutputFormat::Json => render(&value, OutputFormat::Json),
        OutputFormat::Text | OutputFormat::Table => render_account_balances(&value),
    };
    println!("{output}");
    Ok(())
}

fn print_account_positions(value: AccountPositionsResult) -> Result<(), serde_json::Error> {
    let output = match selected_output_format() {
        OutputFormat::Json => render(&value, OutputFormat::Json),
        OutputFormat::Text | OutputFormat::Table => render_account_positions(&value),
    };
    println!("{output}");
    Ok(())
}

fn render_account_positions(value: &AccountPositionsResult) -> String {
    let heading = format!(
        "Account {} · {} · {} · completeness {:?} · segments {}/{}",
        value.account_id,
        value.mode,
        value.source.replace('_', " "),
        value.completeness,
        value.segments_succeeded,
        value.segments_requested,
    );
    let rows = value
        .positions
        .iter()
        .map(|position| {
            vec![
                position.segment.to_string(),
                position.symbol.clone(),
                position.side.clone(),
                position.quantity.to_string(),
                position
                    .average_price
                    .map(|value| value.to_string())
                    .unwrap_or_else(|| "—".into()),
                position
                    .mark_price
                    .map(|value| value.to_string())
                    .unwrap_or_else(|| "—".into()),
                position
                    .unrealized_pnl
                    .map(|value| value.to_string())
                    .unwrap_or_else(|| "—".into()),
                position
                    .realized_pnl
                    .map(|value| value.to_string())
                    .unwrap_or_else(|| "—".into()),
                position.margin_mode.clone().unwrap_or_else(|| "—".into()),
                position.position_mode.clone().unwrap_or_else(|| "—".into()),
            ]
        })
        .collect::<Vec<_>>();
    let mut output = if rows.is_empty() && value.completeness == AccountQueryCompleteness::Complete
    {
        format!(
            "{heading}\nNo trading positions returned. Spot and funding assets are shown under balances."
        )
    } else if rows.is_empty() {
        format!("{heading}\nTrading positions could not be established for the requested scope.")
    } else {
        format!(
            "{heading}\n{}",
            render_compact_table(
                &[
                    "SEGMENT",
                    "SYMBOL",
                    "SIDE",
                    "QUANTITY",
                    "ENTRY",
                    "MARK",
                    "UPNL",
                    "RPNL",
                    "MARGIN",
                    "POSITION MODE"
                ],
                &rows,
            )
        )
    };
    if !value.errors.is_empty() {
        output.push_str("\n\nQuery errors:\n");
        output.push_str(
            &value
                .errors
                .iter()
                .map(|error| format!("- {}: {}", error.segment, error.message))
                .collect::<Vec<_>>()
                .join("\n"),
        );
    }
    output
}

fn render_account_balances(value: &AccountBalancesResult) -> String {
    if value.balances.is_empty()
        && value.collateral.is_empty()
        && value.completeness == AccountQueryCompleteness::Complete
    {
        return format!(
            "Account {} · {} · {} · completeness {:?} · segments {}/{}\nNo balances returned.",
            value.account_id,
            value.mode,
            value.source.replace('_', " "),
            value.completeness,
            value.segments_succeeded,
            value.segments_requested,
        );
    }

    let rows = value
        .balances
        .iter()
        .map(|balance| {
            let role = if value
                .collateral
                .iter()
                .any(|candidate| same_balance_value(balance, candidate))
            {
                "钱包/保证金".into()
            } else {
                localized_balance_role(&balance.role)
            };
            vec![
                balance.segment.to_string(),
                role,
                balance.asset.to_string(),
                balance.total.to_string(),
                optional_decimal_text(balance.available),
                optional_decimal_text(balance.locked),
                optional_decimal_text(balance.borrowed),
                optional_decimal_text(balance.interest),
            ]
        })
        .chain(
            value
                .collateral
                .iter()
                .filter(|candidate| {
                    !value
                        .balances
                        .iter()
                        .any(|balance| same_balance_value(balance, candidate))
                })
                .map(|balance| {
                    vec![
                        balance.segment.to_string(),
                        localized_balance_role(&balance.role),
                        balance.asset.to_string(),
                        balance.total.to_string(),
                        optional_decimal_text(balance.available),
                        optional_decimal_text(balance.locked),
                        optional_decimal_text(balance.borrowed),
                        optional_decimal_text(balance.interest),
                    ]
                }),
        )
        .collect::<Vec<_>>();
    let heading = format!(
        "账户 {} · {} · {} · 完整度 {} · 分区 {}/{}",
        value.account_id,
        localized_status(&value.mode),
        localized_status(&value.source),
        localized_status(&format!("{:?}", value.completeness).to_ascii_lowercase()),
        value.segments_succeeded,
        value.segments_requested,
    );
    let mut output = if rows.is_empty() {
        format!("{heading}\nBalances could not be established for the requested scope.")
    } else {
        format!(
            "{heading}\n{}",
            render_compact_table(
                &[
                    "分区", "类别", "资产", "总额", "可用", "锁定", "借入", "利息",
                ],
                &rows,
            )
        )
    };
    if !value.errors.is_empty() {
        output.push_str("\n\nQuery errors:\n");
        output.push_str(
            &value
                .errors
                .iter()
                .map(|error| format!("- {}: {}", error.segment, error.message))
                .collect::<Vec<_>>()
                .join("\n"),
        );
    }
    output
}

fn same_balance_value(left: &AccountBalanceItem, right: &AccountBalanceItem) -> bool {
    left.segment == right.segment
        && left.asset == right.asset
        && left.total == right.total
        && left.available == right.available
        && left.locked == right.locked
        && left.borrowed == right.borrowed
        && left.interest == right.interest
}

fn localized_balance_role(value: &str) -> String {
    match value {
        "wallet" => "钱包",
        "collateral" => "保证金",
        other => other,
    }
    .into()
}

fn optional_decimal_text(value: Option<kairos_primitives::decimal::DecimalParts>) -> String {
    value
        .map(|value| value.to_string())
        .unwrap_or_else(|| "—".into())
}

fn render_account_list(value: &AccountListResult) -> String {
    if value.accounts.is_empty() {
        return "No accounts configured.".to_owned();
    }

    let rows = value
        .accounts
        .iter()
        .map(|account| {
            vec![
                account_list_name(account),
                account.broker.to_string(),
                display_or_dash(&account.environment),
                if account.segments.is_empty() {
                    "—".to_owned()
                } else {
                    account
                        .segments
                        .iter()
                        .map(ToString::to_string)
                        .collect::<Vec<_>>()
                        .join(", ")
                },
                account.credential_id.clone().unwrap_or_else(|| "—".into()),
                display_or_dash(&account.status),
            ]
        })
        .collect::<Vec<_>>();
    render_compact_table(
        &[
            "ACCOUNT",
            "BROKER/CUSTODIAN",
            "ENVIRONMENT",
            "SEGMENTS",
            "CREDENTIAL",
            "STATUS",
        ],
        &rows,
    )
}

fn account_list_name(account: &AccountListItem) -> String {
    if account.alias.is_empty() || account.alias == account.account_id.as_str() {
        return account.account_id.to_string();
    }
    format!("{} ({})", account.alias, account.account_id)
}

fn display_or_dash(value: &str) -> String {
    if value.is_empty() {
        "—".to_owned()
    } else {
        value.to_owned()
    }
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
    print_connected_result(&command, value);
    Ok(())
}

fn print_connected_result(command: &ConnectedCommand, value: ConnectedAccountOutput) {
    let output = match (selected_output_format(), command) {
        (OutputFormat::Text | OutputFormat::Table, ConnectedCommand::Balances { .. }) => {
            match &value {
                ConnectedAccountOutput::Current(current) => render_connected_balances(current),
                _ => render(&value, selected_output_format()),
            }
        },
        (format, _) => render(&value, format),
    };
    println!("{output}");
}

fn render_connected_balances(value: &ConnectedAccountCurrentResult) -> String {
    let rows = value
        .segments
        .iter()
        .flat_map(|segment| {
            segment.balances.iter().map(move |balance| {
                vec![
                    segment.segment_key.clone(),
                    balance.asset_code.clone().unwrap_or_else(|| "—".to_owned()),
                    balance.total.clone(),
                    balance.available.clone().unwrap_or_else(|| "—".to_owned()),
                    balance.locked.clone().unwrap_or_else(|| "—".to_owned()),
                    segment.freshness.clone(),
                ]
            })
        })
        .collect::<Vec<_>>();
    if rows.is_empty() {
        return format!("No balances for account {}.", value.account_id);
    }
    render_compact_table(
        &[
            "SEGMENT",
            "ASSET",
            "TOTAL",
            "AVAILABLE",
            "RESERVED",
            "FRESHNESS",
        ],
        &rows,
    )
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
    let launch_id = args.launch_id.as_deref().ok_or(
        "Account connected mode is launch-scoped; use `kairos launch instance component account ...`",
    )?;
    let instance = workspace.instance(&args.launch_mode, launch_id, &args.instance_id)?;
    let socket_name =
        resolve_runtime_account_resource(&instance, account_id, args.socket_name.as_deref())?;
    let view_root = if require_views {
        Some(instance.snapshot(&[])?)
    } else {
        None
    };
    ConnectedAccountApplication::connect(instance.socket(&socket_name)?, view_root)
}

async fn run_runtime_control(
    application: &ConnectedAccountApplication,
    args: &Cli,
    command: &ConnectedCommand,
) -> Result<ConnectedAccountOutput, Box<dyn std::error::Error>> {
    let value = match command {
        ConnectedCommand::Fill { fill } => {
            if !is_paper_or_simulated(args.connection.provider.as_str()) {
                return Err("simulated fill is available only for paper/simulated Account".into());
            }
            ConnectedAccountOutput::Command(
                application
                    .apply_simulated_settlement(fill.to_contract()?)
                    .await?,
            )
        },
        ConnectedCommand::Refresh { .. } => {
            let request = account_segments_request(command)?;
            ConnectedAccountOutput::Refresh(application.refresh(request).await?)
        },
        ConnectedCommand::Reconcile { .. } => {
            let request = account_segments_request(command)?;
            ConnectedAccountOutput::Refresh(application.reconcile(request).await?)
        },
        _ => unreachable!("runtime control command already matched"),
    };
    Ok(value)
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
) -> Result<ConnectedAccountOutput, Box<dyn std::error::Error>> {
    if let ConnectedCommand::OpenOrders { symbol, limit } = command {
        return Ok(ConnectedAccountOutput::ObservedOrders(
            application.observed_orders(account_id, symbol.as_deref(), *limit)?,
        ));
    }
    let value = match command {
        ConnectedCommand::Snapshot { symbol } => {
            ConnectedAccountOutput::Current(application.snapshot(account_id, symbol.as_deref())?)
        },
        ConnectedCommand::Balances {
            segments,
            include_zero,
            page,
            page_size,
        } => ConnectedAccountOutput::Current(application.balances(
            account_id,
            segments,
            *include_zero,
            *page,
            *page_size,
        )?),
        ConnectedCommand::Positions { segments, symbol } => ConnectedAccountOutput::Current(
            application.positions(account_id, segments, symbol.as_deref())?,
        ),
        _ => unreachable!("runtime control command routed to mmap"),
    };
    Ok(value)
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
    use kairos_account::{
        AccountAdapterKind, AccountBalanceItem, AccountBalancesResult, AccountFeesResult,
        AccountListItem, AccountListResult, AccountOpenOrdersResult, AccountPositionItem,
        AccountPositionsResult, AccountQueryCompleteness, AccountQueryError,
    };
    use kairos_primitives::account::{AccountId, SegmentKey};
    use kairos_primitives::decimal::DecimalParts;
    use kairos_primitives::reference::Currency;

    use super::{
        Cli, Command, ConnectedCommand, StandaloneCommand, render_account_balances,
        render_account_fees, render_account_list, render_account_open_orders,
        render_account_positions,
    };

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
            "standalone",
            "positions",
            "--segment",
            "usd_m_futures",
            "--symbol",
            "BTCUSDT",
        ])
        .expect("standalone direct positions parses");
        assert!(matches!(
            parsed.command,
            Command::Standalone(StandaloneCommand::Positions { .. })
        ));

        let parsed = Cli::try_parse_from([
            "kairos-account-cli",
            "--workspace",
            "/tmp",
            "--account-id",
            "main",
            "standalone",
            "balances",
            "--output",
            "table",
        ])
        .expect("Python passthrough ordering parses");
        assert_eq!(
            parsed.output,
            Some(kairos_workspace::cli::OutputFormat::Table)
        );
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

    #[test]
    fn account_list_is_a_compact_human_summary() {
        let output = render_account_list(&AccountListResult {
            accounts: vec![
                AccountListItem {
                    account_id: AccountId::new("manual-live-readonly").unwrap(),
                    alias: "manual-live-readonly".into(),
                    broker: kairos_primitives::account::BrokerId::new("binance").unwrap(),
                    exchange: Some("binance".into()),
                    integration_adapter: AccountAdapterKind::new("binance").unwrap(),
                    environment: "live".into(),
                    segments: ["funding", "spot", "usd_m_futures"]
                        .map(|value| SegmentKey::new(value).unwrap())
                        .into(),
                    products: vec!["funding".into(), "spot".into(), "usd_m_futures".into()],
                    account_model: Some("portfolio_margin".into()),
                    credential_id: Some("binance-equity-readonly".into()),
                    configured_credential_role: "readonly".into(),
                    capabilities: vec!["read".into()],
                    status: "configured".into(),
                },
                AccountListItem {
                    account_id: AccountId::new("paper-account").unwrap(),
                    alias: "paper-account".into(),
                    broker: kairos_primitives::account::BrokerId::new("paper").unwrap(),
                    exchange: Some("paper".into()),
                    integration_adapter: AccountAdapterKind::new("paper").unwrap(),
                    environment: "paper".into(),
                    segments: vec![SegmentKey::new("spot").unwrap()],
                    products: vec!["paper".into()],
                    account_model: Some("no_margin".into()),
                    credential_id: None,
                    configured_credential_role: "readonly".into(),
                    capabilities: vec!["read".into()],
                    status: "configured".into(),
                },
            ],
            count: 2,
        });

        assert!(output.contains("ACCOUNT"));
        assert!(output.contains("BROKER/CUSTODIAN"));
        assert!(output.contains("manual-live-readonly"));
        assert!(output.contains("funding, spot, usd_m_futures"));
        assert!(output.contains("paper-account"));
        assert!(!output.contains("[0]."));
        assert_eq!(output.lines().count(), 4);
    }

    #[test]
    fn account_balances_are_rendered_as_business_columns() {
        let output = render_account_balances(&AccountBalancesResult {
            account_id: AccountId::new("paper-account").unwrap(),
            source: "local_registry".into(),
            mode: "standalone".into(),
            kind: "balances".into(),
            segments_requested: 1,
            segments_succeeded: 1,
            completeness: AccountQueryCompleteness::Complete,
            observed_at_unix_nanos: Some(1),
            balances: vec![AccountBalanceItem {
                segment: SegmentKey::new("spot").unwrap(),
                role: "wallet".into(),
                asset: Currency::new("USDT").unwrap(),
                total: "10000".parse::<DecimalParts>().unwrap(),
                available: Some("10000".parse::<DecimalParts>().unwrap()),
                locked: Some(DecimalParts::default()),
                borrowed: None,
                interest: None,
            }],
            collateral: Vec::new(),
            outcomes: Vec::new(),
            errors: Vec::new(),
        });

        assert!(output.contains("分区"));
        assert!(output.contains("资产"));
        assert!(output.contains("spot"));
        assert!(output.contains("USDT"));
        assert!(!output.contains("balances"));
        assert!(output.contains("独立查询 · 本地配置 · 完整度 完整 · 分区 1/1"));
        assert_eq!(output.lines().count(), 4);
    }

    #[test]
    fn partial_assets_keep_successful_rows_and_show_segment_errors() {
        let output = render_account_balances(&AccountBalancesResult {
            account_id: AccountId::new("live-main").unwrap(),
            source: "direct_provider".into(),
            mode: "standalone".into(),
            kind: "assets".into(),
            segments_requested: 2,
            segments_succeeded: 1,
            completeness: AccountQueryCompleteness::Partial,
            observed_at_unix_nanos: Some(1),
            balances: vec![AccountBalanceItem {
                segment: SegmentKey::new("spot").unwrap(),
                role: "wallet".into(),
                asset: Currency::new("USDT").unwrap(),
                total: "10".parse().unwrap(),
                available: Some("10".parse().unwrap()),
                locked: None,
                borrowed: None,
                interest: None,
            }],
            collateral: Vec::new(),
            outcomes: Vec::new(),
            errors: vec![AccountQueryError {
                segment: SegmentKey::new("funding").unwrap(),
                message: "unauthorized".into(),
            }],
        });

        assert!(output.contains("USDT"));
        assert!(output.contains("完整度 部分完整 · 分区 1/2"));
        assert!(output.contains("funding: unauthorized"));
    }

    #[test]
    fn assets_merge_identical_wallet_and_collateral_rows() {
        let balance = AccountBalanceItem {
            segment: SegmentKey::new("usd_m_futures").unwrap(),
            role: "wallet".into(),
            asset: Currency::new("USDT").unwrap(),
            total: "100".parse().unwrap(),
            available: Some("80".parse().unwrap()),
            locked: None,
            borrowed: None,
            interest: None,
        };
        let mut collateral = balance.clone();
        collateral.role = "collateral".into();
        let output = render_account_balances(&AccountBalancesResult {
            account_id: AccountId::new("live-main").unwrap(),
            source: "direct_provider".into(),
            mode: "standalone".into(),
            kind: "assets".into(),
            segments_requested: 1,
            segments_succeeded: 1,
            completeness: AccountQueryCompleteness::Complete,
            observed_at_unix_nanos: Some(1),
            balances: vec![balance],
            collateral: vec![collateral],
            outcomes: Vec::new(),
            errors: Vec::new(),
        });

        assert_eq!(output.matches("USDT").count(), 1);
        assert!(output.contains("钱包/保证金"));
    }

    #[test]
    fn account_positions_are_rendered_as_business_columns() {
        let output = render_account_positions(&AccountPositionsResult {
            account_id: AccountId::new("live-main").expect("account id"),
            source: "direct_provider".into(),
            mode: "standalone".into(),
            kind: "positions".into(),
            segments_requested: 1,
            segments_succeeded: 1,
            completeness: AccountQueryCompleteness::Complete,
            observed_at_unix_nanos: Some(1),
            positions: vec![AccountPositionItem {
                segment: SegmentKey::new("usd_m_futures").expect("segment"),
                symbol: "BTCUSDT".into(),
                instrument_type: Some("perpetual".into()),
                side: "net".into(),
                quantity: "0.1".parse().expect("quantity"),
                average_price: Some("60000".parse().expect("entry")),
                mark_price: Some("61000".parse().expect("mark")),
                unrealized_pnl: Some("100".parse().expect("upnl")),
                realized_pnl: Some("20".parse().expect("rpnl")),
                margin_mode: Some("cross".into()),
                position_mode: Some("one_way".into()),
            }],
            outcomes: Vec::new(),
            errors: Vec::new(),
        });

        assert!(output.contains("BTCUSDT"));
        assert!(output.contains("QUANTITY"));
        assert!(output.contains("UPNL"));
        assert!(!output.contains("participant_instrument"));
    }

    #[test]
    fn incomplete_open_orders_never_claim_an_empty_order_set() {
        let output = render_account_open_orders(&AccountOpenOrdersResult {
            account_id: AccountId::new("live-main").unwrap(),
            source: "direct_provider".into(),
            mode: "standalone".into(),
            kind: "open_orders".into(),
            completeness: AccountQueryCompleteness::Unavailable,
            segments_requested: 1,
            segments_succeeded: 0,
            observed_at_unix_nanos: Some(1),
            orders: Vec::new(),
            outcomes: Vec::new(),
        });
        assert!(output.contains("could not be established"));
        assert!(!output.contains("No open orders returned"));
    }

    #[test]
    fn fee_output_keeps_unavailable_vip_separate_from_observed_rates() {
        let output = render_account_fees(&AccountFeesResult {
            account_id: AccountId::new("live-main").unwrap(),
            source: "direct_provider".into(),
            mode: "standalone".into(),
            kind: "fees".into(),
            product: "spot".into(),
            symbol: Some("BTCUSDT".into()),
            completeness: AccountQueryCompleteness::Complete,
            maker: Some("0.001".parse().unwrap()),
            taker: Some("0.001".parse().unwrap()),
            buyer: None,
            seller: None,
            standard: None,
            special: None,
            tax: None,
            discount: None,
            rpi: None,
            vip_tier: None,
            vip_tier_status: "unavailable".into(),
            observed_at_unix_nanos: Some(1),
            issues: Vec::new(),
        });
        assert!(output.contains("0.001"));
        assert!(output.contains("不可用（接入未提供）"));
    }
}
