//! One-shot Reference verification CLI.
//!
//! Every invocation constructs the application, performs one use case, writes
//! one JSON value to stdout, and exits. It never starts or discovers a server.

use clap::{Args, Parser, Subcommand};
use kairos_domain_types::{AssetId, Exchange, InstrumentId, ListingId, Symbol, UnixNanos};
use kairos_reference::application::{ReferenceKind, ReferenceQuery};
use kairos_reference::composition::{
    build_application, ensure_database_parent, prepare_massive_cash_dividends,
    prepare_massive_option_contract_snapshot, ComposedReferenceApplication,
    MassiveReferenceDatasetConfig, ReferenceCompositionConfig, ReferenceEventWriter,
};
use kairos_reference::domain::{Asset, Instrument, Listing};
use kairos_reference::{CashDividendDatasetRequest, OptionContractSnapshotRequest};
use kairos_reference_contract::{ReferenceCollection, ReferenceSqliteReader, SqliteMarketQuery};
use kairos_workspace::cli::{render, OutputFormat};
use kairos_workspace::workspace::Workspace;
use serde_json::{json, Value};
use std::io::Write;
use std::str::FromStr;

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Cli::parse();
    let workspace = Workspace::open(&args.workspace)?;
    let database = workspace.child(&["reference", "reference.sqlite"])?;
    ensure_database_parent(&database)?;
    let output = args.output.unwrap_or_else(|| {
        workspace
            .cli_format()
            .parse()
            .expect("workspace output format validated when opened")
    });
    if let Command::PrepareOptionContracts(command) = &args.command {
        let value = prepare_option_contracts(&workspace, command).await?;
        println!("{}", render(&value, output));
        return Ok(());
    }
    if let Command::PrepareDividends(command) = &args.command {
        let value = prepare_dividends(&workspace, command).await?;
        println!("{}", render(&value, output));
        return Ok(());
    }
    if !args.command.requires_publication() {
        let value = execute_read(&database, args.command)?;
        println!("{}", render(&value, output));
        return Ok(());
    }
    let config = ReferenceCompositionConfig {
        workspace: Some(workspace.root().to_path_buf()),
        database,
        aeron_dir: args.aeron_dir,
        aeron_channel: args.aeron_channel,
        reference_changes_stream: args.reference_changes_stream,
    };

    let mut composition = build_application(&config, args.command.requires_publication()).await?;
    let value = execute(
        &mut composition.application,
        composition.event_writer.as_mut(),
        args.command,
    )
    .await?;
    println!("{}", render(&value, output));
    Ok(())
}

