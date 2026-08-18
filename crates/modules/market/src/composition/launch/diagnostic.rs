//! One-shot diagnostics driven by the normal Conflux connection owner.

use std::time::Duration;

use kairos_conflux::{
    BinanceRestConfig, BinanceWebSocketConfig, Conflux, ConfluxConfig, ConfluxSystem,
    ConnectionKey, ShutdownMode,
};
use kairos_market_contract::{MarketRestRequest, MarketRestResponse};
use kairos_protocol::InstanceIdentity;

use crate::application::conflux::{MarketSourceMode, MarketSourcePlan};
use crate::domain::source::{SourceDescriptor, SourceId};
use crate::{MarketApplication, ObservationKind};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DiagnosticProvider {
    BinanceSpotRest,
    BinanceSpotWebsocket,
    BinanceOptionsRest,
}

pub async fn run_diagnostic_once(
    mut application: MarketApplication,
    provider: DiagnosticProvider,
    endpoint: String,
) -> Result<MarketApplication, String> {
    let baseline_sequence = application.event_sequence();
    let mut system = ConfluxSystem::new();
    let plan = install_connection(&mut system, provider, endpoint)?;
    let view_root = std::env::temp_dir().join(format!(
        "kairos-market-diagnostic-{}-{}",
        std::process::id(),
        now_unix_nanos()
    ));
    application.configure_conflux(
        Duration::from_secs(1),
        Duration::from_secs(5),
        Duration::from_secs(5),
        view_root.clone(),
        4 * 1024 * 1024,
        InstanceIdentity::new("diagnostic", "diagnostic", "market-cli"),
        vec![plan],
        None,
        None,
    )?;

    let (process, handle) = Conflux::new(
        application,
        system,
        ConfluxConfig {
            ingress_capacity: 64,
            shutdown_timeout: Duration::from_secs(5),
        },
    )
    .map_err(|error| error.to_string())?;
    let mut process = Box::pin(process.run());
    let deadline = tokio::time::Instant::now() + Duration::from_secs(30);
    let mut probe = tokio::time::interval(Duration::from_millis(25));
    probe.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);

    let outcome = loop {
        tokio::select! {
            outcome = &mut process => break outcome.map_err(|error| error.to_string())?,
            _ = probe.tick() => {
                let Some(MarketRestResponse::Health(Ok(health))) = handle
                    .handle_rest(MarketRestRequest::Health)
                    .await
                    .map_err(|error| format!("Market diagnostic health request failed: {error:?}"))?
                else {
                    return Err("Market diagnostic returned an invalid health response".into());
                };
                if health.event_sequence > baseline_sequence {
                    handle.shutdown(ShutdownMode::Drain);
                }
            }
            _ = tokio::time::sleep_until(deadline) => {
                handle.shutdown(ShutdownMode::Immediate);
                return Err("Market diagnostic timed out waiting for provider input".into());
            }
        }
    };
    let _ = std::fs::remove_dir_all(view_root);
    Ok(outcome.actor)
}

fn install_connection(
    system: &mut ConfluxSystem,
    provider: DiagnosticProvider,
    endpoint: String,
) -> Result<MarketSourcePlan, String> {
    let (source_id, descriptor, mode) = match provider {
        DiagnosticProvider::BinanceSpotRest => {
            let source_id = "binance.public.rest";
            system
                .connections()
                .binance_spot_rest
                .create(
                    ConnectionKey::new(source_id)?,
                    BinanceRestConfig {
                        environment: "public".into(),
                        endpoint,
                        credential: None,
                    },
                )
                .map_err(|error| error.to_string())?;
            (
                source_id,
                descriptor(source_id, "spot", [ObservationKind::Quote])?,
                MarketSourceMode::Snapshot(Duration::from_secs(1)),
            )
        }
        DiagnosticProvider::BinanceSpotWebsocket => {
            let source_id = "binance.public.websocket";
            system
                .connections()
                .binance_spot_websocket
                .create(
                    ConnectionKey::new(source_id)?,
                    BinanceWebSocketConfig {
                        environment: "public".into(),
                        endpoint,
                        credential: None,
                        event_capacity: 4_096,
                    },
                )
                .map_err(|error| error.to_string())?;
            (
                source_id,
                descriptor(source_id, "spot", stream_kinds())?,
                MarketSourceMode::MarketScopedStream,
            )
        }
        DiagnosticProvider::BinanceOptionsRest => {
            let source_id = "binance.public.rest.options";
            system
                .connections()
                .binance_options_rest
                .create(
                    ConnectionKey::new(source_id)?,
                    BinanceRestConfig {
                        environment: "public".into(),
                        endpoint,
                        credential: None,
                    },
                )
                .map_err(|error| error.to_string())?;
            (
                source_id,
                descriptor(
                    source_id,
                    "options",
                    [ObservationKind::Quote, ObservationKind::OptionGreeks],
                )?,
                MarketSourceMode::Snapshot(Duration::from_secs(1)),
            )
        }
    };
    debug_assert_eq!(descriptor.id.as_str(), source_id);
    Ok(MarketSourcePlan { descriptor, mode })
}

fn descriptor(
    source_id: &str,
    market_type: &str,
    capabilities: impl IntoIterator<Item = ObservationKind>,
) -> Result<SourceDescriptor, String> {
    Ok(SourceDescriptor::new(
        SourceId::new(source_id)?,
        kairos_primitives::Exchange::new("binance").map_err(|error| error.to_string())?,
        market_type,
        Some("crypto".into()),
    )?
    .with_observation_capabilities(capabilities))
}

fn stream_kinds() -> [ObservationKind; 4] {
    [
        ObservationKind::Quote,
        ObservationKind::Trade,
        ObservationKind::Bar,
        ObservationKind::OrderBook,
    ]
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos().min(u128::from(u64::MAX)) as u64)
        .unwrap_or_default()
}
