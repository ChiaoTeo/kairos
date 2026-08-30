use std::str::FromStr;

use clap::{Args, Parser, Subcommand};
use kairos_execution::application::{
    CliExecutionApplication, CliExecutionOutput, ExecutionOrderOptions, OrderSide, OrderType,
    StandaloneExecutionBinding, SubmitOrder,
};
use kairos_execution::composition::compose_standalone_execution;
use kairos_execution::{ConnectedExecutionApplication, ConnectedExecutionOutput};
use kairos_execution_contract::{
    CancelOrderRequest, CommandEnvelope, CompletionPolicy, ExecutionAlgorithmPolicyRequest,
    ExecutionIntentRequest, ExecutionOrderAuditQuery, ExecutionOrderLifecycle,
    ExecutionOrderOptionsRequest, ExecutionRoutesQuery, FailurePolicy, IntentLegRequest,
    IntentType, ReconcileExecutionRequest, ReplaceOrderRequest, SubmitIntentRequest,
};
use kairos_primitives::account::{AccountId, SegmentKey};
use kairos_primitives::execution::{ExecutionRouteId, IntentId, OrderId};
use kairos_primitives::reference::{InstrumentId, MarketId};
use kairos_primitives::runtime::{InstanceId, InstanceIdentity, LaunchId, StrategyId};
use kairos_workspace::cli::{OutputFormat, render};
use kairos_workspace::workspace::Workspace;
use serde::Serialize;

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
        Command::Standalone(args) => {
            let binding: StandaloneExecutionBinding = serde_json::from_str(&args.binding_json)?;
            if binding.environment.eq_ignore_ascii_case("live")
                && args.command.is_write()
                && !args.confirm_live
            {
                return Err("live standalone order actions require explicit confirmation".into());
            }
            let mut application = compose_standalone_execution(&workspace, binding.clone())?;
            print_json(execute_standalone_command(&mut application, &binding, args.command).await?);
        },
        Command::Connected(connected) => {
            let instance = workspace.instance(
                &connected.mode,
                &connected.launch_id,
                &connected.instance_id,
            )?;
            if connected.command.is_current_view_query() {
                let application = connected_execution_app(&instance, workspace.id(), true)?;
                print_json(connected_result(
                    execute_connected_query(&application, connected.command)?,
                    &connected.mode,
                    &connected.launch_id,
                    &connected.instance_id,
                ));
                return Ok(());
            }
            let application = connected_execution_app(&instance, workspace.id(), false)?;
            print_json(connected_result(
                execute_control_command(
                    &application,
                    connected.command,
                    &connected.launch_id,
                    &connected.instance_id,
                )
                .await?,
                &connected.mode,
                &connected.launch_id,
                &connected.instance_id,
            ));
        },
    }
    Ok(())
}

#[derive(Debug, Serialize)]
struct ConnectedExecutionCliOutput<'a> {
    owner: &'static str,
    mode: &'a str,
    launch_id: &'a str,
    instance_id: &'a str,
    scope: &'static str,
    #[serde(flatten)]
    result: ConnectedExecutionOutput,
}

fn connected_result<'a>(
    value: ConnectedExecutionOutput,
    mode: &'a str,
    launch_id: &'a str,
    instance_id: &'a str,
) -> ConnectedExecutionCliOutput<'a> {
    ConnectedExecutionCliOutput {
        owner: "execution",
        mode,
        launch_id,
        instance_id,
        scope: "launch-instance",
        result: value,
    }
}

#[derive(Debug, Parser)]
#[command(name = "kairos-execution-cli", about = "One-shot execution commands")]
struct Cli {
    #[arg(long)]
    workspace: String,
    #[arg(long, global = true, value_parser = OutputFormat::from_str)]
    output: Option<OutputFormat>,
    #[command(subcommand)]
    command: Command,
}