fn execute_read(
    database: &std::path::Path,
    command: Command,
) -> Result<Value, Box<dyn std::error::Error>> {
    let reader = ReferenceSqliteReader::open(database)?;
    let watermark = reader.watermark()?;
    let value = match command {
        Command::Status | Command::Snapshot => json!({
            "status": "ready",
            "generation": watermark.generation,
            "event_sequence": watermark.event_sequence,
            "committed_at_unix_nanos": watermark.committed_at_unix_nanos,
            "counts": reader.stats()?,
            "note": "full catalog snapshots are retired; use bounded query commands",
        }),
        Command::Assets { command } => match command {
            AssetCommand::List(args) => {
                let mut values = reader.records(ReferenceCollection::Assets, args.limit)?;
                values.retain(|value| {
                    matches_json(value, args.query.as_deref())
                        && matches_field(value, "status", args.status.as_deref())
                        && (!args.active_only
                            || value.get("status").and_then(Value::as_str) == Some("active"))
                });
                json!(values)
            }
            AssetCommand::Show { asset_id } => reader
                .record(&asset_id)?
                .ok_or_else(|| format!("unknown asset identifier: {asset_id}"))?,
            AssetCommand::Add(_) => unreachable!("write command routed to the application"),
        },
        Command::Participants { command } => {
            let entity_type = match command {
                ParticipantCommand::Brokers => "broker",
                ParticipantCommand::Exchanges => "exchange",
                ParticipantCommand::Providers => "data_provider",
            };
            let mut values = reader.records(ReferenceCollection::Entities, 10_000)?;
            values.retain(|value| {
                value.get("entity_type").and_then(Value::as_str) == Some(entity_type)
            });
            json!(values)
        }
        Command::Markets { command } => {
            let (args, resolve) = match command {
                MarketCommand::List(args) | MarketCommand::Browse(args) => (args, false),
                MarketCommand::Resolve(args) => (args, true),
            };
            let market_id = args.market_id.clone();
            let values = if let Some(market_id) = market_id {
                reader.market(&market_id)?.into_iter().collect::<Vec<_>>()
            } else {
                reader.markets(&args.into_sqlite_query())?
            };
            if resolve {
                match values.as_slice() {
                    [market] => json!(market),
                    [] => return Err("reference market was not found".into()),
                    _ => return Err("reference market query is ambiguous".into()),
                }
            } else {
                json!(values)
            }
        }
        Command::Events(args) => {
            debug_assert!(args.action.is_none());
            let query = args.query;
            let from = query.sequence_from.unwrap_or(1).saturating_sub(1);
            let mut events = reader.changes_after(from, query.limit.unwrap_or(256))?;
            events.retain(|event| {
                query.sequence_to.is_none_or(|to| {
                    event
                        .event_id
                        .rsplit(':')
                        .next()
                        .and_then(|value| value.parse::<u64>().ok())
                        .is_some_and(|sequence| sequence <= to)
                })
            });
            json!(events)
        }
        Command::Query(args) => read_query(&reader, args.kind(), args.into_query())?,
        Command::Search(args) => {
            let query = ReferenceQuery {
                text: Some(args.text),
                limit: Some(args.limit),
                ..ReferenceQuery::default()
            };
            read_query(&reader, ReferenceKind::All, query)?
        }
        Command::Show { identifier } => reader
            .record(&identifier)?
            .ok_or_else(|| format!("unknown reference identifier: {identifier}"))?,
        Command::Refresh
        | Command::Sync
        | Command::Publish
        | Command::Instruments { .. }
        | Command::Listings { .. }
        | Command::PrepareOptionContracts(_)
        | Command::PrepareDividends(_) => {
            unreachable!("write or acquisition command routed to its application")
        }
    };
    Ok(value)
}

fn read_query(
    reader: &ReferenceSqliteReader,
    kind: ReferenceKind,
    query: ReferenceQuery,
) -> Result<Value, Box<dyn std::error::Error>> {
    let collections: &[ReferenceCollection] = match kind {
        ReferenceKind::Entity => &[ReferenceCollection::Entities],
        ReferenceKind::Asset => &[ReferenceCollection::Assets],
        ReferenceKind::Instrument => &[ReferenceCollection::Instruments],
        ReferenceKind::Listing => &[ReferenceCollection::Listings],
        ReferenceKind::Market => &[ReferenceCollection::Markets],
        ReferenceKind::FinancialProduct => &[ReferenceCollection::FinancialProducts],
        ReferenceKind::ExecutionAccess => &[ReferenceCollection::ExecutionAccesses],
        ReferenceKind::MarketDataAccess => &[ReferenceCollection::MarketDataAccesses],
        ReferenceKind::Event => &[ReferenceCollection::LifecycleEvents],
        ReferenceKind::All => &[
            ReferenceCollection::Entities,
            ReferenceCollection::Assets,
            ReferenceCollection::Instruments,
            ReferenceCollection::Listings,
            ReferenceCollection::Markets,
            ReferenceCollection::FinancialProducts,
            ReferenceCollection::ExecutionAccesses,
            ReferenceCollection::MarketDataAccesses,
            ReferenceCollection::LifecycleEvents,
        ],
    };
    let limit = query.limit.unwrap_or(256).clamp(1, 10_000);
    let mut values = Vec::new();
    for collection in collections {
        let remaining = limit.saturating_sub(values.len());
        if remaining == 0 {
            break;
        }
        let mut records = reader.records(*collection, remaining)?;
        records.retain(|value| {
            matches_json(value, query.text.as_deref())
                && matches_field(value, "status", query.status.as_deref())
                && matches_field(
                    value,
                    "exchange_id",
                    query.exchange_id.as_ref().map(|value| value.as_str()),
                )
                && matches_field(value, "market_type", query.market_type.as_deref())
                && matches_field(
                    value,
                    "underlying_instrument_id",
                    query.underlying_instrument_id.as_deref(),
                )
                && (!query.active_only
                    || value.get("status").and_then(Value::as_str) == Some("active"))
        });
        values.extend(records);
    }
    values.truncate(limit);
    Ok(json!(values))
}

