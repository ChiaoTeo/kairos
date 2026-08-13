use clap::{Args, Parser, Subcommand};
use kairos_domain_types::{AccountId, InstrumentId, IntentId, MarketId, OrderId, SegmentKey};
use kairos_execution::{
    application::{
        BacktestApplication, BacktestRequest, CancelOrder, ExecutionAuditQuery,
        ExecutionFillReport, ExecutionOrderOptions, RemoteOrderQuery, ReplaceOrder, SubmitOrder,
    },
    composition::{
        compose_direct_execution_connections, ExecutionConnectionOptions, SqlxExecutionStore,
    },
    credentials::load_workspace_credential,
    domain::{OrderSide, OrderType},
    ExecutionApplication,
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
    run_direct_with_options(
        &workspace,
        args.command,
        Some(args.connection.connection_options(&workspace)?),
        args.confirm_live,
    )?;
    Ok(())
}

#[derive(Debug, Parser)]
#[command(name = "kairos-execution-cli", about = "One-shot execution commands")]
struct Cli {
    #[arg(long)]
    workspace: String,
    #[arg(long, global = true, value_parser = OutputFormat::from_str)]
    output: Option<OutputFormat>,
    #[arg(long, global = true)]
    confirm_live: bool,
    #[command(flatten)]
    connection: ConnectionArgs,
    #[command(subcommand)]
    command: Command,
}

#[derive(Clone, Debug, Args)]
struct ConnectionArgs {
    #[arg(long, global = true, default_value = "default")]
    route_id: String,
    #[arg(long, global = true, default_value = "main")]
    account_id: String,
    #[arg(long, global = true, default_value = "spot")]
    segment_key: String,
    #[arg(long, global = true, default_value = "simulated")]
    provider: String,
    #[arg(long, global = true, default_value = "spot")]
    product: String,
    #[arg(long, global = true, env = "BINANCE_API_KEY", default_value = "")]
    api_key: String,
    #[arg(long, global = true, env = "BINANCE_API_SECRET", default_value = "")]
    secret: String,
    #[arg(long, global = true)]
    credential_id: Option<String>,
    #[arg(long, global = true, env = "OKX_PASSPHRASE", default_value = "")]
    passphrase: String,
    #[arg(long, global = true, default_value = "")]
    base_url: String,
    #[arg(long, global = true, default_value = "")]
    websocket_url: String,
    #[arg(long, global = true)]
    isolated_symbol: Option<String>,
    #[arg(long, global = true, default_value_t = 1_000)]
    request_weight_per_minute: u32,
    #[arg(long, global = true, default_value_t = 50)]
    cancel_reserve_weight: u32,
    #[arg(long, global = true, default_value_t = 1_024)]
    order_event_queue_capacity: usize,
    #[arg(long, global = true, default_value = "default-egress")]
    egress_scope_id: String,
    #[arg(long, global = true, default_value = "execution-default")]
    principal_scope_id: String,
    #[arg(long, global = true, default_value_t = 50)]
    orders_per_10_seconds: u32,
    #[arg(long, global = true, default_value_t = 160_000)]
    orders_per_day: u32,
    #[arg(long, global = true, default_value = "127.0.0.1")]
    host: String,
    #[arg(long, global = true, default_value_t = 4002)]
    port: u16,
    #[arg(long, global = true, default_value_t = 0)]
    client_id: i32,
}

impl ConnectionArgs {
    fn connection_options(
        &self,
        workspace: &Workspace,
    ) -> Result<ExecutionConnectionOptions, Box<dyn std::error::Error>> {
        let stored =
            load_workspace_credential(workspace, &self.provider, self.credential_id.as_deref())?;
        let (default_base_url, default_websocket_url) =
            provider_endpoints(&self.provider, &self.product);
        Ok(ExecutionConnectionOptions {
            route_id: self.route_id.clone(),
            required: true,
            account_id: self.account_id.clone(),
            segment_key: self.segment_key.clone(),
            provider: self.provider.clone(),
            product: self.product.clone(),
            api_key: if self.api_key.is_empty() {
                stored
                    .as_ref()
                    .map(|value| value.api_key.clone())
                    .unwrap_or_default()
            } else {
                self.api_key.clone()
            }
            .into(),
            secret: if self.secret.is_empty() {
                stored
                    .as_ref()
                    .map(|value| value.secret.clone())
                    .unwrap_or_default()
            } else {
                self.secret.clone()
            }
            .into(),
            passphrase: if self.passphrase.is_empty() {
                stored
                    .as_ref()
                    .map(|value| value.passphrase.clone())
                    .unwrap_or_default()
            } else {
                self.passphrase.clone()
            }
            .into(),
            base_url: if self.base_url.trim().is_empty() {
                default_base_url.into()
            } else {
                self.base_url.clone()
            },
            websocket_url: if self.websocket_url.trim().is_empty() {
                default_websocket_url.into()
            } else {
                self.websocket_url.clone()
            },
            isolated_symbol: self.isolated_symbol.clone(),
            request_weight_per_minute: self.request_weight_per_minute,
            cancel_reserve_weight: self.cancel_reserve_weight,
            order_event_queue_capacity: self.order_event_queue_capacity,
            shared_quota_ledger_path: Some(
                workspace
                    .state_root()
                    .join("integration")
                    .join("provider-quota.mmap"),
            ),
            egress_scope_id: self.egress_scope_id.clone(),
            principal_scope_id: self.principal_scope_id.clone(),
            orders_per_10_seconds: self.orders_per_10_seconds,
            orders_per_day: self.orders_per_day,
            host: self.host.clone(),
            port: self.port,
            client_id: self.client_id,
        })
    }
}

