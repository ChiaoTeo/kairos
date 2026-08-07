use clap::{Args, Parser, Subcommand};
use kairos_execution::{
    application::{
        BacktestApplication, BacktestRequest, CancelOrder, ExecuteIntent, ExecutionAuditQuery,
        ExecutionFillReport, ExecutionOrderOptions, RemoteOrderQuery, ReplaceOrder, SubmitOrder,
    },
    composition::{
        compose_execution_stream, compose_order_entry, compose_order_query,
        ExecutionConnectionOptions, FileExecutionStore,
    },
    credentials::load_workspace_credential,
    domain::{OrderSide, OrderType},
    ExecutionApplication,
};
use kairos_workspace::{control::RestControlClient, workspace::Workspace};

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
            let client = RestControlClient::new(workspace.process_socket("execution")?);
            let value = match command {
                ServerCommand::Status => client.health().await?,
                ServerCommand::Snapshot => client.request_json("GET", "/v1/snapshot", None).await?,
                ServerCommand::Orders { account_id } => {
                    client
                        .request_json(
                            "GET",
                            &format!(
                                "/v1/orders{}",
                                account_id
                                    .map(|value| format!("?account_id={value}"))
                                    .unwrap_or_default()
                            ),
                            None,
                        )
                        .await?
                }
                ServerCommand::OpenOrders { account_id } => {
                    client
                        .request_json(
                            "GET",
                            &format!(
                                "/v1/open-orders{}",
                                account_id
                                    .map(|value| format!("?account_id={value}"))
                                    .unwrap_or_default()
                            ),
                            None,
                        )
                        .await?
                }
                ServerCommand::History { account_id } => {
                    client
                        .request_json(
                            "GET",
                            &format!(
                                "/v1/history{}",
                                account_id
                                    .map(|value| format!("?account_id={value}"))
                                    .unwrap_or_default()
                            ),
                            None,
                        )
                        .await?
                }
                ServerCommand::RemoteOpenOrders { symbol } => {
                    client
                        .request_json(
                            "GET",
                            &format!(
                                "/v1/remote-open-orders{}",
                                symbol.map(|v| format!("?symbol={v}")).unwrap_or_default()
                            ),
                            None,
                        )
                        .await?
                }
                ServerCommand::RemoteHistory { symbol, limit } => {
                    client
                        .request_json(
                            "GET",
                            &format!(
                                "/v1/remote-history{}",
                                remote_query_string(symbol, limit, None)
                            ),
                            None,
                        )
                        .await?
                }
                ServerCommand::RemoteInspect { order_id } => {
                    client
                        .request_json(
                            "GET",
                            &format!("/v1/remote-order?order_id={order_id}"),
                            None,
                        )
                        .await?
                }
                ServerCommand::StreamNext => {
                    client
                        .request_json("GET", "/v1/stream/consume", None)
                        .await?
                }
                ServerCommand::Events { order_id } => {
                    client
                        .request_json(
                            "GET",
                            &format!(
                                "/v1/events{}",
                                order_id
                                    .map(|value| format!("?order_id={value}"))
                                    .unwrap_or_default()
                            ),
                            None,
                        )
                        .await?
                }
                ServerCommand::Trace { order_id } => {
                    client
                        .request_json("GET", &format!("/v1/trace?order_id={order_id}"), None)
                        .await?
                }
                ServerCommand::Audit {
                    order_id,
                    venue_order_id,
                    status,
                    limit,
                } => {
                    client
                        .request_json(
                            "GET",
                            &format!(
                                "/v1/audit{}",
                                audit_query_string(order_id, venue_order_id, status, limit)
                            ),
                            None,
                        )
                        .await?
                }
                ServerCommand::Journal { order_id } => {
                    client
                        .request_json("GET", &format!("/v1/journal?order_id={order_id}"), None)
                        .await?
                }
                ServerCommand::Backtest { file } => {
                    let request: BacktestRequest = toml::from_str(&std::fs::read_to_string(file)?)?;
                    let body = serde_json::to_vec(&request)?;
                    client
                        .request_json("POST", "/v1/backtest", Some(&body))
                        .await?
                }
                ServerCommand::Fills { order_id } => {
                    client
                        .request_json(
                            "GET",
                            &format!(
                                "/v1/fills{}",
                                order_id
                                    .map(|value| format!("?order_id={value}"))
                                    .unwrap_or_default()
                            ),
                            None,
                        )
                        .await?
                }
                ServerCommand::Submit(args) => {
                    let dry_run = args.dry_run;
                    let body = serde_json::to_vec(&submit_request(args)?)?;
                    client
                        .request_json(
                            "POST",
                            if dry_run {
                                "/v1/preview-submit"
                            } else {
                                "/v1/submit"
                            },
                            Some(&body),
                        )
                        .await?
                }
                ServerCommand::Cancel { order_id, reason } => {
                    let body = serde_json::to_vec(&CancelOrder { order_id, reason })?;
                    client
                        .request_json("POST", "/v1/cancel", Some(&body))
                        .await?
                }
                ServerCommand::Replace {
                    order_id,
                    replacement,
                } => {
                    let body = serde_json::to_vec(&ReplaceOrder {
                        order_id,
                        replacement: submit_request(replacement)?,
                    })?;
                    client
                        .request_json("POST", "/v1/replace", Some(&body))
                        .await?
                }
                ServerCommand::IntentExecute(args) => {
                    let body = serde_json::to_vec(&ExecuteIntent {
                        intent_id: args.intent_id,
                        current_quantity_mantissa: args.current_quantity_mantissa,
                        target_quantity_mantissa: args.target_quantity_mantissa,
                        quantity_scale: args.quantity_scale,
                        order_id: args.order_id,
                        account_id: args.account_id,
                        segment_key: args.segment_key,
                        instrument_id: args.instrument_id,
                        market_id: args.market_id,
                        limit_price_mantissa: args.limit_price_mantissa,
                        limit_price_scale: args.limit_price_scale,
                    })?;
                    client
                        .request_json("POST", "/v1/intents/execute", Some(&body))
                        .await?
                }
                ServerCommand::Fill(args) => {
                    let body = serde_json::to_vec(&ExecutionFillReport {
                        fill_id: args.fill_id,
                        order_id: args.order_id,
                        quantity_mantissa: args.quantity_mantissa,
                        quantity_scale: args.quantity_scale,
                        price_mantissa: args.price_mantissa,
                        price_scale: args.price_scale,
                        fee_mantissa: args.fee_mantissa,
                        fee_scale: args.fee_scale,
                        occurred_at_unix_nanos: args.occurred_at_unix_nanos,
                    })?;
                    client.request_json("POST", "/v1/fill", Some(&body)).await?
                }
                ServerCommand::Stop => client.request_json("POST", "/v1/stop", None).await?,
            };
            print_json(value);
        }
        command => run_direct_with_options(
            &workspace,
            command,
            Some(args.connection.connection_options(&workspace)?),
            args.confirm_live,
        )?,
    }
    Ok(())
}