fn matches_json(value: &Value, text: Option<&str>) -> bool {
    text.is_none_or(|text| {
        value
            .to_string()
            .to_ascii_lowercase()
            .contains(&text.to_ascii_lowercase())
    })
}

fn matches_field(value: &Value, field: &str, expected: Option<&str>) -> bool {
    expected.is_none_or(|expected| value.get(field).and_then(Value::as_str) == Some(expected))
}

async fn execute(
    application: &mut ComposedReferenceApplication,
    writer: Option<&mut ReferenceEventWriter>,
    command: Command,
) -> Result<Value, Box<dyn std::error::Error>> {
    let value = match command {
        Command::Status | Command::Snapshot => unreachable!("read command routed to SQLite"),
        Command::Refresh | Command::Sync => {
            let result = application.refresh().await?;
            publish_pending(writer, application).await?;
            json!({
                "generation": result.generation,
                "event_sequence": result.event_sequence,
                "events": result.change_count,
            })
        }
        Command::Publish => {
            publish_pending(writer, application).await?;
            json!({ "generation": application.generation() })
        }
        Command::Assets { command } => {
            let publishes = matches!(&command, AssetCommand::Add(_));
            let value = assets(application, command).await?;
            if publishes {
                publish_pending(writer, application).await?;
            }
            value
        }
        Command::Instruments { command } => match command {
            InstrumentCommand::Add(args) => {
                let generation = application
                    .upsert_instrument(Instrument {
                        instrument_id: InstrumentId::try_from(args.instrument_id)?,
                        symbol: Symbol::try_from(args.symbol)?,
                        name: args.name,
                        instrument_type: args.instrument_type,
                        product_family: args.product_family,
                        underlying_instrument_id: args
                            .underlying_instrument_id
                            .map(InstrumentId::try_from)
                            .transpose()?,
                        expiry_unix_nanos: args.expiry_unix_nanos.map(UnixNanos::from),
                        strike: args.strike,
                        option_right: args.option_right,
                        status: args.status.into(),
                        ..Default::default()
                    })
                    .await?;
                publish_pending(writer, application).await?;
                json!({"generation": generation})
            }
        },
        Command::Listings { command } => match command {
            ListingCommand::Add(args) => {
                let generation = application
                    .upsert_listing(Listing {
                        source_id: None,
                        listing_id: ListingId::try_from(args.listing_id)?,
                        instrument_id: InstrumentId::try_from(args.instrument_id)?,
                        exchange_id: Exchange::new(args.exchange_id).expect("valid exchange id"),
                        exchange_symbol: Symbol::new(args.exchange_symbol)?,
                        status: args.status.into(),
                        effective_from_unix_nanos: args.effective_from_unix_nanos.into(),
                        effective_to_unix_nanos: args.effective_to_unix_nanos.map(UnixNanos::from),
                    })
                    .await?;
                publish_pending(writer, application).await?;
                json!({"generation": generation})
            }
        },
        Command::Participants { .. } | Command::Markets { .. } => {
            unreachable!("read command routed to SQLite")
        }
        Command::Events(args) => match args.action {
            Some(EventAction::Sync(sync)) => {
                let result = application.refresh().await?;
                publish_pending(writer, application).await?;
                let ticker = sync.ticker.to_ascii_lowercase();
                let events = result
                    .events
                    .into_iter()
                    .filter(|event| {
                        event
                            .source_symbol
                            .as_deref()
                            .is_none_or(|symbol| symbol.to_ascii_lowercase() == ticker)
                            && sync.exchange_id.as_deref().is_none_or(|exchange| {
                                event
                                    .exchange_id
                                    .as_ref()
                                    .is_some_and(|value| value.as_str() == exchange)
                            })
                            && sync
                                .start_unix_nanos
                                .is_none_or(|start| event.event_time_unix_nanos >= start.into())
                            && sync
                                .end_unix_nanos
                                .is_none_or(|end| event.event_time_unix_nanos < end.into())
                    })
                    .take(sync.limit.unwrap_or(usize::MAX))
                    .collect::<Vec<_>>();
                json!({
                    "ticker": sync.ticker,
                    "generation": result.generation,
                    "event_sequence": result.event_sequence,
                    "events": events,
                })
            }
            None => unreachable!("read command routed to SQLite"),
        },
        Command::Query(_) | Command::Search(_) | Command::Show { .. } => {
            unreachable!("read command routed to SQLite")
        }
        Command::PrepareOptionContracts(_) | Command::PrepareDividends(_) => {
            unreachable!("acquisition command routed before mutable composition")
        }
    };
    Ok(value)
}

