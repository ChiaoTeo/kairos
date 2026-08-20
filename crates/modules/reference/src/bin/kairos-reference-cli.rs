//! One-shot Reference verification CLI.
//!
//! Every invocation constructs the application, performs one use case, writes
//! one JSON value to stdout, and exits. It never starts or discovers a server.

use std::str::FromStr;

use clap::{Args, Parser, Subcommand};
use kairos_primitives::reference::{AssetId, Exchange, InstrumentId, ListingId, Symbol};
use kairos_primitives::time::UnixNanos;
use kairos_reference::application::{
    ReferenceKind, ReferenceQuery, UpsertAssetCommand, UpsertInstrumentCommand,
    UpsertListingCommand,
};
use kairos_reference::composition::{
    ComposedReferenceApplication, ReferenceCompositionConfig, ReferenceEventWriter,
    build_application, ensure_database_parent,
};
use kairos_reference_contract::{
    ReferenceCollection, ReferenceProjectionSnapshot, ReferenceSqliteReader,
};
use kairos_workspace::cli::{OutputFormat, render};
use kairos_workspace::workspace::Workspace;
use serde_json::{Value, json};

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Cli::parse();
    let workspace = Workspace::open(&args.workspace)?;
    let database = workspace.child(&["state", "reference", "reference.sqlite"])?;
    ensure_database_parent(&database)?;
    let output = args.output.unwrap_or_else(|| {
        workspace
            .cli_format()
            .parse()
            .expect("workspace output format validated when opened")
    });
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
    composition.activate_sources().await?;
    let (application, system, writer) = composition.split_mut();
    let value = execute(application, system, writer, args.command).await?;
    println!("{}", render(&value, output));
    Ok(())
}