impl ConnectedCommand {
    fn is_current_view_query(&self) -> bool {
        matches!(
            self,
            Self::ActiveOrders { .. } | Self::UnknownRemoteOrders | Self::ActiveOrder { .. }
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
        return kairos_execution::composition::connect_execution_application(
            socket,
            Some(instance.snapshot(&[])?),
            identity,
        );
    }
    kairos_execution::composition::connect_execution_application(socket, None, identity)
}

fn execute_connected_query(
    application: &ConnectedExecutionApplication,
    command: ConnectedCommand,
) -> Result<ConnectedExecutionOutput, Box<dyn std::error::Error>> {
    let value = match command {
        ConnectedCommand::ActiveOrders { account_id } => {
            ConnectedExecutionOutput::Orders(application.active_orders(account_id.as_deref())?)
        },
        ConnectedCommand::UnknownRemoteOrders => {
            ConnectedExecutionOutput::UnknownRemoteOrders(application.unknown_remote_orders()?)
        },
        ConnectedCommand::ActiveOrder { order_id } => {
            ConnectedExecutionOutput::Order(application.active_order(&order_id)?)
        },
        _ => unreachable!("control command routed to current-view query"),
    };
    Ok(value)
}

async fn execute_standalone_command(
    application: &mut CliExecutionApplication,
    binding: &StandaloneExecutionBinding,
    command: StandaloneCommand,
) -> Result<CliExecutionOutput, Box<dyn std::error::Error>> {
    let value = match command {
        StandaloneCommand::OpenOrders(args) => application
            .open_orders(args.symbol.as_deref(), args.limit)
            .await
            .map_err(invalid_standalone_input)?,
        StandaloneCommand::History(args) => application
            .history(args.symbol.as_deref(), args.limit)
            .await
            .map_err(invalid_standalone_input)?,
        StandaloneCommand::Order { order_id, symbol } => application
            .order(&order_id, symbol.as_deref())
            .await
            .map_err(invalid_standalone_input)?,
        StandaloneCommand::Fills(args) => application
            .fills(args.symbol.as_deref(), args.order_id.as_deref(), args.limit)
            .await
            .map_err(invalid_standalone_input)?,
        StandaloneCommand::Submit(args) => application
            .submit(
                direct_submit_request(binding, &args)?,
                args.symbol.as_deref(),
            )
            .await
            .map_err(invalid_standalone_input)?,
        StandaloneCommand::Cancel { order_id, symbol } => application
            .cancel(&order_id, symbol.as_deref())
            .await
            .map_err(invalid_standalone_input)?,
        StandaloneCommand::Replace {
            target_order_id,
            replacement,
        } => application
            .replace(
                &target_order_id,
                direct_submit_request(binding, &replacement)?,
                replacement.symbol.as_deref(),
            )
            .await
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
    launch_id: &str,
    instance_id: &str,
) -> Result<ConnectedExecutionOutput, Box<dyn std::error::Error>> {
    let value = match command {
        ConnectedCommand::Routes {
            account_id,
            segment_key,
            instrument_id,
            market_id,
        } => ConnectedExecutionOutput::Routes(
            application
                .routes(ExecutionRoutesQuery {
                    account_id: account_id.map(AccountId::new).transpose()?,
                    segment_key: segment_key.map(SegmentKey::new).transpose()?,
                    instrument_id: instrument_id.map(InstrumentId::new).transpose()?,
                    market_id: market_id.map(MarketId::new).transpose()?,
                    broker_id: None,
                })
                .await?,
        ),
        ConnectedCommand::Audit {
            order_id,
            remote_order_id,
            lifecycle,
            since_unix_nanos,
            until_unix_nanos,
            limit,
        } => ConnectedExecutionOutput::Audit(
            application
                .order_audit(ExecutionOrderAuditQuery {
                    order_id: order_id.map(OrderId::new).transpose()?,
                    remote_order_id: remote_order_id
                        .map(kairos_primitives::integration::RemoteOrderId::new)
                        .transpose()?,
                    lifecycle: lifecycle
                        .as_deref()
                        .map(parse_execution_lifecycle)
                        .transpose()?,
                    since_unix_nanos: since_unix_nanos.map(Into::into),
                    until_unix_nanos: until_unix_nanos.map(Into::into),
                    limit,
                })
                .await?,
        ),
        ConnectedCommand::Reconcile { order_id } => ConnectedExecutionOutput::Reconcile(
            application
                .reconcile(ReconcileExecutionRequest {
                    order_id: order_id.map(OrderId::new).transpose()?,
                    ..ReconcileExecutionRequest::default()
                })
                .await?,
        ),
        ConnectedCommand::Submit(args) => ConnectedExecutionOutput::Command(
            application
                .submit_intent(submit_intent_request(
                    submit_request(args)?,
                    launch_id,
                    instance_id,
                )?)
                .await?,
        ),
        ConnectedCommand::Cancel { order_id, reason } => ConnectedExecutionOutput::Command(
            application
                .cancel_order(
                    OrderId::new(order_id)?,
                    CancelOrderRequest {
                        reason: Some(reason),
                    },
                )
                .await?,
        ),
        ConnectedCommand::Replace {
            order_id,
            replacement,
        } => ConnectedExecutionOutput::Command(
            application
                .replace_order(
                    OrderId::new(order_id)?,
                    ReplaceOrderRequest {
                        quantity: Some(replacement.quantity.parse()?),
                        limit_price: replacement
                            .limit_price
                            .as_deref()
                            .map(str::parse)
                            .transpose()?,
                        options: Some(ExecutionOrderOptionsRequest {
                            time_in_force: replacement.time_in_force,
                            reduce_only: replacement.reduce_only,
                            post_only: replacement.post_only,
                            position_side: replacement.position_side,
                            ..ExecutionOrderOptionsRequest::default()
                        }),
                        reason: None,
                    },
                )
                .await?,
        ),
        _ => unreachable!("query command routed to typed indexed current view"),
    };
    Ok(value)
}

#[derive(Clone, Debug, Subcommand)]
enum Command {
    Standalone(StandaloneArgs),
    Connected(ConnectedArgs),
}

#[derive(Clone, Debug, Args)]
struct ConnectedArgs {
    #[arg(long)]
    mode: String,
    #[arg(long)]
    launch_id: String,
    #[arg(long)]
    instance_id: String,
    #[command(subcommand)]
    command: ConnectedCommand,
}

#[derive(Clone, Debug, Subcommand)]
enum StandaloneCommand {
    OpenOrders(StandaloneQueryArgs),
    History(StandaloneQueryArgs),
    Order {
        #[arg(long)]
        order_id: String,
        #[arg(long)]
        symbol: Option<String>,
    },
    Fills(StandaloneFillsArgs),
    Submit(StandaloneSubmitArgs),
    Cancel {
        #[arg(long)]
        order_id: String,
        #[arg(long)]
        symbol: Option<String>,
    },
    Replace {
        #[arg(long = "target-order-id")]
        target_order_id: String,
        #[command(flatten)]
        replacement: StandaloneSubmitArgs,
    },
}

impl StandaloneCommand {
    fn is_write(&self) -> bool {
        matches!(
            self,
            Self::Submit(_) | Self::Cancel { .. } | Self::Replace { .. }
        )
    }
}

#[derive(Clone, Debug, Args)]
struct StandaloneArgs {
    /// Account-owned, secret-free connection binding resolved by the public CLI.
    #[arg(long, hide = true)]
    binding_json: String,
    #[arg(long, hide = true)]
    confirm_live: bool,
    #[command(subcommand)]
    command: StandaloneCommand,
}

#[derive(Clone, Debug, Args)]
struct StandaloneQueryArgs {
    #[arg(long)]
    symbol: Option<String>,
    #[arg(long)]
    limit: Option<u32>,
}

#[derive(Clone, Debug, Args)]
struct StandaloneFillsArgs {
    #[arg(long)]
    symbol: Option<String>,
    #[arg(long)]
    order_id: Option<String>,
    #[arg(long)]
    limit: Option<u16>,
}

#[derive(Clone, Debug, Args)]
struct StandaloneSubmitArgs {
    #[arg(long)]
    order_id: String,
    #[arg(long)]
    instrument_id: String,
    #[arg(long)]
    symbol: Option<String>,
    #[arg(long)]
    quantity: String,
    #[arg(long, default_value = "buy")]
    side: String,
    #[arg(long, default_value = "market")]
    order_type: String,
    #[arg(long)]
    limit_price: Option<String>,
    #[arg(long)]
    time_in_force: Option<String>,
    #[arg(long)]
    reduce_only: Option<bool>,
    #[arg(long)]
    post_only: Option<bool>,
    #[arg(long)]
    position_side: Option<String>,
}

#[derive(Clone, Debug, Subcommand)]
enum ConnectedCommand {
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
    ActiveOrders {
        #[arg(long)]
        account_id: Option<String>,
    },
    Reconcile {
        #[arg(long)]
        order_id: Option<String>,
    },
    UnknownRemoteOrders,
    ActiveOrder {
        #[arg(long)]
        order_id: String,
    },
    Audit {
        #[arg(long)]
        order_id: Option<String>,
        #[arg(long)]
        remote_order_id: Option<String>,
        #[arg(long)]
        lifecycle: Option<String>,
        #[arg(long)]
        since_unix_nanos: Option<u64>,
        #[arg(long)]
        until_unix_nanos: Option<u64>,
        #[arg(long, default_value_t = 1000)]
        limit: u32,
    },
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
        replacement: ConnectedReplaceArgs,
    },
}

#[derive(Clone, Debug, Args)]
struct ConnectedReplaceArgs {
    #[arg(long)]
    quantity: String,
    #[arg(long)]
    limit_price: Option<String>,
    #[arg(long)]
    time_in_force: Option<String>,
    #[arg(long)]
    reduce_only: Option<bool>,
    #[arg(long)]
    post_only: Option<bool>,
    #[arg(long)]
    position_side: Option<String>,
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

fn direct_submit_request(
    binding: &StandaloneExecutionBinding,
    args: &StandaloneSubmitArgs,
) -> Result<SubmitOrder, Box<dyn std::error::Error>> {
    Ok(SubmitOrder {
        order_id: OrderId::new(args.order_id.clone())?,
        intent_id: None,
        strategy_id: None,
        account_id: AccountId::new(binding.account_id.clone())?,
        segment_key: SegmentKey::new(binding.segment_key.clone())?,
        instrument_id: InstrumentId::new(args.instrument_id.clone())?,
        market_id: None,
        execution_route_id: None,
        side: parse_side(&args.side)?,
        order_type: parse_order_type(&args.order_type)?,
        quantity: args.quantity.parse()?,
        limit_price: args.limit_price.as_deref().map(str::parse).transpose()?,
        options: ExecutionOrderOptions {
            time_in_force: args.time_in_force.clone(),
            reduce_only: args.reduce_only,
            post_only: args.post_only,
            position_side: args.position_side.clone(),
            ..Default::default()
        },
        submitted_at_unix_nanos: None,
    })
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
    launch_id: &str,
    instance_id: &str,
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
            launch_id: LaunchId::new(launch_id)?,
            instance_id: InstanceId::new(instance_id)?,
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
            execution_benchmarks: Vec::new(),
            deadline_unix_nanos: None,
            min_edge_bps: None,
            max_slippage_bps: None,
            estimated_fee_bps: None,
            minimum_net_credit: None,
            maximum_loss: None,
            algorithm: ExecutionAlgorithmPolicyRequest::Immediate,
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

fn parse_execution_lifecycle(
    value: &str,
) -> Result<ExecutionOrderLifecycle, Box<dyn std::error::Error>> {
    match value.to_ascii_lowercase().replace(['-', '_'], "").as_str() {
        "pending" => Ok(ExecutionOrderLifecycle::Pending),
        "submitting" => Ok(ExecutionOrderLifecycle::Submitting),
        "accepted" => Ok(ExecutionOrderLifecycle::Accepted),
        "partiallyfilled" => Ok(ExecutionOrderLifecycle::PartiallyFilled),
        "filled" => Ok(ExecutionOrderLifecycle::Filled),
        "cancelrequested" => Ok(ExecutionOrderLifecycle::CancelRequested),
        "canceled" => Ok(ExecutionOrderLifecycle::Canceled),
        "rejected" => Ok(ExecutionOrderLifecycle::Rejected),
        "expired" => Ok(ExecutionOrderLifecycle::Expired),
        "unknown" => Ok(ExecutionOrderLifecycle::Unknown),
        "failed" => Ok(ExecutionOrderLifecycle::Failed),
        _ => Err(format!("unsupported Execution order lifecycle: {value}").into()),
    }
}

fn print_json(value: impl Serialize) {
    let format = std::env::var("KAIROS_CLI_FORMAT")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(OutputFormat::Json);
    println!("{}", render(&value, format));
}

#[cfg(test)]
mod cli_tests {
    use clap::Parser;
    use kairos_primitives::account::{AccountId, SegmentKey};
    use kairos_primitives::execution::OrderId;
    use kairos_primitives::reference::InstrumentId;

    use super::{
        Cli, ConnectedExecutionOutput, ExecutionOrderOptions, OrderSide, OrderType, SubmitOrder,
        connected_result, submit_intent_request,
    };

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
            "--binding-json",
            r#"{"account_id":"main"}"#,
            "open-orders",
        ]);
        assert!(
            parsed.is_ok(),
            "execution standalone direct query must parse: {parsed:?}"
        );