fn publish(
    writer: Option<&mut ReferenceEventWriter>,
    application: &ComposedReferenceApplication,
    events: &[kairos_reference::domain::LifecycleEvent],
) -> Result<(), Box<dyn std::error::Error>> {
    let Some(writer) = writer else {
        return Err("reference publication is not configured for this command".into());
    };
    writer.publish(
        application.generation(),
        application.event_sequence(),
        events,
    )?;
    Ok(())
}

async fn publish_pending(
    mut writer: Option<&mut ReferenceEventWriter>,
    application: &mut ComposedReferenceApplication,
) -> Result<(), Box<dyn std::error::Error>> {
    loop {
        let events = application.pending_events(256).await?;
        if events.is_empty() {
            break;
        }
        publish(writer.as_deref_mut(), application, &events)?;
        let event_ids = events
            .iter()
            .map(|event| event.event_id.clone())
            .collect::<Vec<_>>();
        application.acknowledge_published_events(&event_ids).await?;
    }
    Ok(())
}

async fn prepare_option_contracts(
    workspace: &Workspace,
    args: &PrepareOptionContractsArgs,
) -> Result<Value, Box<dyn std::error::Error>> {
    let endpoint = args
        .endpoint
        .clone()
        .or_else(|| {
            workspace
                .reference_config()
                .providers
                .get("massive")
                .and_then(|value| value.endpoint.clone())
        })
        .unwrap_or_else(|| kairos_reference::composition::default_endpoint("massive").into());
    let request = OptionContractSnapshotRequest {
        underlying: args.underlying.clone(),
        as_of: args.as_of.clone(),
        expiration_start: args.expiration_start.clone(),
        expiration_end: args.expiration_end.clone(),
        option_right: Some(args.option_right.clone()),
    };
    let result = prepare_massive_option_contract_snapshot(
        workspace,
        &MassiveReferenceDatasetConfig {
            credential_id: args.credential_id.clone(),
            endpoint,
        },
        &request,
    )
    .await?;
    if let Some(parent) = args.file.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let temporary = args.file.with_extension("jsonl.tmp");
    let mut output = std::fs::File::create(&temporary)?;
    for record in &result.records {
        serde_json::to_writer(&mut output, record)?;
        output.write_all(b"\n")?;
    }
    output.sync_all()?;
    std::fs::rename(&temporary, &args.file)?;
    Ok(json!({
        "status": "prepared",
        "snapshot_id": result.snapshot_id,
        "observed_at_unix_nanos": result.observed_at_unix_nanos,
        "record_count": result.records.len(),
        "file": args.file,
        "source": "massive",
        "credential_id": args.credential_id,
        "point_in_time": {
            "as_of": args.as_of,
            "expiration_start": args.expiration_start,
            "expiration_end": args.expiration_end,
            "option_right": args.option_right,
        },
    }))
}

