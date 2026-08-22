use std::path::PathBuf;
use std::str::FromStr;

use clap::{Args, Parser, Subcommand};
use kairos_execution::ConnectedExecutionApplication;
use kairos_execution::application::{
    CancelOrder, CliExecutionApplication, ExecutionOrderOptions, OrderSide, OrderType,
    ReplaceOrder, SubmitOrder,
};
use kairos_execution_contract::{
    CancelOrderRequest, CommandEnvelope, CompletionPolicy, ExecutionIntentRequest,
    ExecutionOrderOptionsRequest, ExecutionRoutesQuery, FailurePolicy, IntentLegRequest,
    IntentType, ReconcileExecutionRequest, ReplaceOrderRequest, SubmitIntentRequest,
};
use kairos_primitives::account::{AccountId, SegmentKey};
use kairos_primitives::execution::{ExecutionRouteId, IntentId, OrderId};
use kairos_primitives::reference::{InstrumentId, MarketId};
use kairos_primitives::runtime::{InstanceId, InstanceIdentity, LaunchId, StrategyId};
use kairos_workspace::cli::{OutputFormat, render};
use kairos_workspace::workspace::Workspace;

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
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
    match args.command {
        Command::Standalone(command) => {
            let application = CliExecutionApplication::open(&workspace);
            print_json(execute_standalone_command(&application, command)?);
        },
        Command::Connected(command) => {
            let instance = workspace.instance(&args.mode, &args.launch_id, &args.instance_id)?;
            if command.is_mmap_query() {
                let application = connected_execution_app(&instance, workspace.id(), true)?;
                print_json(execute_connected_query(&application, command)?);
                return Ok(());
            }
            let application = connected_execution_app(&instance, workspace.id(), false)?;
            print_json(execute_control_command(&application, command).await?);
        },
    }
    Ok(())
}

#[derive(Debug, Parser)]
#[command(name = "kairos-execution-cli", about = "One-shot execution commands")]
struct Cli {
    #[arg(long)]
    workspace: String,
    #[arg(long, global = true, default_value = "paper")]
    mode: String,
    #[arg(long, global = true, default_value = "default")]
    launch_id: String,
    #[arg(long, global = true, default_value = "default")]
    instance_id: String,
    #[arg(long, global = true, value_parser = OutputFormat::from_str)]
    output: Option<OutputFormat>,
    #[command(subcommand)]
    command: Command,
}

impl ConnectedCommand {
    fn is_mmap_query(&self) -> bool {
        matches!(
            self,
            Self::Snapshot
                | Self::Orders { .. }
                | Self::OpenOrders { .. }
                | Self::History { .. }
                | Self::UnknownRemoteOrders
                | Self::Status { .. }
                | Self::Inspect { .. }
                | Self::Events { .. }
                | Self::Trace { .. }
                | Self::Audit { .. }
                | Self::Journal { .. }
                | Self::Fills { .. }
        )
    }
}

fn connected_execution_app(
    instance: &kairos_workspace::workspace::InstanceWorkspace,
    workspace_id: &str,
    require_views: bool,
) -> Result<ConnectedExecutionApplication, Box<dyn std::error::Error>> {
    let identity =
        InstanceIdentity::new(workspace_id, instance.launch_id(), instance.instance_id())?;
    let socket = instance.socket("execution")?;
    if require_views {
        return ConnectedExecutionApplication::connect(
            socket,
            instance.snapshot(&[])?,
            identity,
            workspace_id.to_string(),
            instance.launch_id().to_string(),
            instance.instance_id().to_string(),
        );
    }
    ConnectedExecutionApplication::connect_control(
        socket,
        identity,
        workspace_id.to_string(),
        instance.launch_id().to_string(),
        instance.instance_id().to_string(),
    )
}

fn execute_connected_query(
    application: &ConnectedExecutionApplication,
    command: ConnectedCommand,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    match command {
        ConnectedCommand::Snapshot => application.snapshot(),
        ConnectedCommand::Orders { account_id } => application.orders(account_id.as_deref()),
        ConnectedCommand::OpenOrders { account_id } => {
            application.open_orders(account_id.as_deref())
        },
        ConnectedCommand::History { account_id } => application.history(account_id.as_deref()),
        ConnectedCommand::UnknownRemoteOrders => application.unknown_remote_orders(),
        ConnectedCommand::Status { order_id } | ConnectedCommand::Inspect { order_id } => {
            application.order_status(&order_id)
        },
        ConnectedCommand::Events { order_id } => application.events(order_id.as_deref()),
        ConnectedCommand::Trace { order_id } | ConnectedCommand::Journal { order_id } => {
            application.trace(&order_id)
        },
        ConnectedCommand::Audit {
            order_id,
            remote_order_id,
            status,
            limit,
        } => application.audit(
            order_id.as_deref(),
            remote_order_id.as_deref(),
            status.as_deref(),
            limit,
        ),
        ConnectedCommand::Fills { order_id } => application.fills(order_id.as_deref()),
        _ => unreachable!("control command routed to projection query"),
    }
}