        let parsed = Cli::try_parse_from([
            "kairos-execution-cli",
            "--workspace",
            "/tmp",
            "standalone",
            "--binding-json",
            r#"{"account_id":"main"}"#,
            "audit",
        ]);
        assert!(
            parsed.is_err(),
            "local evidence tools must not remain exposed"
        );

        let parsed = Cli::try_parse_from([
            "kairos-execution-cli",
            "--workspace",
            "/tmp",
            "connected",
            "--mode",
            "paper",
            "--launch-id",
            "demo",
            "--instance-id",
            "run-1",
            "active-orders",
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
            "--launch-id",
            "demo",
            "--instance-id",
            "run-1",
            "active-orders",
        ]);
        assert!(parsed.is_err(), "connected identity must include mode");

        for removed_alias in [
            "snapshot",
            "recent-order-events",
            "recent-fills",
            "list",
            "open",
            "closed",
            "reconcile-remote",
            "show",
            "place",
        ] {
            let parsed = Cli::try_parse_from([
                "kairos-execution-cli",
                "--workspace",
                "/tmp",
                "connected",
                "--mode",
                "paper",
                "--launch-id",
                "demo",
                "--instance-id",
                "run-1",
                removed_alias,
            ]);
            assert!(
                parsed.is_err(),
                "removed connected alias must not parse: {removed_alias}"
            );
        }