async fn prepare_dividends(
    workspace: &Workspace,
    args: &PrepareDividendsArgs,
) -> Result<Value, Box<dyn std::error::Error>> {
    let endpoint = args
        .endpoint
        .clone()
        .or_else(|| {
            workspace
                .reference_config()
                .providers
                .get("massive")
                .and_then(|value| value.endpoint.clone())
        })
        .unwrap_or_else(|| kairos_reference::composition::default_endpoint("massive").into());
    let request = CashDividendDatasetRequest {
        ticker: args.ticker.clone(),
        start_date: args.start_date.clone(),
        end_date: args.end_date.clone(),
    };
    let result = prepare_massive_cash_dividends(
        workspace,
        &MassiveReferenceDatasetConfig {
            credential_id: args.credential_id.clone(),
            endpoint,
        },
        &request,
    )
    .await?;
    write_json_lines_atomically(&args.file, &result.records)?;
    Ok(json!({
        "status": "prepared",
        "record_count": result.records.len(),
        "file": args.file,
        "source": "massive",
        "credential_id": args.credential_id,
        "coverage": {
            "ticker": args.ticker,
            "start_date": args.start_date,
            "end_date": args.end_date,
        },
    }))
}

fn write_json_lines_atomically<T: serde::Serialize>(
    file: &std::path::Path,
    records: &[T],
) -> Result<(), Box<dyn std::error::Error>> {
    if let Some(parent) = file.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let temporary = file.with_extension("jsonl.tmp");
    let mut output = std::fs::File::create(&temporary)?;
    for record in records {
        serde_json::to_writer(&mut output, record)?;
        output.write_all(b"\n")?;
    }
    output.sync_all()?;
    std::fs::rename(&temporary, file)?;
    Ok(())
}

async fn assets(
    application: &mut ComposedReferenceApplication,
    command: AssetCommand,
) -> Result<Value, Box<dyn std::error::Error>> {
    match command {
        AssetCommand::Add(args) => {
            let generation = application
                .upsert_asset(Asset {
                    asset_id: AssetId::try_from(args.asset_id)?,
                    code: args.code,
                    name: args.name,
                    asset_class: args.asset_class,
                    status: args.status.into(),
                    ..Default::default()
                })
                .await?;
            Ok(json!({ "generation": generation }))
        }
        AssetCommand::List(_) | AssetCommand::Show { .. } => {
            unreachable!("read command routed to SQLite")
        }
    }
}

impl MarketQueryArgs {
    fn into_sqlite_query(self) -> SqliteMarketQuery {
        let mut statuses = self.status.into_iter().collect::<Vec<_>>();
        if self.active_only && statuses.is_empty() {
            statuses.push("active".into());
        }
        SqliteMarketQuery {
            source_symbol: self.symbol,
            exchange_id: self.exchange_id.or(self.exchange),
            market_type: self.market_type.or(self.market),
            asset_type: self.asset_type,
            statuses,
            limit: self.limit.unwrap_or(256),
            ..SqliteMarketQuery::default()
        }
    }
}

impl QueryArgs {
    fn into_query(self) -> ReferenceQuery {
        ReferenceQuery {
            text: self.text,
            exchange_id: self
                .exchange_id
                .map(|value| Exchange::new(value).expect("valid exchange id")),
            market_type: self.market_type,
            underlying_instrument_id: self.underlying_instrument_id,
            status: self.status,
            active_only: self.active_only,
            as_of_unix_nanos: self.as_of_unix_nanos.map(Into::into),
            sequence_from: self.sequence_from.map(Into::into),
            sequence_to: self.sequence_to.map(Into::into),
            event_time_from_unix_nanos: self.event_time_from_unix_nanos.map(Into::into),
            event_time_to_unix_nanos: self.event_time_to_unix_nanos.map(Into::into),
            limit: self.limit,
            ..ReferenceQuery::default()
        }
    }
}

impl Command {
    fn requires_publication(&self) -> bool {
        if matches!(
            self,
            Self::Events(EventArgs {
                action: Some(EventAction::Sync(_)),
                ..
            })
        ) {
            return true;
        }
        matches!(
            self,
            Self::Refresh
                | Self::Sync
                | Self::Publish
                | Self::Assets {
                    command: AssetCommand::Add(_)
                }
                | Self::Instruments {
                    command: InstrumentCommand::Add(_)
                }
                | Self::Listings {
                    command: ListingCommand::Add(_)
                }
        )
    }
}