fn execute_standalone_command(
    application: &CliExecutionApplication,
    command: StandaloneCommand,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    let value = match command {
        StandaloneCommand::Inspect(args) => application
            .inspect_file(&args.file, &args.order_id)
            .map_err(invalid_standalone_input)?,
        StandaloneCommand::Journal(args) => application
            .journal_file(&args.file, &args.order_id)
            .map_err(invalid_standalone_input)?,
        StandaloneCommand::Audit(args) => application
            .audit_file(
                &args.file,
                args.order_id.as_deref(),
                args.remote_order_id.as_deref(),
                args.status.as_deref(),
                args.limit,
            )
            .map_err(invalid_standalone_input)?,
        StandaloneCommand::Fills(args) => application
            .fills_file(&args.file, args.order_id.as_deref())
            .map_err(invalid_standalone_input)?,
        StandaloneCommand::PreviewSubmit(args) => application
            .preview_submit(submit_request(args)?)
            .map_err(invalid_standalone_input)?,
        StandaloneCommand::PreviewSubmitFile(args) => application
            .preview_submit_file(&args.file)
            .map_err(invalid_standalone_input)?,
        StandaloneCommand::PreviewCancel(args) => application
            .preview_cancel(CancelOrder {
                order_id: OrderId::new(args.order_id.clone())?,
                reason: args.reason.clone(),
            })
            .map_err(invalid_standalone_input)?,
        StandaloneCommand::PreviewCancelFile(args) => application
            .preview_cancel_file(&args.file)
            .map_err(invalid_standalone_input)?,
        StandaloneCommand::PreviewReplace {
            target_order_id,
            replacement,
        } => application
            .preview_replace(ReplaceOrder {
                order_id: OrderId::new(target_order_id.clone())?,
                replacement: submit_request(replacement)?,
            })
            .map_err(invalid_standalone_input)?,
        StandaloneCommand::PreviewReplaceFile(args) => application
            .preview_replace_file(&args.file)
            .map_err(invalid_standalone_input)?,
    };
    Ok(value)
}

fn invalid_standalone_input(error: String) -> std::io::Error {
    std::io::Error::new(std::io::ErrorKind::InvalidInput, error)
}

async fn execute_control_command(
    application: &ConnectedExecutionApplication,
    command: ConnectedCommand,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    match command {
        ConnectedCommand::Routes {
            account_id,
            segment_key,
            instrument_id,
            market_id,
        } => {
            application
                .routes(ExecutionRoutesQuery {
                    account_id: account_id.map(AccountId::new).transpose()?,
                    segment_key: segment_key.map(SegmentKey::new).transpose()?,
                    instrument_id: instrument_id.map(InstrumentId::new).transpose()?,
                    market_id: market_id.map(MarketId::new).transpose()?,
                    participant_id: None,
                })
                .await
        },
        ConnectedCommand::Reconcile { order_id } => {
            application
                .reconcile(ReconcileExecutionRequest {
                    order_id: order_id.map(OrderId::new).transpose()?,
                    ..ReconcileExecutionRequest::default()
                })
                .await
        },
        ConnectedCommand::Submit(args) => {
            application
                .submit_intent(submit_intent_request(submit_request(args)?)?)
                .await
        },
        ConnectedCommand::Cancel { order_id, reason } => {
            application
                .cancel_order(
                    OrderId::new(order_id)?,
                    CancelOrderRequest {
                        reason: Some(reason),
                    },
                )
                .await
        },
        ConnectedCommand::Replace {
            order_id,
            replacement,
        } => {
            let replacement = submit_request(replacement)?;
            application
                .replace_order(
                    OrderId::new(order_id)?,
                    ReplaceOrderRequest {
                        quantity: Some(replacement.quantity),
                        limit_price: replacement.limit_price,
                        options: Some(control_options(replacement.options)),
                        reason: None,
                    },
                )
                .await
        },
        _ => unreachable!("query command routed to typed mmap"),
    }
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
    Inspect(LocalOrderEvidenceArgs),
    Journal(LocalOrderEvidenceArgs),
    Audit(LocalAuditArgs),
    Fills(LocalFillsArgs),
    #[command(name = "preview-submit")]
    PreviewSubmit(SubmitArgs),
    #[command(name = "preview-submit-file")]
    PreviewSubmitFile(FilePreviewArgs),
    #[command(name = "preview-cancel")]
    PreviewCancel(CancelPreviewArgs),
    #[command(name = "preview-cancel-file")]
    PreviewCancelFile(FilePreviewArgs),
    #[command(name = "preview-replace")]
    PreviewReplace {
        #[arg(long = "target-order-id")]
        target_order_id: String,
        #[command(flatten)]
        replacement: SubmitArgs,
    },
    #[command(name = "preview-replace-file")]
    PreviewReplaceFile(FilePreviewArgs),
}

