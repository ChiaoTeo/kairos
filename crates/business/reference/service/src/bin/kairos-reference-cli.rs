//! One-shot Reference verification CLI.
//!
//! Every invocation constructs the application, performs one use case, writes
//! one JSON value to stdout, and exits. It never starts or discovers a server.

use clap::{Args, Parser, Subcommand};
use kairos_domain_types::{AssetId, Exchange, InstrumentId, ListingId, Symbol, UnixNanos};
use kairos_reference::application::{ReferenceKind, ReferenceQuery};
use kairos_reference::composition::{
    build_application, ensure_database_parent, ReferenceCompositionConfig, ReferenceEventWriter,
};
use kairos_reference::domain::{Asset, Instrument, Listing};
use kairos_workspace::cli::{render, OutputFormat};
use kairos_workspace::workspace::Workspace;
use serde_json::{json, Value};
use std::str::FromStr;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Cli::parse();
    let workspace = Workspace::open(&args.workspace)?;
    let database = workspace.child(&["reference", "reference.sqlite"])?;
    ensure_database_parent(&database)?;
    let config = ReferenceCompositionConfig {
        workspace: Some(workspace.root().to_path_buf()),
        database,
        aeron_dir: args.aeron_dir,
        aeron_channel: args.aeron_channel,
        reference_changes_stream: args.reference_changes_stream,
    };

    let mut composition = build_application(&config, args.command.requires_publication())?;
    let value = execute(
        &mut composition.application,
        composition.event_writer.as_mut(),
        args.command,
    )?;
    let output = args.output.unwrap_or_else(|| {
        workspace
            .cli_format()
            .parse()
            .expect("workspace output format validated when opened")
    });
    println!("{}", render(&value, output));
    Ok(())
}

