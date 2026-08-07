use std::path::PathBuf;
use std::time::Duration;

use clap::Parser;

use kairos_market::composition::{binance_spot_rest_feed, binance_spot_websocket_feed};
use kairos_market::{
    MarketActor, MarketApplication, MarketDescriptor, MarketObservation, MarketProcess,
    MmapMarketSnapshotPublisher, ReplayMarketFeed, SubscriptionId,
};
use kairos_workspace::workspace::Workspace;

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let actor_id = args.actor_id;
    let workspace = Workspace::open(args.workspace)?;
    let instance = args
        .launch_id
        .as_deref()
        .map(|launch_id| workspace.instance(&args.launch_mode, launch_id, &args.instance_id))
        .transpose()?;
    if let Some(instance) = &instance {
        instance.prepare()?;
    }
    let snapshot_path = instance
        .as_ref()
        .map(|value| value.snapshot(&["market", "market.snapshot"]))
        .transpose()?
        .unwrap_or(workspace.child(&["state", "market", "market.snapshot"])?);
    let socket_path = instance
        .as_ref()
        .map(|value| value.socket("market"))
        .transpose()?
        .unwrap_or(workspace.process_socket("market")?);
    let event_socket_path = instance
        .as_ref()
        .map(|value| value.socket("market-events"))
        .transpose()?
        .unwrap_or(workspace.process_socket("market-events")?);
    let slot_size = args.slot_size;
    let provider = args.provider;
    let once = args.once;
    let refresh_ms = args.refresh_ms;
    if slot_size == 0 || refresh_ms == 0 {
        return Err("slot_size and refresh_ms must be positive".into());
    }

    if let Some(parent) = snapshot_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let actor = MarketActor::new(actor_id.clone(), 10_000)?;
    let mut application = MarketApplication::new(actor);
    let mut publisher =
        MmapMarketSnapshotPublisher::create(&snapshot_path, slot_size, actor_id, "market.events")?;

    if provider == "empty" {
        publisher.publish(&application.snapshot())?;
        if once {
            return Ok(());
        }
        return MarketProcess::new(
            application,
            publisher,
            socket_path,
            event_socket_path,
            Duration::from_millis(refresh_ms),
            false,
        )?
        .run()
        .await;
    }
    if provider == "replay" {
        let replay_file = args
            .replay_file
            .clone()
            .ok_or("replay market provider requires --replay-file")?;
        let events = std::fs::read_to_string(replay_file)?
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(serde_json::from_str::<MarketObservation>)
            .collect::<Result<Vec<_>, _>>()?;
        let descriptor = MarketDescriptor::new(
            args.market_id,
            args.instrument_id,
            args.venue_id,
            "spot",
            args.source_symbol,
        )?;
        application.attach_feed(Box::new(ReplayMarketFeed::new(events)));
        application.subscribe_static(
            SubscriptionId::new("market-process-replay")?,
            "market-process",
            descriptor,
        )?;
        return MarketProcess::new(
            application,
            publisher,
            socket_path,
            event_socket_path,
            Duration::from_millis(refresh_ms),
            true,
        )?
        .run()
        .await;
    }
    if provider != "binance-spot-rest" && provider != "binance-spot-websocket" {
        return Err(format!("unsupported market provider: {provider}").into());
    }

    let descriptor = MarketDescriptor::new(
        args.market_id,
        args.instrument_id,
        args.venue_id,
        "spot",
        args.source_symbol,
    )?;
    let feed = if provider == "binance-spot-websocket" {
        binance_spot_websocket_feed(args.endpoint.clone())?
    } else {
        binance_spot_rest_feed(args.endpoint.clone())?
    };
    application.attach_feed(Box::new(feed));
    application.subscribe_static(
        SubscriptionId::new("market-process")?,
        "market-process",
        descriptor,
    )?;
    if once {
        application.poll_feed()?;
        publisher.publish(&application.snapshot())?;
        return Ok(());
    }
    MarketProcess::new(
        application,
        publisher,
        socket_path,
        event_socket_path,
        Duration::from_millis(refresh_ms),
        true,
    )?
    .run()
    .await
}

#[derive(Debug, Parser)]
#[command(name = "kairos-market", about = "Run the Market actor process")]
struct Args {
    #[arg(long, default_value = "market-actor")]
    actor_id: String,
    #[arg(long)]
    workspace: PathBuf,
    #[arg(long, default_value = "paper")]
    launch_mode: String,
    #[arg(long)]
    launch_id: Option<String>,
    #[arg(long, default_value = "default")]
    instance_id: String,
    #[arg(long, default_value_t = 4_194_304)]
    slot_size: usize,
    #[arg(long, default_value = "empty")]
    provider: String,
    #[arg(long)]
    replay_file: Option<PathBuf>,
    #[arg(long, default_value_t = false)]
    once: bool,
    #[arg(long, default_value_t = 1_000)]
    refresh_ms: u64,
    #[arg(long, default_value = "market:binance:spot:BTCUSDT")]
    market_id: String,
    #[arg(long, default_value = "instrument:binance:spot:BTCUSDT")]
    instrument_id: String,
    #[arg(long, default_value = "binance")]
    venue_id: String,
    #[arg(long, default_value = "BTCUSDT")]
    source_symbol: String,
    #[arg(long, default_value = "https://api.binance.com")]
    endpoint: String,
}