#[derive(Clone, Debug, Subcommand)]
enum ConnectedCommand {
    Snapshot,
    /// List current Execution-owned order submission route candidates.
    Routes {
        #[arg(long)]
        account_id: Option<String>,
        #[arg(long)]
        segment_key: Option<String>,
        #[arg(long)]
        instrument_id: Option<String>,
        #[arg(long)]
        market_id: Option<String>,
    },
    #[command(alias = "list")]
    Orders {
        #[arg(long)]
        account_id: Option<String>,
    },
    #[command(alias = "open")]
    OpenOrders {
        #[arg(long)]
        account_id: Option<String>,
    },
    #[command(alias = "closed")]
    History {
        #[arg(long)]
        account_id: Option<String>,
    },
    #[command(alias = "reconcile-remote")]
    Reconcile {
        #[arg(long)]
        order_id: Option<String>,
    },
    UnknownRemoteOrders,
    #[command(alias = "show")]
    Status {
        #[arg(long)]
        order_id: String,
    },
    Inspect {
        #[arg(long)]
        order_id: String,
    },
    Events {
        #[arg(long)]
        order_id: Option<String>,
    },
    Trace {
        #[arg(long)]
        order_id: String,
    },
    Audit {
        #[arg(long)]
        order_id: Option<String>,
        #[arg(long)]
        remote_order_id: Option<String>,
        #[arg(long)]
        status: Option<String>,
        #[arg(long)]
        limit: Option<u32>,
    },
    Journal {
        #[arg(long)]
        order_id: String,
    },
    Fills {
        #[arg(long)]
        order_id: Option<String>,
    },
    #[command(alias = "place")]
    Submit(SubmitArgs),
    Cancel {
        #[arg(long)]
        order_id: String,
        #[arg(long, default_value = "cli cancel")]
        reason: String,
    },
    Replace {
        #[arg(long)]
        order_id: String,
        #[command(flatten)]
        replacement: SubmitArgs,
    },
}

#[derive(Clone, Debug, Args)]
struct SubmitArgs {
    #[arg(long)]
    order_id: String,
    #[arg(long)]
    account_id: String,
    #[arg(long, default_value = "spot")]
    segment_key: String,
    #[arg(long)]
    instrument_id: String,
    #[arg(long)]
    quantity: String,
    #[arg(long, default_value = "buy")]
    side: String,
    #[arg(long, default_value = "market")]
    order_type: String,
    #[arg(long)]
    limit_price: Option<String>,
    #[arg(long)]
    intent_id: Option<String>,
    #[arg(long)]
    market_id: Option<String>,
    #[arg(long)]
    execution_route_id: String,
    #[arg(long)]
    time_in_force: Option<String>,
    #[arg(long)]
    reduce_only: Option<bool>,
    #[arg(long)]
    post_only: Option<bool>,
    #[arg(long)]
    position_side: Option<String>,
    #[arg(long)]
    quote_asset: Option<String>,
    #[arg(long)]
    wallet_type: Option<String>,
    #[arg(long)]
    trading_session: Option<String>,
    #[arg(long)]
    tokenize: Option<bool>,
}

#[derive(Clone, Debug, Args)]
struct CancelPreviewArgs {
    #[arg(long)]
    order_id: String,
    #[arg(long, default_value = "cli cancel")]
    reason: String,
}

#[derive(Clone, Debug, Args)]
struct LocalOrderEvidenceArgs {
    #[arg(long)]
    file: PathBuf,
    #[arg(long)]
    order_id: String,
}