        let parsed = Cli::try_parse_from([
            "kairos-execution-cli",
            "--workspace",
            "/tmp",
            "connected",
            "--mode",
            "paper",
            "--launch-id",
            "demo",
            "--instance-id",
            "run-1",
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
            "--mode",
            "paper",
            "--launch-id",
            "demo",
            "--instance-id",
            "run-1",
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
            "--mode",
            "paper",
            "--launch-id",
            "demo",
            "--instance-id",
            "run-1",
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
            "--mode",
            "paper",
            "--launch-id",
            "demo",
            "--instance-id",
            "run-1",
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

    #[test]
    fn connected_submit_preserves_selected_launch_instance_identity() {
        let request = SubmitOrder {
            order_id: OrderId::new("order-1").unwrap(),
            intent_id: None,
            strategy_id: None,
            account_id: AccountId::new("main").unwrap(),
            segment_key: SegmentKey::new("spot").unwrap(),
            instrument_id: InstrumentId::new("BTC-USDT").unwrap(),
            market_id: None,
            execution_route_id: None,
            side: OrderSide::Buy,
            order_type: OrderType::Market,
            quantity: "1".parse().unwrap(),
            limit_price: None,
            options: ExecutionOrderOptions::default(),
            submitted_at_unix_nanos: None,
        };

        let intent = submit_intent_request(request, "grid-btc", "run-20260823-02")
            .expect("connected submit intent");

        assert_eq!(intent.intent.launch_id.as_str(), "grid-btc");
        assert_eq!(intent.intent.instance_id.as_str(), "run-20260823-02");
    }

    #[test]
    fn connected_output_is_self_describing_without_python_decoration() {
        let value = connected_result(
            ConnectedExecutionOutput::Command(kairos_execution_contract::ExecutionCommandStatus {
                status: "ok".into(),
                command_id: None,
                intent_id: None,
                order_id: None,
            }),
            "paper",
            "grid-btc",
            "run-1",
        );
        assert_eq!(value.owner, "execution");
        assert_eq!(value.mode, "paper");
        assert_eq!(value.launch_id, "grid-btc");
        assert_eq!(value.instance_id, "run-1");
        assert_eq!(value.scope, "launch-instance");
    }
}