fn provider_endpoints(provider: &str, product: &str) -> (&'static str, &'static str) {
    match (
        provider.trim().to_ascii_lowercase().as_str(),
        product.trim().to_ascii_lowercase().as_str(),
    ) {
        ("binance", "usd-m-futures" | "swap") => {
            ("https://fapi.binance.com", "wss://fstream.binance.com")
        }
        ("binance", "coin-m-futures" | "futures") => {
            ("https://dapi.binance.com", "wss://dstream.binance.com")
        }
        ("binance", "options" | "option") => (
            "https://eapi.binance.com",
            "wss://nbstream.binance.com/eoptions/private/stream",
        ),
        ("binance", "cross-margin" | "margin" | "isolated-margin") => {
            ("https://api.binance.com", "wss://stream.binance.com:9443")
        }
        ("okx" | "okex", _) => ("https://www.okx.com", "wss://ws.okx.com:8443/ws/v5/private"),
        _ => (
            "https://api.binance.com",
            "wss://ws-api.binance.com:443/ws-api/v3",
        ),
    }
}

#[derive(Clone, Debug, Subcommand)]
enum Command {
    Snapshot,
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
    RemoteOpenOrders {
        #[arg(long)]
        symbol: Option<String>,
    },
    RemoteHistory {
        #[arg(long)]
        symbol: Option<String>,
        #[arg(long)]
        limit: Option<u32>,
    },
    RemoteInspect {
        #[arg(long)]
        order_id: String,
    },
    ReconcileRemote {
        #[arg(long)]
        symbol: Option<String>,
        #[arg(long)]
        limit: Option<u32>,
    },
    UnknownRemoteOrders,
    LinkUnknown {
        #[arg(long)]
        remote_order_id: String,
        #[arg(long)]
        local_order_id: String,
    },
    StreamNext,
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
    occurred_at_unix_nanos: Option<u64>,
}