fn execute_read(
    database: &std::path::Path,
    command: Command,
) -> Result<Value, Box<dyn std::error::Error>> {
    let reader = ReferenceSqliteReader::open(database)?;
    if matches!(command, Command::Status | Command::Snapshot) {
        let watermark = reader.watermark()?;
        let counts = reader.stats()?;
        return Ok(json!({
            "status": "ready",
            "generation": watermark.generation,
            "event_sequence": watermark.event_sequence,
            "committed_at_unix_nanos": watermark.committed_at_unix_nanos,
            "counts": counts,
            "note": "diagnostic query read from the Reference-owned catalog",
        }));
    }
    let snapshot = diagnostic_snapshot(&reader)?;
    let value = match command {
        Command::Status | Command::Snapshot => unreachable!("handled before catalog reads"),
        Command::Assets { command } => match command {
            AssetCommand::List(args) => {
                let mut values = json_records(&snapshot.assets)?;
                values.retain(|value| {
                    matches_json(value, args.query.as_deref())
                        && matches_field(value, "status", args.status.as_deref())
                        && (!args.active_only
                            || value.get("status").and_then(Value::as_str) == Some("active"))
                });
                values.truncate(args.limit);
                json!(values)
            },
            AssetCommand::Show { asset_id } => find_record(&snapshot, &asset_id)?
                .ok_or_else(|| format!("unknown asset identifier: {asset_id}"))?,
            AssetCommand::Add(_) => unreachable!("write command routed to the application"),
        },
        Command::Participants { command } => {
            let entity_type = match command {
                ParticipantCommand::Brokers => "broker",
                ParticipantCommand::Exchanges => "exchange",
                ParticipantCommand::Providers => "data_provider",
            };
            let mut values = json_records(&snapshot.entities)?;
            values.retain(|value| {
                value.get("entity_type").and_then(Value::as_str) == Some(entity_type)
            });
            json!(values)
        },
        Command::Markets { command } => {
            let (args, resolve) = match command {
                MarketCommand::List(args) | MarketCommand::Browse(args) => (args, false),
                MarketCommand::Resolve(args) => (args, true),
            };
            let market_id = args.market_id.clone();
            let limit = args.limit.unwrap_or(256);
            let mut values = json_records(&snapshot.markets)?;
            values.retain(|value| {
                market_id.as_deref().is_none_or(|expected| {
                    value.get("market_id").and_then(Value::as_str) == Some(expected)
                }) && args.symbol.as_deref().is_none_or(|expected| {
                    value.get("venue_symbol").and_then(Value::as_str) == Some(expected)
                }) && args
                    .exchange_id
                    .as_deref()
                    .or(args.exchange.as_deref())
                    .is_none_or(|expected| {
                        value.get("exchange_id").and_then(Value::as_str) == Some(expected)
                    })
                    && args
                        .instrument_kind
                        .as_deref()
                        .or(args.market.as_deref())
                        .is_none_or(|expected| {
                            value.get("instrument_kind").and_then(Value::as_str) == Some(expected)
                        })
                    && args.asset_type.as_deref().is_none_or(|expected| {
                        value.get("asset_type").and_then(Value::as_str) == Some(expected)
                    })
                    && args.status.as_deref().is_none_or(|expected| {
                        value.get("status").and_then(Value::as_str) == Some(expected)
                    })
                    && (!args.active_only
                        || value.get("status").and_then(Value::as_str) == Some("active"))
            });
            values.truncate(limit);
            if resolve {
                match values.as_slice() {
                    [market] => json!(market),
                    [] => return Err("reference market was not found".into()),
                    _ => return Err("reference market query is ambiguous".into()),
                }
            } else {
                json!(values)
            }
        },
        Command::Events(args) => {
            debug_assert!(args.action.is_none());
            let query = args.query;
            let from = query.sequence_from.unwrap_or(1).saturating_sub(1);
            let mut events = json_records(&snapshot.lifecycle_events)?;
            events.retain(|event| {
                let sequence = event
                    .get("event_id")
                    .and_then(Value::as_str)
                    .and_then(|value| value.rsplit(':').next())
                    .and_then(|value| value.parse::<u64>().ok());
                sequence.is_some_and(|sequence| sequence > from)
                    && query
                        .sequence_to
                        .is_none_or(|to| sequence.is_some_and(|sequence| sequence <= to))
            });
            events.truncate(query.limit.unwrap_or(256));
            json!(events)
        },
        Command::Query(args) => read_query(&snapshot, args.kind(), args.into_query())?,
        Command::Search(args) => {
            let query = ReferenceQuery {
                text: Some(args.text),
                limit: Some(args.limit),
                ..ReferenceQuery::default()
            };
            read_query(&snapshot, ReferenceKind::All, query)?
        },
        Command::Show { identifier } => find_record(&snapshot, &identifier)?
            .ok_or_else(|| format!("unknown reference identifier: {identifier}"))?,
        Command::Refresh
        | Command::Sync
        | Command::Publish
        | Command::Instruments { .. }
        | Command::Listings { .. } => {
            unreachable!("write or acquisition command routed to its application")
        },
    };
    Ok(value)
}

fn diagnostic_snapshot(
    reader: &ReferenceSqliteReader,
) -> Result<ReferenceProjectionSnapshot, Box<dyn std::error::Error>> {
    fn records<T: serde::de::DeserializeOwned>(
        reader: &ReferenceSqliteReader,
        collection: ReferenceCollection,
    ) -> Result<Vec<T>, Box<dyn std::error::Error>> {
        reader
            .records(collection, 10_000)?
            .into_iter()
            .map(|value| serde_json::from_value(value).map_err(Into::into))
            .collect()
    }

    let watermark = reader.watermark()?;
    Ok(ReferenceProjectionSnapshot {
        generation: watermark.generation,
        event_sequence: watermark.event_sequence,
        entities: records(reader, ReferenceCollection::Entities)?,
        assets: records(reader, ReferenceCollection::Assets)?,
        instruments: records(reader, ReferenceCollection::Instruments)?,
        listings: records(reader, ReferenceCollection::Listings)?,
        markets: records(reader, ReferenceCollection::Markets)?,
        lifecycle_events: records(reader, ReferenceCollection::LifecycleEvents)?,
        ..Default::default()
    })
}

