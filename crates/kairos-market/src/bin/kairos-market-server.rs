use std::path::PathBuf;
use std::time::Duration;

use clap::Parser;

use kairos_integration::credentials::load_workspace_credential;
use kairos_market::composition::{
    binance_equity_rest_feed, binance_spot_rest_feed, binance_spot_websocket_feed, MarketActor,
    MmapMarketSnapshotPublisher, ReplayMarketFeed,
};
use kairos_market::{MarketApplication, MarketObservation, MarketProcess};
use kairos_protocol::InstanceIdentity;
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
    let transport_identity = instance
        .as_ref()
        .map(|value| InstanceIdentity::new(workspace.id(), value.launch_id(), value.instance_id()))
        .unwrap_or_default();
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
    let mut publisher = MmapMarketSnapshotPublisher::create_with_identity(
        &snapshot_path,
        slot_size,
        actor_id,
        "market.events",
        transport_identity.clone(),
    )?;

    if provider == "empty" {
        publisher.publish(&application.snapshot())?;
        if once {
            return Ok(());
        }
        return MarketProcess::new_with_identity(
            application,
            publisher,
            socket_path,
            event_socket_path,
            Duration::from_millis(refresh_ms),
            false,
            transport_identity.clone(),
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
        let replay = if let Some(instance) = &instance {
            ReplayMarketFeed::with_checkpoint(
                events,
                None,
                None,
                instance.market_state("cursor.json")?,
            )?
        } else {
            ReplayMarketFeed::new(events)
        };
        application.attach_feed(Box::new(replay));
        return MarketProcess::new_with_identity(
            application,
            publisher,
            socket_path,
            event_socket_path,
            Duration::from_millis(refresh_ms),
            true,
            transport_identity.clone(),
        )?
        .run()
        .await;
    }
    if provider == "binance-equity-rest" {
        let credential = load_workspace_credential(
            &workspace.child(&["credentials"])?,
            "binance",
            args.credential_id.as_deref(),
        )?
        .ok_or("Binance Equity Market requires a workspace credential")?;
        if credential.api_key.trim().is_empty() {
            return Err("Binance Equity Market credential has no API key".into());
        }
        let feed =
            binance_equity_rest_feed(credential.api_key, credential.secret, args.endpoint.clone())?;
        application.attach_feed(Box::new(feed));
    } else {
        if provider != "binance-spot-rest" && provider != "binance-spot-websocket" {
            return Err(format!("unsupported market provider: {provider}").into());
        }
        let feed = if provider == "binance-spot-websocket" {
            binance_spot_websocket_feed(args.endpoint.clone())?
        } else {
            binance_spot_rest_feed(args.endpoint.clone())?
        };
        application.attach_feed(Box::new(feed));
    }
    if once {
        application.poll_feed()?;
        publisher.publish(&application.snapshot())?;
        return Ok(());
    }
    MarketProcess::new_with_identity(
        application,
        publisher,
        socket_path,
        event_socket_path,
        Duration::from_millis(refresh_ms),
        true,
        transport_identity,
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
    #[arg(long, visible_alias = "launch-mode", default_value = "paper")]
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
    #[arg(long, default_value = "https://api.binance.com")]
    endpoint: String,
    #[arg(long)]
    credential_id: Option<String>,
}
