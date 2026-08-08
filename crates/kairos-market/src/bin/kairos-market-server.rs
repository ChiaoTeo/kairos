use std::path::PathBuf;
use std::time::Duration;

use clap::Parser;
use kairos_integration::domain::ProductFamily;

use kairos_integration::credentials::load_workspace_credential;
use kairos_market::composition::{
    binance_derivatives_rest_feed, binance_equity_rest_feed, binance_spot_rest_feed,
    binance_spot_websocket_feed, default_endpoint, default_market_feed,
    massive_market_websocket_feed, okx_market_rest_feed, CompositeMarketFeed, MarketActor,
    MarketFeedFactory, MarketRoute, MmapMarketSnapshotPublisher, ReplayMarketFeed,
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
        .map(|value| value.service_snapshot("market"))
        .transpose()?
        .unwrap_or(workspace.service_snapshot("market")?);
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
    let endpoint = args
        .endpoint
        .unwrap_or_else(|| default_endpoint(&provider).to_owned());
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
    if provider == "workspace" {
        let feed = workspace_market_feed(&workspace)?;
        application.attach_feed(Box::new(feed));
        return MarketProcess::new_with_identity(
            application,
            publisher,
            socket_path,
            event_socket_path,
            Duration::from_millis(refresh_ms),
            true,
            transport_identity.clone(),
        )?
        .with_reference_socket(workspace.process_socket("reference")?)
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
            binance_equity_rest_feed(credential.api_key, credential.secret, endpoint.clone())?;
        application.attach_feed(Box::new(feed));
    } else {
        if provider != "binance-spot-rest" && provider != "binance-spot-websocket" {
            return Err(format!("unsupported market provider: {provider}").into());
        }
        let feed = if provider == "binance-spot-websocket" {
            binance_spot_websocket_feed(endpoint.clone())?
        } else {
            binance_spot_rest_feed(endpoint.clone())?
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
    #[arg(long)]
    endpoint: Option<String>,
    #[arg(long)]
    credential_id: Option<String>,
}

fn workspace_market_feed(
    workspace: &Workspace,
) -> Result<CompositeMarketFeed, Box<dyn std::error::Error>> {
    let manifest = std::fs::read_to_string(workspace.root().join("kairos.toml"))
        .or_else(|_| std::fs::read_to_string(workspace.root().join("workspace.toml")))?;
    let value: toml::Value = toml::from_str(&manifest)?;
    let connections = value
        .get("market")
        .and_then(|market| market.get("connections"))
        .and_then(toml::Value::as_table);
    let Some(connections) = connections else {
        return Ok(default_market_feed()?);
    };
    let mut factories: std::collections::BTreeMap<MarketRoute, MarketFeedFactory> =
        std::collections::BTreeMap::new();
    for (connection_id, raw) in connections {
        let table = raw
            .as_table()
            .ok_or_else(|| format!("market connection {connection_id} must be a table"))?;
        let provider = table
            .get("provider")
            .and_then(toml::Value::as_str)
            .ok_or_else(|| format!("market connection {connection_id} requires provider"))?
            .to_owned();
        let venue = table
            .get("venue")
            .and_then(toml::Value::as_str)
            .unwrap_or_else(|| provider.split('-').next().unwrap_or("unknown"))
            .to_owned();
        let market_type = table
            .get("market_type")
            .and_then(toml::Value::as_str)
            .map(str::to_owned)
            .unwrap_or_else(|| infer_market_type(&provider));
        let asset_type = table
            .get("asset_type")
            .and_then(toml::Value::as_str)
            .map(str::to_owned)
            .or_else(|| infer_asset_type(&provider));
        let endpoint = table
            .get("endpoint")
            .and_then(toml::Value::as_str)
            .unwrap_or_else(|| default_endpoint(&provider))
            .to_owned();
        let credential_id = table
            .get("credential_id")
            .and_then(toml::Value::as_str)
            .map(str::to_owned);
        let credentials_root = workspace.child(&["credentials"])?;
        let provider_for_factory = provider.clone();
        let endpoint_for_factory = endpoint.clone();
        let credential_id_for_factory = credential_id.clone();
        let factory: MarketFeedFactory = Box::new(move || match provider_for_factory.as_str() {
            "binance-spot-rest" => Ok(Box::new(binance_spot_rest_feed(
                endpoint_for_factory.clone(),
            )?)
                as Box<dyn kairos_market::application::MarketFeed>),
            "binance-spot-websocket" => Ok(Box::new(binance_spot_websocket_feed(
                endpoint_for_factory.clone(),
            )?)
                as Box<dyn kairos_market::application::MarketFeed>),
            "binance-equity-rest" => {
                let credential = load_workspace_credential(
                    &credentials_root,
                    "binance",
                    credential_id_for_factory.as_deref(),
                )?
                .ok_or("Binance Equity Market requires a workspace credential")?;
                if credential.api_key.trim().is_empty() {
                    return Err("Binance Equity Market credential has no API key".into());
                }
                Ok(Box::new(binance_equity_rest_feed(
                    credential.api_key,
                    credential.secret,
                    endpoint_for_factory.clone(),
                )?)
                    as Box<dyn kairos_market::application::MarketFeed>)
            }
            "binance-usdm-futures-rest" => Ok(Box::new(binance_derivatives_rest_feed(
                ProductFamily::UsdMFutures,
                endpoint_for_factory.clone(),
                "/fapi/v1/ticker/bookTicker",
            )?)
                as Box<dyn kairos_market::application::MarketFeed>),
            "binance-coinm-futures-rest" => Ok(Box::new(binance_derivatives_rest_feed(
                ProductFamily::CoinMFutures,
                endpoint_for_factory.clone(),
                "/dapi/v1/ticker/bookTicker",
            )?)
                as Box<dyn kairos_market::application::MarketFeed>),
            "binance-options-rest" => Ok(Box::new(binance_derivatives_rest_feed(
                ProductFamily::Options,
                endpoint_for_factory.clone(),
                "/eapi/v1/ticker",
            )?)
                as Box<dyn kairos_market::application::MarketFeed>),
            "okx-spot-rest" => Ok(Box::new(okx_market_rest_feed(
                ProductFamily::Spot,
                endpoint_for_factory.clone(),
            )?)
                as Box<dyn kairos_market::application::MarketFeed>),
            "okx-swap-rest" => Ok(Box::new(okx_market_rest_feed(
                ProductFamily::UsdMFutures,
                endpoint_for_factory.clone(),
            )?)
                as Box<dyn kairos_market::application::MarketFeed>),
            "okx-futures-rest" => Ok(Box::new(okx_market_rest_feed(
                ProductFamily::CoinMFutures,
                endpoint_for_factory.clone(),
            )?)
                as Box<dyn kairos_market::application::MarketFeed>),
            "okx-options-rest" => Ok(Box::new(okx_market_rest_feed(
                ProductFamily::Options,
                endpoint_for_factory.clone(),
            )?)
                as Box<dyn kairos_market::application::MarketFeed>),
            "massive-equity-websocket" => {
                let credential = load_workspace_credential(
                    &credentials_root,
                    "massive",
                    credential_id_for_factory.as_deref(),
                )?
                .ok_or("Massive market requires a workspace credential")?;
                Ok(Box::new(massive_market_websocket_feed(
                    ProductFamily::Equity,
                    credential.api_key,
                    endpoint_for_factory.clone(),
                )?)
                    as Box<dyn kairos_market::application::MarketFeed>)
            }
            "massive-options-websocket" => {
                let credential = load_workspace_credential(
                    &credentials_root,
                    "massive",
                    credential_id_for_factory.as_deref(),
                )?
                .ok_or("Massive market requires a workspace credential")?;
                Ok(Box::new(massive_market_websocket_feed(
                    ProductFamily::Options,
                    credential.api_key,
                    endpoint_for_factory.clone(),
                )?)
                    as Box<dyn kairos_market::application::MarketFeed>)
            }
            other => Err(format!("unsupported workspace market provider: {other}").into()),
        });
        let route = match asset_type {
            Some(asset_type) => MarketRoute::with_asset_type(venue, market_type, asset_type),
            None => MarketRoute::new(venue, market_type),
        };
        if factories.insert(route.clone(), factory).is_some() {
            return Err(format!(
                "market connections declare duplicate route {}:{}:{}",
                route.venue_id,
                route.market_type,
                route.asset_type.as_deref().unwrap_or("unspecified")
            )
            .into());
        }
    }
    Ok(CompositeMarketFeed::new(factories)?)
}

fn infer_market_type(provider: &str) -> String {
    if provider.contains("equity") {
        "equity".into()
    } else if provider.contains("option") {
        "options".into()
    } else if provider.contains("usdm") {
        "usd-m-futures".into()
    } else if provider.contains("coinm") {
        "coin-m-futures".into()
    } else if provider.contains("swap") {
        "swap".into()
    } else if provider.contains("future") {
        "futures".into()
    } else {
        "spot".into()
    }
}

fn infer_asset_type(provider: &str) -> Option<String> {
    if provider.contains("equity") || provider.starts_with("massive-") {
        Some("equity".into())
    } else if matches!(
        provider,
        "binance-spot-rest"
            | "binance-spot-websocket"
            | "binance-usdm-futures-rest"
            | "binance-coinm-futures-rest"
            | "binance-options-rest"
            | "okx-spot-rest"
            | "okx-swap-rest"
            | "okx-futures-rest"
            | "okx-options-rest"
    ) {
        Some("crypto".into())
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::workspace_market_feed;
    use kairos_workspace::workspace::Workspace;
    use std::collections::BTreeSet;

    #[test]
    fn workspace_connection_directory_declares_all_required_market_routes() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::write(
            directory.path().join("kairos.toml"),
            r#"
version = 1
workspace_id = "workspace-test"

[market.connections.massive-equity]
provider = "massive-equity-websocket"
credential_id = "massive"

[market.connections.massive-options]
provider = "massive-options-websocket"
credential_id = "massive"

[market.connections.binance-spot]
provider = "binance-spot-rest"

[market.connections.binance-usdm]
provider = "binance-usdm-futures-rest"

[market.connections.binance-coinm]
provider = "binance-coinm-futures-rest"

[market.connections.binance-options]
provider = "binance-options-rest"

[market.connections.binance-equity]
provider = "binance-equity-rest"
credential_id = "binance"

[market.connections.okx-spot]
provider = "okx-spot-rest"
asset_type = "crypto"

[market.connections.okx-equity]
provider = "okx-spot-rest"
asset_type = "equity"

[market.connections.okx-swap]
provider = "okx-swap-rest"

[market.connections.okx-futures]
provider = "okx-futures-rest"

[market.connections.okx-options]
provider = "okx-options-rest"
"#,
        )
        .unwrap();
        let workspace = Workspace::open(directory.path()).unwrap();
        let feed = workspace_market_feed(&workspace).unwrap();
        let routes = feed
            .configured_routes()
            .map(|route| {
                (
                    route.venue_id.clone(),
                    route.market_type.clone(),
                    route.asset_type.clone(),
                )
            })
            .collect::<BTreeSet<_>>();
        let expected = [
            ("massive", "equity", Some("equity")),
            ("massive", "options", Some("equity")),
            ("binance", "spot", Some("crypto")),
            ("binance", "usd-m-futures", Some("crypto")),
            ("binance", "coin-m-futures", Some("crypto")),
            ("binance", "options", Some("crypto")),
            ("binance", "equity", Some("equity")),
            ("okx", "spot", Some("crypto")),
            ("okx", "spot", Some("equity")),
            ("okx", "swap", Some("crypto")),
            ("okx", "futures", Some("crypto")),
            ("okx", "options", Some("crypto")),
        ]
        .into_iter()
        .map(|(venue, market_type, asset_type)| {
            (
                venue.to_string(),
                market_type.to_string(),
                asset_type.map(str::to_string),
            )
        })
        .collect::<BTreeSet<_>>();
        assert_eq!(routes, expected);
    }
}