#[derive(Clone, Debug, Args)]
struct FilePreviewArgs {
    #[arg(long)]
    file: PathBuf,
}

#[derive(Clone, Debug, Args)]
struct LocalAuditArgs {
    #[arg(long)]
    file: PathBuf,
    #[arg(long)]
    order_id: Option<String>,
    #[arg(long)]
    remote_order_id: Option<String>,
    #[arg(long)]
    status: Option<String>,
    #[arg(long)]
    limit: Option<usize>,
}

#[derive(Clone, Debug, Args)]
struct LocalFillsArgs {
    #[arg(long)]
    file: PathBuf,
    #[arg(long)]
    order_id: Option<String>,
}

fn submit_request(args: SubmitArgs) -> Result<SubmitOrder, Box<dyn std::error::Error>> {
    Ok(SubmitOrder {
        order_id: OrderId::new(args.order_id)?,
        intent_id: args.intent_id.map(IntentId::new).transpose()?,
        strategy_id: None,
        account_id: AccountId::new(args.account_id)?,
        segment_key: SegmentKey::new(args.segment_key)?,
        instrument_id: InstrumentId::new(args.instrument_id)?,
        market_id: args.market_id.map(MarketId::new).transpose()?,
        execution_route_id: Some(ExecutionRouteId::new(args.execution_route_id)?),
        side: parse_side(&args.side)?,
        order_type: parse_order_type(&args.order_type)?,
        quantity: args.quantity.parse()?,
        limit_price: args.limit_price.as_deref().map(str::parse).transpose()?,
        options: ExecutionOrderOptions {
            time_in_force: args.time_in_force,
            reduce_only: args.reduce_only,
            post_only: args.post_only,
            position_side: args.position_side,
            quote_asset: args.quote_asset,
            wallet_type: args.wallet_type,
            trading_session: args.trading_session,
            tokenize: args.tokenize,
            ..ExecutionOrderOptions::default()
        },
        submitted_at_unix_nanos: None,
    })
}

fn submit_intent_request(
    request: SubmitOrder,
) -> Result<SubmitIntentRequest, Box<dyn std::error::Error>> {
    let intent_id = request
        .intent_id
        .clone()
        .unwrap_or_else(|| IntentId::new(format!("intent:{}", request.order_id)).unwrap());
    let strategy_id = request
        .strategy_id
        .clone()
        .unwrap_or_else(|| StrategyId::new("cli").unwrap());
    let leg = IntentLegRequest {
        leg_id: kairos_primitives::execution::LegId::new(format!("leg:{}", request.order_id))?,
        account_id: request.account_id.clone(),
        segment_key: request.segment_key.clone(),
        instrument_id: request.instrument_id.clone(),
        market_id: request.market_id.clone(),
        execution_route_id: request.execution_route_id.clone(),
        side: request.side,
        quantity: request.quantity,
        limit_price: request.limit_price,
        target_position: false,
        options: control_options(request.options.clone()),
    };
    Ok(SubmitIntentRequest {
        envelope: CommandEnvelope {
            command_id: Some(kairos_primitives::runtime::RequestId::new(format!(
                "cli:{}",
                request.order_id
            ))?),
            idempotency_key: Some(kairos_primitives::runtime::IdempotencyKey::new(format!(
                "cli:{}",
                request.order_id
            ))?),
            caller_id: Some(kairos_primitives::runtime::ActorId::new("cli")?),
            workspace_id: None,
        },
        intent: ExecutionIntentRequest {
            intent_id,
            strategy_decision_id: None,
            strategy_id,
            launch_id: LaunchId::new("cli")?,
            instance_id: InstanceId::new("cli")?,
            instrument_id: request.instrument_id,
            market_id: request.market_id,
            execution_route_id: request.execution_route_id,
            account_ids: vec![request.account_id],
            segment_key: request.segment_key,
            target_quantity: request.quantity,
            limit_price: request.limit_price,
            source_snapshot_id: None,
            source_event_sequence: None,
            source_event_time_unix_nanos: request.submitted_at_unix_nanos,
            reason: "cli submit".into(),
            intent_type: IntentType::SingleOrder,
            completion_policy: CompletionPolicy::AllLegsSatisfied,
            failure_policy: FailurePolicy::CancelRemaining,
            legs: vec![leg],
            deadline_unix_nanos: None,
            min_edge_bps: None,
            max_slippage_bps: None,
            estimated_fee_bps: None,
            minimum_net_credit: None,
            maximum_loss: None,
            hedge_policy: None,
            order_options: control_options(request.options),
        },
        admission_evidence: None,
    })
}