fn execute(
    application: &mut kairos_reference::ReferenceApplication,
    writer: Option<&mut ReferenceEventWriter>,
    command: Command,
) -> Result<Value, Box<dyn std::error::Error>> {
    let value = match command {
        Command::Status => json!({
            "status": "ready",
            "actor_id": application.actor_id(),
            "source_id": application.source_id(),
            "generation": application.catalog().generation,
            "event_sequence": application.catalog().event_sequence,
            "entities": application.catalog().entities.len(),
            "assets": application.catalog().assets.len(),
            "instruments": application.catalog().instruments.len(),
            "listings": application.catalog().listings.len(),
            "market_count": application.catalog().markets.len(),
            "active_markets": application.catalog().active_market_count(),
            "events": application.catalog().lifecycle_events.len(),
        }),
        Command::Snapshot => json!({
            "actor_id": application.actor_id(),
            "generation": application.catalog().generation,
            "event_sequence": application.catalog().event_sequence,
            "catalog": application.catalog(),
        }),
        Command::Refresh | Command::Sync => {
            let result = application.refresh()?;
            publish_pending(writer, application)?;
            json!({
                "generation": result.generation,
                "event_sequence": result.event_sequence,
                "events": result.events.len(),
            })
        }
        Command::Publish => {
            publish_pending(writer, application)?;
            json!({ "generation": application.catalog().generation })
        }
        Command::Assets { command } => {
            let publishes = matches!(&command, AssetCommand::Add(_));
            let value = assets(application, command)?;
            if publishes {
                publish_pending(writer, application)?;
            }
            value
        }
        Command::Instruments { command } => match command {
            InstrumentCommand::Add(args) => {
                let generation = application.upsert_instrument(Instrument {
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
                })?;
                publish_pending(writer, application)?;
                json!({"generation": generation})
            }
        },
        Command::Listings { command } => match command {
            ListingCommand::Add(args) => {
                let generation = application.upsert_listing(Listing {
                    source_id: None,
                    listing_id: ListingId::try_from(args.listing_id)?,
                    instrument_id: InstrumentId::try_from(args.instrument_id)?,
                    exchange_id: Exchange::new(args.exchange_id).expect("valid exchange id"),
                    exchange_symbol: Symbol::new(args.exchange_symbol)?,
                    status: args.status.into(),
                    effective_from_unix_nanos: args.effective_from_unix_nanos.into(),
                    effective_to_unix_nanos: args.effective_to_unix_nanos.map(UnixNanos::from),
                })?;
                publish_pending(writer, application)?;
                json!({"generation": generation})
            }
        },
        Command::Participants { command } => participants(application, command),
        Command::Markets { command } => markets(application, command)?,
        Command::Events(args) => match args.action {
            Some(EventAction::Sync(sync)) => {
                let result = application.refresh()?;
                publish_pending(writer, application)?;
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
            None => query(application, ReferenceKind::Event, args.query.into_query())?,
        },
        Command::Query(args) => {
            let kind = args.kind();
            query(application, kind, args.into_query())?
        }
        Command::Search(args) => query(
            application,
            ReferenceKind::All,
            ReferenceQuery {
                text: Some(args.text),
                limit: Some(args.limit),
                ..ReferenceQuery::default()
            },
        )?,
        Command::Show { identifier } => serde_json::to_value(application.record(&identifier)?)?,
    };
    Ok(value)
}

fn publish(
    writer: Option<&mut ReferenceEventWriter>,
    application: &kairos_reference::ReferenceApplication,
    events: &[kairos_reference::domain::LifecycleEvent],
) -> Result<(), Box<dyn std::error::Error>> {
    let Some(writer) = writer else {
        return Err("reference publication is not configured for this command".into());
    };
    writer.publish(application.catalog(), events)?;
    Ok(())
}

fn publish_pending(
    writer: Option<&mut ReferenceEventWriter>,
    application: &mut kairos_reference::ReferenceApplication,
) -> Result<(), Box<dyn std::error::Error>> {
    let events = application.pending_events(256)?;
    publish(writer, application, &events)?;
    let event_ids = events
        .iter()
        .map(|event| event.event_id.clone())
        .collect::<Vec<_>>();
    application.acknowledge_published_events(&event_ids)?;
    Ok(())
}

fn assets(
    application: &mut kairos_reference::ReferenceApplication,
    command: AssetCommand,
) -> Result<Value, Box<dyn std::error::Error>> {
    match command {
        AssetCommand::Add(args) => {
            let generation = application.upsert_asset(Asset {
                asset_id: AssetId::try_from(args.asset_id)?,
                code: args.code,
                name: args.name,
                asset_class: args.asset_class,
                status: args.status.into(),
                ..Default::default()
            })?;
            Ok(json!({ "generation": generation }))
        }
        AssetCommand::List(args) => {
            let records = application.query(&ReferenceQuery {
                kind: ReferenceKind::Asset,
                text: args.query,
                status: args.status,
                active_only: args.active_only,
                limit: Some(args.limit),
                ..ReferenceQuery::default()
            });
            Ok(serde_json::to_value(
                records
                    .into_iter()
                    .filter_map(|record| match record {
                        kairos_reference::application::ReferenceRecord::Asset(value) => Some(value),
                        _ => None,
                    })
                    .collect::<Vec<_>>(),
            )?)
        }
        AssetCommand::Show { asset_id } => application
            .catalog()
            .assets
            .get(&asset_id)
            .map(|value| json!(value))
            .ok_or_else(|| format!("unknown asset identifier: {asset_id}").into()),
    }
}

fn participants(
    application: &kairos_reference::ReferenceApplication,
    command: ParticipantCommand,
) -> Value {
    let kind = match command {
        ParticipantCommand::Brokers => "broker",
        ParticipantCommand::Exchanges => "exchange",
        ParticipantCommand::Providers => "data_provider",
    };
    json!(application
        .catalog()
        .entities
        .values()
        .filter(|value| value.entity_type == kind)
        .collect::<Vec<_>>())
}

fn markets(
    application: &kairos_reference::ReferenceApplication,
    command: MarketCommand,
) -> Result<Value, Box<dyn std::error::Error>> {
    let args = match command {
        MarketCommand::List(args) | MarketCommand::Browse(args) => args,
        MarketCommand::Resolve(args) => {
            let market = application.resolve_market(&args.into_query())?;
            return Ok(json!(market));
        }
    };
    let limit = args.limit;
    let mut values = application.markets(&args.into_query());
    if let Some(limit) = limit {
        values.truncate(limit);
    }
    Ok(json!(values))
}

fn query(
    application: &kairos_reference::ReferenceApplication,
    kind: ReferenceKind,
    mut query: ReferenceQuery,
) -> Result<Value, Box<dyn std::error::Error>> {
    query.kind = kind;
    Ok(serde_json::to_value(application.query(&query))?)
}

impl MarketQueryArgs {
    fn into_query(self) -> kairos_reference::MarketQuery {
        kairos_reference::MarketQuery {
            market_id: self
                .market_id
                .map(|value| kairos_domain_types::MarketId::new(value).expect("valid market id")),
            exchange_id: self
                .exchange_id
                .or(self.exchange)
                .map(|value| Exchange::new(value).expect("valid exchange id")),
            market_type: self.market_type.or(self.market),
            asset_type: self.asset_type,
            source_symbol: self
                .symbol
                .map(|value| kairos_domain_types::Symbol::new(value).expect("valid symbol")),
            active_only: self.active_only,
            as_of_unix_nanos: None,
            status: self.status,
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
            "event" => ReferenceKind::Event,
            _ => ReferenceKind::All,
        }
    }
}
