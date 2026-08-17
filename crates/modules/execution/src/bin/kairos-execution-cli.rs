use clap::{Args, Parser, Subcommand};
use kairos_execution::application::{
    BacktestApplication, BacktestRequest, ExecutionFillReport, ExecutionOrderOptions, OrderSide,
    OrderType, SubmitOrder,
};
use kairos_execution_contract::{ExecutionViewKey, ExecutionViewKind, ExecutionViewReader};
use kairos_primitives::{
    AccountId, ExecutionRouteId, InstrumentId, IntentId, MarketId, OrderId, SegmentKey,
};
use kairos_workspace::cli::{render, OutputFormat};
use kairos_workspace::workspace::Workspace;
use std::str::FromStr;

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
    if args.command.is_mmap_query() {
        let instance = workspace.instance(&args.mode, &args.launch_id, &args.instance_id)?;
        print_json(read_current_execution_view(
            &instance,
            workspace.id(),
            &args.command,
        )?);
        return Ok(());
    }
    if let Command::Backtest { file } = &args.command {
        let request: BacktestRequest = toml::from_str(&std::fs::read_to_string(file)?)?;
        print_json(serde_json::to_value(BacktestApplication::evaluate(
            request,
        )?)?);
        return Ok(());
    }
    let instance = workspace.instance(&args.mode, &args.launch_id, &args.instance_id)?;
    let socket = instance.socket("execution")?;
    print_json(execute_control_command(&socket, args.command).await?);
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