fn control_options(options: ExecutionOrderOptions) -> ExecutionOrderOptionsRequest {
    ExecutionOrderOptionsRequest {
        time_in_force: options.time_in_force,
        reduce_only: options.reduce_only,
        post_only: options.post_only,
        position_side: options.position_side,
        quote_asset: options.quote_asset,
        wallet_type: options.wallet_type,
        trading_session: options.trading_session,
        tokenize: options.tokenize,
        split: None,
        maker: None,
    }
}

fn parse_side(value: &str) -> Result<OrderSide, Box<dyn std::error::Error>> {
    match value.to_ascii_lowercase().as_str() {
        "buy" => Ok(OrderSide::Buy),
        "sell" => Ok(OrderSide::Sell),
        _ => Err(format!("unsupported order side: {value}").into()),
    }
}

fn parse_order_type(value: &str) -> Result<OrderType, Box<dyn std::error::Error>> {
    match value.to_ascii_lowercase().as_str() {
        "market" => Ok(OrderType::Market),
        "limit" => Ok(OrderType::Limit),
        _ => Err(format!("unsupported order type: {value}").into()),
    }
}

fn print_json(value: serde_json::Value) {
    let format = std::env::var("KAIROS_CLI_FORMAT")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(OutputFormat::Json);
    println!("{}", render(&value, format));
}

#[cfg(test)]
mod cli_tests {
    use clap::Parser;

    use super::Cli;

    #[test]
    fn command_surface_requires_explicit_mode_and_exposes_connected_runtime() {
        let parsed =
            Cli::try_parse_from(["kairos-execution-cli", "--workspace", "/tmp", "snapshot"]);
        assert!(
            parsed.is_err(),
            "execution Rust CLI must force explicit standalone/connected mode"
        );

        let parsed = Cli::try_parse_from([
            "kairos-execution-cli",
            "--workspace",
            "/tmp",
            "standalone",
            "backtest",
        ]);
        assert!(
            parsed.is_err(),
            "execution standalone mode must not expose legacy backtest"
        );

        let parsed = Cli::try_parse_from([
            "kairos-execution-cli",
            "--workspace",
            "/tmp",
            "standalone",
            "audit",
            "--file",
            "/tmp/execution-evidence.json",
            "--order-id",
            "order-1",
        ]);
        assert!(
            parsed.is_ok(),
            "execution standalone evidence query must parse: {parsed:?}"
        );

        let parsed = Cli::try_parse_from([
            "kairos-execution-cli",
            "--workspace",
            "/tmp",
            "connected",
            "snapshot",
        ]);
        assert!(
            parsed.is_ok(),
            "execution connected command surface must parse: {parsed:?}"
        );

        let parsed = Cli::try_parse_from([
            "kairos-execution-cli",
            "--workspace",
            "/tmp",
            "connected",
            "fill",
            "--fill-id",
            "fill-1",
            "--order-id",
            "order-1",
            "--quantity",
            "1",
            "--price",
            "100",
        ]);
        assert!(
            parsed.is_err(),
            "execution connected mode must not expose an unimplemented fill command"
        );

        let parsed = Cli::try_parse_from([
            "kairos-execution-cli",
            "--workspace",
            "/tmp",
            "connected",
            "link-unknown",
            "--remote-order-id",
            "remote-1",
            "--local-order-id",
            "order-1",
        ]);
        assert!(
            parsed.is_err(),
            "execution connected mode must not expose link-unknown before the RPC exists"
        );

        let parsed = Cli::try_parse_from([
            "kairos-execution-cli",
            "--workspace",
            "/tmp",
            "connected",
            "routes",
            "--order-type",
            "limit",
        ]);
        assert!(
            parsed.is_err(),
            "execution routes must not parse filters that are not in ExecutionControlRpc"
        );

        let parsed = Cli::try_parse_from([
            "kairos-execution-cli",
            "--workspace",
            "/tmp",
            "connected",
            "submit",
            "--order-id",
            "order-1",
            "--account-id",
            "account-1",
            "--instrument-id",
            "BTC-USDT",
            "--quantity",
            "1",
            "--execution-route-id",
            "route-1",
            "--dry-run",
        ]);
        assert!(
            parsed.is_err(),
            "execution connected submit must not expose dry-run before a real API exists"
        );
    }
}