fn run_direct_with_options(
    workspace: &Workspace,
    command: Command,
    options: Option<ExecutionConnectionOptions>,
    confirm_live: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    let options = options.expect("direct execution options");
    let path = workspace.child(&["state", "execution", "execution-state.sqlite"])?;
    let connections = compose_direct_execution_connections(&options)?;
    let (order_entry, order_query, execution_stream, _runtime) = connections.into_parts();
    let mut application = ExecutionApplication::with_dependencies_and_query_and_stream(
        "execution",
        order_entry,
        order_query,
        execution_stream,
        Some(Box::new(SqlxExecutionStore::new(path)?)),
    )?;
    application.configure_live_trading(
        !matches!(
            options.provider.trim().to_ascii_lowercase().as_str(),
            "simulated" | "paper"
        ),
        confirm_live,
    );
    let value = match command {
        Command::Snapshot => serde_json::to_value(application.snapshot())?,
        Command::Orders { account_id } => {
            serde_json::json!({"orders": application.orders(account_id.as_deref())})
        }
        Command::OpenOrders { account_id } => {
            let orders: Vec<_> = application
                .orders(account_id.as_deref())
                .into_iter()
                .filter(|order| !order.status.terminal())
                .collect();
            serde_json::json!({"orders": orders})
        }
        Command::History { account_id } => {
            serde_json::json!({"orders": application.orders(account_id.as_deref())})
        }
        Command::RemoteOpenOrders { symbol } => {
            serde_json::to_value(application.remote_open_orders(RemoteOrderQuery {
                symbol: symbol.map(kairos_domain_types::Symbol::new).transpose()?,
                ..Default::default()
            })?)?
        }
        Command::RemoteHistory { symbol, limit } => {
            serde_json::to_value(application.remote_history(RemoteOrderQuery {
                symbol: symbol.map(kairos_domain_types::Symbol::new).transpose()?,
                limit,
                ..Default::default()
            })?)?
        }
        Command::RemoteInspect { order_id } => {
            serde_json::to_value(application.remote_detail(RemoteOrderQuery {
                order_id: Some(kairos_domain_types::OrderId::new(order_id)?),
                ..Default::default()
            })?)?
        }
        Command::ReconcileRemote { symbol, limit } => serde_json::json!({
            "changed": application.reconcile_remote_orders(RemoteOrderQuery {
                symbol: symbol
                    .map(kairos_domain_types::Symbol::new)
                    .transpose()?,
                limit,
                ..Default::default()
            })?
        }),
        Command::UnknownRemoteOrders => {
            serde_json::json!({"orders": application.unknown_remote_orders()})
        }
        Command::LinkUnknown {
            remote_order_id,
            local_order_id,
        } => serde_json::to_value(
            application.link_unknown_remote_order(&remote_order_id, &local_order_id)?,
        )?,
        Command::StreamNext => serde_json::to_value(application.consume_remote_execution_event()?)?,
        Command::Status { order_id } | Command::Inspect { order_id } => {
            let order = application
                .orders(None)
                .into_iter()
                .find(|order| order.order_id == order_id)
                .ok_or_else(|| format!("unknown order: {order_id}"))?;
            serde_json::to_value(order)?
        }
        Command::Events { order_id } => {
            serde_json::json!({"events": application.events(order_id.as_deref())})
        }
        Command::Trace { order_id } => {
            serde_json::json!({"events": application.trace(&order_id)})
        }
        Command::Audit {
            order_id,
            remote_order_id,
            status,
            limit,
        } => serde_json::to_value(
            application.audit_events(ExecutionAuditQuery {
                order_id: order_id
                    .map(kairos_domain_types::OrderId::new)
                    .transpose()?,
                remote_order_id: remote_order_id
                    .map(kairos_domain_types::RemoteOrderId::new)
                    .transpose()?,
                status,
                limit,
                ..Default::default()
            })?,
        )?,
        Command::Journal { order_id } => {
            serde_json::to_value(application.audit_events(ExecutionAuditQuery {
                order_id: Some(kairos_domain_types::OrderId::new(order_id)?),
                ..Default::default()
            })?)?
        }
        Command::Backtest { file } => {
            let request: BacktestRequest = toml::from_str(&std::fs::read_to_string(file)?)?;
            serde_json::to_value(BacktestApplication::evaluate(request)?)?
        }
        Command::Fills { order_id } => {
            serde_json::json!({"fills": application.fills(order_id.as_deref())})
        }
        Command::Fill(args) => {
            serde_json::to_value(application.record_fill(ExecutionFillReport {
                fill_id: kairos_domain_types::FillId::new(args.fill_id)?,
                order_id: kairos_domain_types::OrderId::new(args.order_id)?,
                quantity: args.quantity.parse()?,
                price: args.price.parse()?,
                fee: args.fee.parse()?,
                occurred_at_unix_nanos: args.occurred_at_unix_nanos.map(Into::into),
            })?)?
        }
        Command::Submit(args) => {
            let request = submit_request(args.clone())?;
            if args.dry_run {
                serde_json::to_value(application.preview_submit(&request)?)?
            } else {
                serde_json::to_value(application.submit(request)?)?
            }
        }
        Command::Cancel { order_id, reason } => {
            serde_json::to_value(application.cancel(CancelOrder {
                order_id: OrderId::new(order_id)?,
                reason,
            })?)?
        }
        Command::Replace {
            order_id,
            replacement,
        } => serde_json::to_value(application.replace(ReplaceOrder {
            order_id: OrderId::new(order_id)?,
            replacement: submit_request(replacement)?,
        })?)?,
    };
    print_json(value);
    Ok(())
}

fn submit_request(args: SubmitArgs) -> Result<SubmitOrder, Box<dyn std::error::Error>> {
    Ok(SubmitOrder {
        order_id: OrderId::new(args.order_id)?,
        intent_id: args.intent_id.map(IntentId::new).transpose()?,
        account_id: AccountId::new(args.account_id)?,
        segment_key: SegmentKey::new(args.segment_key)?,
        instrument_id: InstrumentId::new(args.instrument_id)?,
        market_id: args.market_id.map(MarketId::new).transpose()?,
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
