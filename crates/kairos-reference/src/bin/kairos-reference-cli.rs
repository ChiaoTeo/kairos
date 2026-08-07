//! One-shot Reference verification CLI.
//!
//! Every invocation constructs the application, performs one use case, writes
//! one JSON value to stdout, and exits. It never starts or discovers a server.

use clap::{Args, Parser, Subcommand};
use kairos_reference::application::{ReferenceKind, ReferenceQuery};
use kairos_reference::composition::{
    build_application, ensure_database_parent, ReferenceCompositionConfig, ReferenceSnapshotWriter,
};
use kairos_reference::domain::Asset;
use kairos_workspace::workspace::Workspace;
use serde_json::{json, Value};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Cli::parse();
    let endpoint = match args.provider.as_str() {
        "hyperliquid" if args.endpoint == Cli::default_endpoint() => {
            "https://api.hyperliquid.xyz/info".to_string()
        }
        "massive" | "massive-options" | "massive-equity"
            if args.endpoint == Cli::default_endpoint() =>
        {
            "https://api.polygon.io".to_string()
        }
        _ => args.endpoint.clone(),
    };
    let workspace = Workspace::open(&args.workspace)?;
    let database = workspace.child(&["reference", "reference.sqlite"])?;
    ensure_database_parent(&database)?;
    let config = ReferenceCompositionConfig {
        workspace: Some(workspace.root().to_path_buf()),
        provider: args.provider,
        endpoint,
        database,
        api_key: args.api_key.unwrap_or_default(),
        binance_api_key: args.binance_api_key.unwrap_or_default(),
        secret: args.secret.unwrap_or_default(),
        underlying: args.underlying,
        aeron_dir: args.aeron_dir,
        channel: args.channel,
        catalog_stream: args.catalog_stream,
        markets_stream: args.markets_stream,
        lifecycle_stream: args.lifecycle_stream,
        changes_stream: args.changes_stream,
    };

    let mut composition = build_application(&config, args.command.requires_publication())?;
    let value = execute(
        &mut composition.application,
        composition.snapshot_writer.as_mut(),
        args.command,
    )?;
    println!("{}", serde_json::to_string_pretty(&value)?);
    Ok(())
}

fn execute(
    application: &mut kairos_reference::ReferenceApplication,
    writer: Option<&mut ReferenceSnapshotWriter>,
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
            publish(writer, application, &result.events)?;
            json!({
                "generation": result.generation,
                "event_sequence": result.event_sequence,
                "events": result.events.len(),
            })
        }
        Command::Publish => {
            publish(writer, application, &[])?;
            json!({ "generation": application.catalog().generation })
        }
        Command::Assets { command } => assets(application, command)?,
        Command::Participants { command } => participants(application, command),
        Command::Markets { command } => markets(application, command)?,
        Command::Events(args) => match args.action {
            Some(EventAction::Sync(sync)) => {
                let result = application.refresh()?;
                let ticker = sync.ticker.to_ascii_lowercase();
                let events = result
                    .events
                    .into_iter()
                    .filter(|event| {
                        event
                            .source_symbol
                            .as_deref()
                            .is_none_or(|symbol| symbol.to_ascii_lowercase() == ticker)
                            && sync
                                .venue_id
                                .as_deref()
                                .is_none_or(|venue| event.venue_id.as_deref() == Some(venue))
                            && sync
                                .start_unix_nanos
                                .is_none_or(|start| event.event_time_unix_nanos >= start)
                            && sync
                                .end_unix_nanos
                                .is_none_or(|end| event.event_time_unix_nanos < end)
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
    writer: Option<&mut ReferenceSnapshotWriter>,
    application: &kairos_reference::ReferenceApplication,
    events: &[kairos_reference::domain::LifecycleEvent],
) -> Result<(), Box<dyn std::error::Error>> {
    let Some(writer) = writer else {
        return Err("reference publication is not configured for this command".into());
    };
    writer.publish(application.catalog())?;
    writer.publish_change(application.catalog(), events)?;
    Ok(())
}

fn assets(
    application: &mut kairos_reference::ReferenceApplication,
    command: AssetCommand,
) -> Result<Value, Box<dyn std::error::Error>> {
    match command {
        AssetCommand::Add(args) => {
            let generation = application.upsert_asset(Asset {
                asset_id: args.asset_id,
                code: args.code,
                name: args.name,
                asset_class: args.asset_class,
                status: args.status,
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
        ParticipantCommand::Exchanges => "venue",
        ParticipantCommand::Providers => "provider",
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
            market_id: self.market_id,
            venue_id: self.venue_id.or(self.venue),
            market_type: self.market_type.or(self.market),
            asset_type: self.asset_type,
            source_symbol: self.symbol,
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
            venue_id: self.venue_id,
            market_type: self.market_type,
            status: self.status,
            active_only: self.active_only,
            as_of_unix_nanos: self.as_of_unix_nanos,
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
    #[arg(long, default_value = "binance-spot")]
    provider: String,
    #[arg(long, default_value = "https://api.binance.com/api/v3/exchangeInfo")]
    endpoint: String,
    #[arg(long, env = "MASSIVE_API_KEY")]
    api_key: Option<String>,
    #[arg(long, env = "BINANCE_API_KEY")]
    binance_api_key: Option<String>,
    #[arg(long, env = "BINANCE_API_SECRET")]
    secret: Option<String>,
    #[arg(long, default_value = "SPY", env = "MASSIVE_OPTION_UNDERLYING")]
    underlying: String,
    #[arg(long, default_value = "aeron:udp?endpoint=localhost:40123")]
    channel: String,
    #[arg(long, default_value_t = 1201)]
    catalog_stream: i32,
    #[arg(long, default_value_t = 1202)]
    markets_stream: i32,
    #[arg(long, default_value_t = 1203)]
    lifecycle_stream: i32,
    #[arg(long, default_value_t = 1204)]
    changes_stream: i32,
    #[arg(long)]
    aeron_dir: Option<String>,
    #[command(subcommand)]
    command: Command,
}

impl Cli {
    fn default_endpoint() -> String {
        "https://api.binance.com/api/v3/exchangeInfo".to_string()
    }
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
    venue_id: Option<String>,
    #[arg(long, visible_alias = "venue")]
    venue: Option<String>,
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
    venue_id: Option<String>,
    #[arg(long)]
    market_type: Option<String>,
    #[arg(long)]
    status: Option<String>,
    #[arg(long)]
    active_only: bool,
    #[arg(long)]
    as_of_unix_nanos: Option<u64>,
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
    venue_id: Option<String>,
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