#[derive(Debug, Parser)]
#[command(
    name = "kairos-reference-cli",
    about = "One-shot Reference verification CLI"
)]
struct Cli {
    #[arg(long)]
    workspace: std::path::PathBuf,
    #[arg(long, global = true, visible_alias = "format", value_parser = OutputFormat::from_str)]
    output: Option<OutputFormat>,
    #[arg(
        long = "aeron-channel",
        global = true,
        default_value = kairos_transport::DEFAULT_CHANNEL
    )]
    aeron_channel: String,
    #[arg(
        long = "reference-changes-stream",
        global = true,
        default_value_t = kairos_transport::stream_ids::REFERENCE_CHANGES,
        value_parser = clap::value_parser!(i32).range(1..)
    )]
    reference_changes_stream: i32,
    #[arg(long, global = true)]
    aeron_dir: Option<String>,
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    Status,
    Snapshot,
    Refresh,
    Sync,
    Publish,
    PrepareOptionContracts(PrepareOptionContractsArgs),
    PrepareDividends(PrepareDividendsArgs),
    Assets {
        #[command(subcommand)]
        command: AssetCommand,
    },
    Instruments {
        #[command(subcommand)]
        command: InstrumentCommand,
    },
    Listings {
        #[command(subcommand)]
        command: ListingCommand,
    },
    Participants {
        #[command(subcommand)]
        command: ParticipantCommand,
    },
    Markets {
        #[command(subcommand)]
        command: MarketCommand,
    },
    Events(EventArgs),
    Query(QueryArgs),
    Search(SearchArgs),
    Show {
        identifier: String,
    },
}

#[derive(Debug, Args)]
struct PrepareOptionContractsArgs {
    #[arg(long)]
    underlying: String,
    #[arg(long)]
    as_of: String,
    #[arg(long)]
    expiration_start: String,
    #[arg(long)]
    expiration_end: String,
    #[arg(long, default_value = "put")]
    option_right: String,
    #[arg(long, default_value = "massive-readonly")]
    credential_id: String,
    #[arg(long)]
    endpoint: Option<String>,
    #[arg(long)]
    file: std::path::PathBuf,
}

#[derive(Debug, Args)]
struct PrepareDividendsArgs {
    #[arg(long)]
    ticker: String,
    #[arg(long)]
    start_date: String,
    #[arg(long)]
    end_date: String,
    #[arg(long, default_value = "massive-readonly")]
    credential_id: String,
    #[arg(long)]
    endpoint: Option<String>,
    #[arg(long)]
    file: std::path::PathBuf,
}

#[derive(Debug, Subcommand)]
enum AssetCommand {
    Add(AddAssetArgs),
    List(AssetListArgs),
    Show { asset_id: String },
}

#[derive(Debug, Subcommand)]
enum InstrumentCommand {
    Add(AddInstrumentArgs),
}

#[derive(Debug, Args)]
struct AddInstrumentArgs {
    #[arg(long)]
    instrument_id: String,
    #[arg(long)]
    symbol: String,
    #[arg(long, default_value = "spot")]
    instrument_type: String,
    #[arg(long)]
    name: Option<String>,
    #[arg(long)]
    product_family: Option<String>,
    #[arg(long)]
    underlying_instrument_id: Option<String>,
    #[arg(long)]
    expiry_unix_nanos: Option<u64>,
    #[arg(long)]
    strike: Option<String>,
    #[arg(long)]
    option_right: Option<String>,
    #[arg(long, default_value = "active")]
    status: String,
}

#[derive(Debug, Subcommand)]
enum ListingCommand {
    Add(AddListingArgs),
}

#[derive(Debug, Args)]
struct AddListingArgs {
    #[arg(long)]
    listing_id: String,
    #[arg(long)]
    instrument_id: String,
    #[arg(long)]
    exchange_id: String,
    #[arg(long)]
    exchange_symbol: String,
    #[arg(long, default_value = "active")]
    status: String,
    #[arg(long, default_value_t = 0)]
    effective_from_unix_nanos: u64,
    #[arg(long)]
    effective_to_unix_nanos: Option<u64>,
}