#[derive(Debug, Parser)]
#[command(
    name = "kairos-execution-cli",
    about = "One-shot execution commands and server control"
)]
struct Cli {
    #[arg(long)]
    workspace: String,
    #[arg(long, global = true, value_parser = ["text", "json"])]
    output: Option<String>,
    #[arg(long, global = true)]
    confirm_live: bool,
    #[command(flatten)]
    connection: ConnectionArgs,
    #[command(subcommand)]
    command: Command,
}

#[derive(Clone, Debug, Args)]
struct ConnectionArgs {
    #[arg(long, default_value = "simulated")]
    provider: String,
    #[arg(long, default_value = "spot")]
    product: String,
    #[arg(long, env = "BINANCE_API_KEY", default_value = "")]
    api_key: String,
    #[arg(long, env = "BINANCE_API_SECRET", default_value = "")]
    secret: String,
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
}

impl ConnectionArgs {
    fn connection_options(
        &self,
        workspace: &Workspace,
    ) -> Result<ExecutionConnectionOptions, Box<dyn std::error::Error>> {
        let stored =
            load_workspace_credential(workspace, &self.provider, self.credential_id.as_deref())?;
        Ok(ExecutionConnectionOptions {
            provider: self.provider.clone(),
            product: self.product.clone(),
            api_key: if self.api_key.is_empty() {
                stored
                    .as_ref()
                    .map(|value| value.api_key.clone())
                    .unwrap_or_default()
            } else {
                self.api_key.clone()
            },
            secret: if self.secret.is_empty() {
                stored
                    .as_ref()
                    .map(|value| value.secret.clone())
                    .unwrap_or_default()
            } else {
                self.secret.clone()
            },
            passphrase: if self.passphrase.is_empty() {
                stored
                    .as_ref()
                    .map(|value| value.passphrase.clone())
                    .unwrap_or_default()
            } else {
                self.passphrase.clone()
            },
            base_url: self.base_url.clone(),
            host: self.host.clone(),
            port: self.port,
            client_id: self.client_id,
        })
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
        venue_order_id: Option<String>,
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
    IntentExecute(IntentArgs),
    Server {
        #[command(subcommand)]
        command: ServerCommand,
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
    quantity_mantissa: i64,
    #[arg(long, default_value_t = 0)]
    quantity_scale: u8,
    #[arg(long, default_value = "buy")]
    side: String,
    #[arg(long, default_value = "market")]
    order_type: String,
    #[arg(long)]
    limit_price_mantissa: Option<i64>,
    #[arg(long)]
    limit_price_scale: Option<u8>,
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
struct IntentArgs {
    #[arg(long)]
    intent_id: String,
    #[arg(long)]
    current_quantity_mantissa: i64,
    #[arg(long)]
    target_quantity_mantissa: i64,
    #[arg(long, default_value_t = 0)]
    quantity_scale: u8,
    #[arg(long)]
    order_id: String,
    #[arg(long)]
    account_id: String,
    #[arg(long, default_value = "spot")]
    segment_key: String,
    #[arg(long)]
    instrument_id: String,
    #[arg(long)]
    market_id: Option<String>,
    #[arg(long)]
    limit_price_mantissa: Option<i64>,
    #[arg(long)]
    limit_price_scale: Option<u8>,
}

#[derive(Clone, Debug, Subcommand)]
enum ServerCommand {
    Status,
    Snapshot,
    Orders {
        #[arg(long)]
        account_id: Option<String>,
    },
    OpenOrders {
        #[arg(long)]
        account_id: Option<String>,
    },
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
    StreamNext,
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
        venue_order_id: Option<String>,
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
    Submit(SubmitArgs),
    Cancel {
        #[arg(long)]
        order_id: String,
        #[arg(long, default_value = "server cancel")]
        reason: String,
    },
    Replace {
        #[arg(long)]
        order_id: String,
        #[command(flatten)]
        replacement: SubmitArgs,
    },
    IntentExecute(IntentArgs),
    Fill(FillArgs),
    Stop,
}

#[derive(Clone, Debug, Args)]
struct FillArgs {
    #[arg(long)]
    fill_id: String,
    #[arg(long)]
    order_id: String,
    #[arg(long)]
    quantity_mantissa: i64,
    #[arg(long, default_value_t = 0)]
    quantity_scale: u8,
    #[arg(long)]
    price_mantissa: i64,
    #[arg(long, default_value_t = 0)]
    price_scale: u8,
    #[arg(long, default_value_t = 0)]
    fee_mantissa: i64,
    #[arg(long, default_value_t = 0)]
    fee_scale: u8,
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
    let path = workspace.child(&["state", "execution", "execution-state.json"])?;
    let mut application = ExecutionApplication::with_dependencies_and_query_and_stream(
        "execution",
        Some(compose_order_entry(&options)?),
        compose_order_query(&options)?,
        compose_execution_stream(&options)?,
        Some(Box::new(FileExecutionStore::new(path))),
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
                symbol,
                ..Default::default()
            })?)?
        }
        Command::RemoteHistory { symbol, limit } => {
            serde_json::to_value(application.remote_history(RemoteOrderQuery {
                symbol,
                limit,
                ..Default::default()
            })?)?
        }
        Command::RemoteInspect { order_id } => {
            serde_json::to_value(application.remote_detail(RemoteOrderQuery {
                order_id: Some(order_id),
                ..Default::default()
            })?)?
        }
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
            venue_order_id,
            status,
            limit,
        } => serde_json::to_value(application.audit_events(ExecutionAuditQuery {
            order_id,
            venue_order_id,
            status,
            limit,
            ..Default::default()
        })?)?,
        Command::Journal { order_id } => {
            serde_json::to_value(application.audit_events(ExecutionAuditQuery {
                order_id: Some(order_id),
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
                fill_id: args.fill_id,
                order_id: args.order_id,
                quantity_mantissa: args.quantity_mantissa,
                quantity_scale: args.quantity_scale,
                price_mantissa: args.price_mantissa,
                price_scale: args.price_scale,
                fee_mantissa: args.fee_mantissa,
                fee_scale: args.fee_scale,
                occurred_at_unix_nanos: args.occurred_at_unix_nanos,
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
            serde_json::to_value(application.cancel(CancelOrder { order_id, reason })?)?
        }
        Command::Replace {
            order_id,
            replacement,
        } => serde_json::to_value(application.replace(ReplaceOrder {
            order_id,
            replacement: submit_request(replacement)?,
        })?)?,
        Command::IntentExecute(args) => {
            serde_json::to_value(application.execute_intent(ExecuteIntent {
                intent_id: args.intent_id,
                current_quantity_mantissa: args.current_quantity_mantissa,
                target_quantity_mantissa: args.target_quantity_mantissa,
                quantity_scale: args.quantity_scale,
                order_id: args.order_id,
                account_id: args.account_id,
                segment_key: args.segment_key,
                instrument_id: args.instrument_id,
                market_id: args.market_id,
                limit_price_mantissa: args.limit_price_mantissa,
                limit_price_scale: args.limit_price_scale,
            })?)?
        }
        Command::Server { .. } => unreachable!(),
    };
    print_json(value);
    Ok(())
}

fn remote_query_string(
    symbol: Option<String>,
    limit: Option<u32>,
    order_id: Option<String>,
) -> String {
    let mut values = Vec::new();
    if let Some(value) = symbol {
        values.push(format!("symbol={value}"));
    }
    if let Some(value) = limit {
        values.push(format!("limit={value}"));
    }
    if let Some(value) = order_id {
        values.push(format!("order_id={value}"));
    }
    if values.is_empty() {
        String::new()
    } else {
        format!("?{}", values.join("&"))
    }
}

fn audit_query_string(
    order_id: Option<String>,
    venue_order_id: Option<String>,
    status: Option<String>,
    limit: Option<u32>,
) -> String {
    let mut values = Vec::new();
    if let Some(value) = order_id {
        values.push(format!("order_id={value}"));
    }
    if let Some(value) = venue_order_id {
        values.push(format!("venue_order_id={value}"));
    }
    if let Some(value) = status {
        values.push(format!("status={value}"));
    }
    if let Some(value) = limit {
        values.push(format!("limit={value}"));
    }
    if values.is_empty() {
        String::new()
    } else {
        format!("?{}", values.join("&"))
    }
}

fn submit_request(args: SubmitArgs) -> Result<SubmitOrder, Box<dyn std::error::Error>> {
    Ok(SubmitOrder {
        order_id: args.order_id,
        intent_id: args.intent_id,
        account_id: args.account_id,
        segment_key: args.segment_key,
        instrument_id: args.instrument_id,
        market_id: args.market_id,
        side: parse_side(&args.side)?,
        order_type: parse_order_type(&args.order_type)?,
        quantity_mantissa: args.quantity_mantissa,
        quantity_scale: args.quantity_scale,
        limit_price_mantissa: args.limit_price_mantissa,
        limit_price_scale: args.limit_price_scale,
        options: ExecutionOrderOptions {
            time_in_force: args.time_in_force,
            reduce_only: args.reduce_only,
            post_only: args.post_only,
            position_side: args.position_side,
            quote_asset: args.quote_asset,
            wallet_type: args.wallet_type,
            trading_session: args.trading_session,
            tokenize: args.tokenize,
        },
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