fn read_query(
    snapshot: &ReferenceProjectionSnapshot,
    kind: ReferenceKind,
    query: ReferenceQuery,
) -> Result<Value, Box<dyn std::error::Error>> {
    let collections = snapshot_collections(snapshot, kind)?;
    let limit = query.limit.unwrap_or(256).clamp(1, 10_000);
    let mut values = Vec::new();
    for records in collections {
        let remaining = limit.saturating_sub(values.len());
        if remaining == 0 {
            break;
        }
        let mut records = records;
        records.retain(|value| {
            matches_json(value, query.text.as_deref())
                && matches_field(value, "status", query.status.as_deref())
                && matches_field(
                    value,
                    "exchange_id",
                    query.exchange_id.as_ref().map(|value| value.as_str()),
                )
                && matches_field(
                    value,
                    "instrument_kind",
                    query.instrument_kind.map(|value| value.as_str()),
                )
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

fn snapshot_collections(
    snapshot: &ReferenceProjectionSnapshot,
    kind: ReferenceKind,
) -> Result<Vec<Vec<Value>>, serde_json::Error> {
    let mut all = Vec::new();
    macro_rules! include {
        ($variant:ident, $field:ident) => {
            if matches!(kind, ReferenceKind::$variant | ReferenceKind::All) {
                all.push(json_records(&snapshot.$field)?);
            }
        };
    }
    include!(Entity, entities);
    include!(Asset, assets);
    include!(Instrument, instruments);
    include!(Listing, listings);
    include!(Market, markets);
    include!(Event, lifecycle_events);
    Ok(all)
}

fn json_records<T: serde::Serialize>(records: &[T]) -> Result<Vec<Value>, serde_json::Error> {
    records.iter().map(serde_json::to_value).collect()
}

fn find_record(
    snapshot: &ReferenceProjectionSnapshot,
    identifier: &str,
) -> Result<Option<Value>, serde_json::Error> {
    Ok(snapshot_collections(snapshot, ReferenceKind::All)?
        .into_iter()
        .flatten()
        .find(|value| {
            [
                "entity_id",
                "asset_id",
                "instrument_id",
                "listing_id",
                "market_id",
                "product_id",
                "access_id",
                "event_id",
            ]
            .into_iter()
            .any(|field| value.get(field).and_then(Value::as_str) == Some(identifier))
        }))
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
    system: &mut kairos_conflux::ConfluxSystem,
    writer: Option<&mut ReferenceEventWriter>,
    command: Command,
) -> Result<Value, Box<dyn std::error::Error>> {
    let value = match command {
        Command::Status | Command::Snapshot => unreachable!("read command routed to SQLite"),
        Command::Refresh | Command::Sync => {
            let result = application
                .refresh_with_connections(&mut system.connections())
                .await?;
            publish_pending(writer, application, system).await?;
            json!({
                "generation": result.generation,
                "event_sequence": result.event_sequence,
                "events": result.change_count,
            })
        },
        Command::Publish => {
            publish_pending(writer, application, system).await?;
            json!({ "generation": application.generation() })
        },
        Command::Assets { command } => {
            let publishes = matches!(&command, AssetCommand::Add(_));
            let value = assets(application, command).await?;
            if publishes {
                publish_pending(writer, application, system).await?;
            }
            value
        },
        Command::Instruments { command } => match command {
            InstrumentCommand::Add(args) => {
                let generation = application
                    .upsert_instrument(UpsertInstrumentCommand {
                        instrument_id: InstrumentId::try_from(args.instrument_id)?,
                        symbol: Symbol::try_from(args.symbol)?,
                        name: args.name,
                        instrument_type: args.instrument_type.parse()?,
                        underlying_instrument_id: args
                            .underlying_instrument_id
                            .map(InstrumentId::try_from)
                            .transpose()?,
                        expiry_unix_nanos: args.expiry_unix_nanos.map(UnixNanos::from),
                        strike: args.strike.map(|value| value.parse()).transpose()?,
                        option_right: args.option_right,
                        status: args.status.into(),
                        issuer_id: None,
                        share_class: None,
                        primary_currency_asset_id: None,
                    })
                    .await?;
                publish_pending(writer, application, system).await?;
                json!({"generation": generation})
            },
        },
        Command::Listings { command } => match command {
            ListingCommand::Add(args) => {
                let generation = application
                    .upsert_listing(UpsertListingCommand {
                        listing_id: ListingId::try_from(args.listing_id)?,
                        instrument_id: InstrumentId::try_from(args.instrument_id)?,
                        exchange_id: Exchange::new(args.exchange_id).expect("valid exchange id"),
                        exchange_symbol: Symbol::new(args.exchange_symbol)?,
                        status: args.status.into(),
                        effective_from_unix_nanos: args.effective_from_unix_nanos.into(),
                        effective_to_unix_nanos: args.effective_to_unix_nanos.map(UnixNanos::from),
                    })
                    .await?;
                publish_pending(writer, application, system).await?;
                json!({"generation": generation})
            },
        },
        Command::Participants { .. } | Command::Markets { .. } => {
            unreachable!("read command routed to SQLite")
        },
        Command::Events(args) => match args.action {
            Some(EventAction::Sync(sync)) => {
                let result = application
                    .refresh_with_connections(&mut system.connections())
                    .await?;
                publish_pending(writer, application, system).await?;
                let ticker = sync.ticker.to_ascii_lowercase();
                let events = result
                    .events
                    .into_iter()
                    .filter(|event| {
                        event
                            .venue_symbol
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
            },
            None => unreachable!("read command routed to SQLite"),
        },
        Command::Query(_) | Command::Search(_) | Command::Show { .. } => {
            unreachable!("read command routed to SQLite")
        },
    };
    Ok(value)
}

fn publish(
    writer: Option<&mut ReferenceEventWriter>,
    system: &mut kairos_conflux::ConfluxSystem,
    publications: &[kairos_reference::ReferencePublication],
) -> Result<(), Box<dyn std::error::Error>> {
    let Some(writer) = writer else {
        return Err("reference publication is not configured for this command".into());
    };
    writer.publish(system, publications)?;
    Ok(())
}

async fn publish_pending(
    mut writer: Option<&mut ReferenceEventWriter>,
    application: &mut ComposedReferenceApplication,
    system: &mut kairos_conflux::ConfluxSystem,
) -> Result<(), Box<dyn std::error::Error>> {
    loop {
        let publications = application.pending_publications(256).await?;
        if publications.is_empty() {
            break;
        }
        publish(writer.as_deref_mut(), system, &publications)?;
        let event_ids = publications
            .iter()
            .map(|event| event.event_id().to_owned())
            .collect::<Vec<_>>();
        application.acknowledge_publications(&event_ids).await?;
    }
    Ok(())
}

async fn assets(
    application: &mut ComposedReferenceApplication,
    command: AssetCommand,
) -> Result<Value, Box<dyn std::error::Error>> {
    match command {
        AssetCommand::Add(args) => {
            let generation = application
                .upsert_asset(UpsertAssetCommand {
                    asset_id: AssetId::try_from(args.asset_id)?,
                    code: Symbol::try_from(args.code)?,
                    name: args.name,
                    asset_class: args.asset_class.parse()?,
                    status: args.status.into(),
                })
                .await?;
            Ok(json!({ "generation": generation }))
        },
        AssetCommand::List(_) | AssetCommand::Show { .. } => {
            unreachable!("read command routed to SQLite")
        },
    }
}

impl QueryArgs {
    fn into_query(self) -> ReferenceQuery {
        ReferenceQuery {
            text: self.text,
            exchange_id: self
                .exchange_id
                .map(|value| Exchange::new(value).expect("valid exchange id")),
            instrument_kind: self.instrument_kind.as_deref().map(|value| {
                value
                    .parse()
                    .unwrap_or(kairos_primitives::reference::InstrumentKind::Unknown)
            }),
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
        default_value = kairos_conflux::DEFAULT_AERON_CHANNEL
    )]
    aeron_channel: String,
    #[arg(
        long = "reference-changes-stream",
        global = true,
        default_value_t = kairos_conflux::output_stream_ids::REFERENCE_CHANGES,
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
    instrument_kind: Option<String>,
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
    instrument_kind: Option<String>,
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
            "event" => ReferenceKind::Event,
            _ => ReferenceKind::All,
        }
    }
}