#[derive(Debug, Args)]
struct AddAssetArgs {
    #[arg(long)]
    asset_id: String,
    #[arg(long)]
    code: String,
    #[arg(long, default_value = "currency")]
    asset_class: String,
    #[arg(long)]
    name: Option<String>,
    #[arg(long, default_value = "active")]
    status: String,
}

#[derive(Debug, Args)]
struct AssetListArgs {
    #[arg(long)]
    query: Option<String>,
    #[arg(long)]
    status: Option<String>,
    #[arg(long)]
    active_only: bool,
    #[arg(long)]
    limit: usize,
}

#[derive(Debug, Subcommand)]
enum ParticipantCommand {
    Brokers,
    Exchanges,
    Providers,
}

#[derive(Debug, Subcommand)]
enum MarketCommand {
    List(MarketQueryArgs),
    Browse(MarketQueryArgs),
    Resolve(MarketQueryArgs),
}

#[derive(Debug, Args)]
struct MarketQueryArgs {
    #[arg(long)]
    symbol: Option<String>,
    #[arg(long)]
    market_id: Option<String>,
    #[arg(long)]
    exchange_id: Option<String>,
    #[arg(long, visible_alias = "exchange")]
    exchange: Option<String>,
    #[arg(long)]
    market_type: Option<String>,
    #[arg(long)]
    asset_type: Option<String>,
    #[arg(long, visible_alias = "market")]
    market: Option<String>,
    #[arg(long)]
    status: Option<String>,
    #[arg(long)]
    limit: Option<usize>,
    #[arg(long)]
    active_only: bool,
}

#[derive(Debug, Args)]
struct QueryArgs {
    #[arg(long)]
    text: Option<String>,
    #[arg(long, default_value = "all")]
    kind: String,
    #[arg(long)]
    exchange_id: Option<String>,
    #[arg(long)]
    market_type: Option<String>,
    #[arg(long, visible_alias = "underlying")]
    underlying_instrument_id: Option<String>,
    #[arg(long)]
    status: Option<String>,
    #[arg(long)]
    active_only: bool,
    #[arg(long)]
    as_of_unix_nanos: Option<u64>,
    #[arg(long)]
    sequence_from: Option<u64>,
    #[arg(long)]
    sequence_to: Option<u64>,
    #[arg(long)]
    event_time_from_unix_nanos: Option<u64>,
    #[arg(long)]
    event_time_to_unix_nanos: Option<u64>,
    #[arg(long)]
    limit: Option<usize>,
}

#[derive(Debug, Args)]
struct EventArgs {
    #[command(subcommand)]
    action: Option<EventAction>,
    #[command(flatten)]
    query: QueryArgs,
}

#[derive(Debug, Subcommand)]
enum EventAction {
    Sync(EventSyncArgs),
}

#[derive(Debug, Args)]
struct EventSyncArgs {
    #[arg(long)]
    ticker: String,
    #[arg(long)]
    exchange_id: Option<String>,
    #[arg(long)]
    start_unix_nanos: Option<u64>,
    #[arg(long)]
    end_unix_nanos: Option<u64>,
    #[arg(long)]
    limit: Option<usize>,
}

#[derive(Debug, Args)]
struct SearchArgs {
    text: String,
    #[arg(long, default_value_t = 50)]
    limit: usize,
}

impl QueryArgs {
    fn kind(&self) -> ReferenceKind {
        match self.kind.to_ascii_lowercase().as_str() {
            "entity" => ReferenceKind::Entity,
            "asset" => ReferenceKind::Asset,
            "instrument" => ReferenceKind::Instrument,
            "listing" => ReferenceKind::Listing,
            "market" => ReferenceKind::Market,
            "financial-product" | "financial_product" => ReferenceKind::FinancialProduct,
            "execution-access" | "execution_access" | "access" => ReferenceKind::ExecutionAccess,
            "market-data-access" | "market_data_access" | "data-access" => {
                ReferenceKind::MarketDataAccess
            }
            "event" => ReferenceKind::Event,
            _ => ReferenceKind::All,
        }
    }
}