impl Command {
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

fn read_current_execution_view(
    instance: &kairos_workspace::workspace::InstanceWorkspace,
    workspace_id: &str,
    command: &Command,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    use kairos_protocol::generated::kairos::{common::v_2 as common, execution::v_2 as fb};
    let key = ExecutionViewKey::new(
        workspace_id,
        ExecutionViewKind::CurrentExecution,
        Some(instance.launch_id()),
        Some(instance.instance_id()),
    )?;
    let frame = ExecutionViewReader::open(instance.root(), key.clone())?.read()?;
    let envelope = frame.envelope_metadata();
    let view = frame.current_execution()?;
    let metadata = view.metadata();
    if metadata.view_key() != key.canonical_key()
        || metadata.workspace_id() != workspace_id
        || metadata.launch_id() != Some(instance.launch_id())
        || metadata.instance_id() != Some(instance.instance_id())
    {
        return Err("Execution mmap identity mismatch".into());
    }
    if metadata.completeness() != common::ViewCompleteness::COMPLETE
        || metadata.generation() != envelope.generation
        || metadata.applied_revision().unwrap_or_default() != envelope.applied_event_sequence
    {
        return Err("Execution mmap is partial or its watermarks differ".into());
    }

    let orders = view.orders().iter().map(order_json).collect::<Vec<_>>();
    let intents = view.intents().iter().map(intent_json).collect::<Vec<_>>();
    let fills = view.fills().iter().map(fill_json).collect::<Vec<_>>();
    let events = view
        .order_events()
        .iter()
        .map(order_event_json)
        .collect::<Vec<_>>();
    let unknown = view
        .unknown_remote_orders()
        .iter()
        .map(unknown_remote_json)
        .collect::<Vec<_>>();
    let value = match command {
        Command::Snapshot => serde_json::json!({
            "generation": metadata.generation(),
            "event_sequence": metadata.applied_revision().unwrap_or_default(),
            "orders": orders,
            "intents": intents,
            "fills": fills,
            "events": events,
            "unknown_remote_orders": unknown,
            "commitment_count": view.commitments().len(),
            "risk_reservation_count": view.risk_reservations().len(),
            "exchange_event_watermark_unix_nanos": view.exchange_event_watermark_unix_nanos(),
            "fill_history_truncated": view.fill_history_truncated(),
            "order_event_history_truncated": view.order_event_history_truncated(),
            "intent_event_history_truncated": view.intent_event_history_truncated(),
        }),
        Command::Orders { account_id } => serde_json::json!({
            "orders": filter_orders(orders, account_id.as_deref(), None)
        }),
        Command::OpenOrders { account_id } => serde_json::json!({
            "orders": filter_orders(orders, account_id.as_deref(), Some(false))
        }),
        Command::History { account_id } => serde_json::json!({
            "orders": filter_orders(orders, account_id.as_deref(), Some(true))
        }),
        Command::UnknownRemoteOrders => serde_json::json!({"orders": unknown}),
        Command::Status { order_id } | Command::Inspect { order_id } => orders
            .into_iter()
            .find(|value| value["order_id"] == order_id.as_str())
            .ok_or_else(|| format!("unknown order: {order_id}"))?,
        Command::Events { order_id } => serde_json::json!({
            "events": filter_events(events, order_id.as_deref(), None, None, None)
        }),
        Command::Trace { order_id } | Command::Journal { order_id } => serde_json::json!({
            "events": filter_events(events, Some(order_id), None, None, None)
        }),
        Command::Audit {
            order_id,
            remote_order_id,
            status,
            limit,
        } => serde_json::json!({
            "events": filter_events(
                events,
                order_id.as_deref(),
                remote_order_id.as_deref(),
                status.as_deref(),
                *limit,
            )
        }),
        Command::Fills { order_id } => serde_json::json!({
            "fills": fills.into_iter().filter(|value| {
                order_id.as_deref().is_none_or(|expected| value["order_id"] == expected)
            }).collect::<Vec<_>>()
        }),
        _ => unreachable!("non-query command was routed to mmap"),
    };
    let result: Result<serde_json::Value, Box<dyn std::error::Error>> = Ok(value);

    fn order_json(value: fb::OrderState<'_>) -> serde_json::Value {
        serde_json::json!({
            "order_id": value.order_id(),
            "intent_id": value.intent_id(),
            "plan_id": value.plan_id(),
            "leg_id": value.leg_id(),
            "strategy_id": value.strategy_id(),
            "account_id": value.account_id(),
            "segment_key": value.segment_key(),
            "instrument_id": value.instrument_id(),
            "market_id": value.market_id(),
            "execution_route_id": value.execution_route_id(),
            "remote_order_id": value.remote_order_id(),
            "side": value.side().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
            "order_type": value.order_type().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
            "quantity": decimal_json(value.quantity()),
            "filled_quantity": decimal_json(value.filled_quantity()),
            "limit_price": value.limit_price().map(decimal_json),
            "status": value.lifecycle().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
            "terminal": matches!(value.lifecycle(), fb::OrderLifecycle::FILLED | fb::OrderLifecycle::CANCELED | fb::OrderLifecycle::REJECTED | fb::OrderLifecycle::EXPIRED | fb::OrderLifecycle::FAILED),
            "submitted_at_unix_nanos": value.submitted_at_unix_nanos(),
            "updated_at_unix_nanos": value.updated_at_unix_nanos(),
            "reason": value.reason(),
        })
    }

    fn intent_json(value: fb::IntentState<'_>) -> serde_json::Value {
        let intent = value.intent();
        serde_json::json!({
            "intent_id": intent.intent_id(),
            "strategy_id": intent.strategy_id(),
            "launch_id": intent.launch_id(),
            "instance_id": intent.instance_id(),
            "intent_type": intent.intent_type().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
            "status": value.lifecycle().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
            "updated_at_unix_nanos": value.updated_at_unix_nanos(),
            "reason": value.reason(),
        })
    }

    fn fill_json(value: fb::Fill<'_>) -> serde_json::Value {
        serde_json::json!({
            "fill_id": value.fill_id(),
            "order_id": value.order_id(),
            "intent_id": value.intent_id(),
            "strategy_id": value.strategy_id(),
            "account_id": value.account_id(),
            "segment_key": value.segment_key(),
            "instrument_id": value.instrument_id(),
            "market_id": value.market_id(),
            "remote_order_id": value.remote_order_id(),
            "side": value.side().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
            "quantity": decimal_json(value.quantity()),
            "price": decimal_json(value.price()),
            "fee": value.fee().map(decimal_json),
            "fee_currency": value.fee_asset_id(),
            "occurred_at_unix_nanos": value.source_filled_at_unix_nanos(),
        })
    }

    fn order_event_json(value: fb::OrderLifecycleEventState<'_>) -> serde_json::Value {
        serde_json::json!({
            "order_id": value.order_id(),
            "intent_id": value.intent_id(),
            "plan_id": value.plan_id(),
            "leg_id": value.leg_id(),
            "status": value.lifecycle().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
            "remote_order_id": value.remote_order_id(),
            "occurred_at_unix_nanos": value.occurred_at_unix_nanos(),
            "reason": value.reason(),
            "fill_id": value.fill_id(),
            "filled_quantity": value.filled_quantity().map(decimal_json),
        })
    }

    fn unknown_remote_json(value: fb::UnknownRemoteOrderState<'_>) -> serde_json::Value {
        serde_json::json!({
            "remote_order_id": value.remote_order_id(),
            "symbol": value.symbol(),
            "status": value.lifecycle().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
            "execution_id": value.execution_id(),
            "fill_quantity": value.fill_quantity().map(decimal_json),
            "fill_price": value.fill_price().map(decimal_json),
            "fee_currency": value.fee_currency(),
            "fee_amount": value.fee_amount().map(decimal_json),
            "first_seen_at_unix_nanos": value.first_seen_at_unix_nanos(),
            "last_seen_at_unix_nanos": value.last_seen_at_unix_nanos(),
            "resolution": value.resolution(),
            "reason": value.reason(),
        })
    }

    fn decimal_json(value: &common::Decimal64) -> String {
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

    result
}

fn filter_orders(
    values: Vec<serde_json::Value>,
    account_id: Option<&str>,
    terminal: Option<bool>,
) -> Vec<serde_json::Value> {
    values
        .into_iter()
        .filter(|value| {
            account_id.is_none_or(|expected| value["account_id"] == expected)
                && terminal.is_none_or(|expected| value["terminal"] == expected)
        })
        .collect()
}

fn filter_events(
    values: Vec<serde_json::Value>,
    order_id: Option<&str>,
    remote_order_id: Option<&str>,
    status: Option<&str>,
    limit: Option<u32>,
) -> Vec<serde_json::Value> {
    let mut values = values
        .into_iter()
        .filter(|value| {
            order_id.is_none_or(|expected| value["order_id"] == expected)
                && remote_order_id.is_none_or(|expected| value["remote_order_id"] == expected)
                && status.is_none_or(|expected| value["status"] == expected)
        })
        .collect::<Vec<_>>();
    if let Some(limit) = limit {
        values.truncate(limit as usize);
    }
    values
}

async fn execute_control_command(
    socket: &std::path::Path,
    command: Command,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    let client = kairos_workspace::RestControlClient::new(socket);
    let (method, path, body) = match command {
        Command::Routes {
            account_id,
            segment_key,
            instrument_id,
            market_id,
            order_type,
            option,
        } => {
            let query = [
                ("account_id", account_id),
                ("segment_key", segment_key),
                ("instrument_id", instrument_id),
                ("market_id", market_id),
                ("order_type", order_type),
                ("options", (!option.is_empty()).then(|| option.join(","))),
            ]
            .into_iter()
            .filter_map(|(key, value)| value.map(|value| format!("{key}={value}")))
            .collect::<Vec<_>>()
            .join("&");
            (
                "GET",
                if query.is_empty() {
                    "/v1/routes".into()
                } else {
                    format!("/v1/routes?{query}")
                },
                None,
            )
        }
        Command::Reconcile { order_id } => (
            "POST",
            "/v1/reconciliation".into(),
            Some(serde_json::to_vec(&serde_json::json!({
                "order_id": order_id,
                "reason": "operator requested reconciliation",
            }))?),
        ),
        Command::LinkUnknown {
            remote_order_id,
            local_order_id,
        } => (
            "POST",
            format!(
                "/v1/link-unknown-remote?remote_order_id={remote_order_id}&local_order_id={local_order_id}"
            ),
            None,
        ),
        Command::Fill(args) => {
            let request = ExecutionFillReport {
                fill_id: kairos_primitives::FillId::new(args.fill_id)?,
                order_id: kairos_primitives::OrderId::new(args.order_id)?,
                quantity: args.quantity.parse()?,
                price: args.price.parse()?,
                fee: args.fee.parse()?,
                fee_currency: args
                    .fee_currency
                    .as_deref()
                    .map(kairos_primitives::Currency::new)
                    .transpose()?,
                occurred_at_unix_nanos: args.occurred_at_unix_nanos.map(Into::into),
                execution_market_id: None,
                reported_provider_id: None,
                provider_product: None,
                provider_symbol: None,
                remote_order_id: None,
            };
            ("POST", "/v1/fill".into(), Some(serde_json::to_vec(&request)?))
        }
        Command::Submit(args) => {
            let dry_run = args.dry_run;
            let request = submit_request(args)?;
            (
                "POST",
                if dry_run {
                    "/v1/preview-submit"
                } else {
                    "/v1/orders"
                }
                .into(),
                Some(serde_json::to_vec(&request)?),
            )
        }
        Command::Cancel { order_id, reason } => (
            "DELETE",
            format!("/v1/orders/{order_id}"),
            Some(serde_json::to_vec(&serde_json::json!({"reason": reason}))?),
        ),
        Command::Replace {
            order_id,
            replacement,
        } => (
            "PATCH",
            format!("/v1/orders/{order_id}"),
            Some(serde_json::to_vec(&submit_request(replacement)?)?),
        ),
        Command::Backtest { .. } => unreachable!("backtest handled locally"),
        _ => unreachable!("query command routed to typed mmap"),
    };
    Ok(client.request_json(method, &path, body.as_deref()).await?)
}

#[derive(Clone, Debug, Subcommand)]
enum Command {
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
        #[arg(long)]
        order_type: Option<String>,
        #[arg(long = "option")]
        option: Vec<String>,
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
    LinkUnknown {
        #[arg(long)]
        remote_order_id: String,
        #[arg(long)]
        local_order_id: String,
    },
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
    Backtest {
        #[arg(long)]
        file: String,
    },
    Fills {
        #[arg(long)]
        order_id: Option<String>,
    },
    Fill(FillArgs),
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
    dry_run: bool,
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
struct FillArgs {
    #[arg(long)]
    fill_id: String,
    #[arg(long)]
    order_id: String,
    #[arg(long)]
    quantity: String,
    #[arg(long)]
    price: String,
    #[arg(long, default_value = "0")]
    fee: String,
    #[arg(long)]
    fee_currency: Option<String>,
    #[arg(long)]
    occurred_at_unix_nanos: Option<u64>,
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
    use super::Cli;
    use clap::Parser;

    #[test]
    fn command_surface_builds_without_duplicate_aliases() {
        let parsed =
            Cli::try_parse_from(["kairos-execution-cli", "--workspace", "/tmp", "snapshot"]);
        assert!(
            parsed.is_ok(),
            "execution CLI command surface must parse: {parsed:?}"
        );
    }
}
