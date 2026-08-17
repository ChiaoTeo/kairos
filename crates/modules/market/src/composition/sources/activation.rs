//! Demand-driven construction of configured provider sources.

use kairos_workspace::Workspace;

use super::super::config::{BinanceSpotTransport, MarketConfig, MarketSourceBinding as Binding};
use super::routing::binding_matches_market;
use crate::domain::source::SourceId;
use crate::services::source::{SourceActivator, SourceHandle};

/// Demand-driven source construction for live and paper Market processes.
///
/// The activator contains only immutable workspace/configuration facts. The
/// active source map and all subscription state remain owned by MarketActor.
pub(crate) struct ConfiguredMarketSourceActivator {
    workspace: Workspace,
}

impl ConfiguredMarketSourceActivator {
    pub(crate) fn new(workspace: Workspace) -> Self {
        Self { workspace }
    }
}

impl SourceActivator for ConfiguredMarketSourceActivator {
    fn activate<'a>(
        &'a mut self,
        market: &'a crate::ResolvedMarket,
        source_input_capacity: usize,
    ) -> std::pin::Pin<
        Box<dyn std::future::Future<Output = Result<SourceHandle, String>> + Send + 'a>,
    > {
        let workspace = self.workspace.clone();
        let market = market.clone();
        Box::pin(async move { activate_workspace_source(workspace, market, source_input_capacity) })
    }
}

fn activate_workspace_source(
    workspace: Workspace,
    market: crate::ResolvedMarket,
    source_input_capacity: usize,
) -> Result<SourceHandle, String> {
    let credentials_root = workspace
        .child(&["credentials"])
        .map_err(|error| error.to_string())?;
    let market_config = MarketConfig::load(&workspace)?;
    let configured_route_exists = market_config
        .sources
        .values()
        .any(|binding| binding_matches_market(binding, &market));
    let mut candidates = market_config
        .sources
        .iter()
        .filter(|(id, binding)| {
            binding.enabled()
                && market
                    .source_id
                    .as_ref()
                    .is_none_or(|requested| requested.as_str().eq_ignore_ascii_case(id))
                && binding_matches_market(binding, &market)
        })
        .map(|(id, binding)| (id.clone(), binding.clone()))
        .collect::<Vec<_>>();

    // Public Binance Spot is the built-in default route. It keeps a
    // minimal workspace usable without turning provider source creation
    // into a required static Market configuration.
    if candidates.is_empty()
        && !configured_route_exists
        && market.source_id.is_none()
        && market.route.provider_id == "binance"
        && market.route.provider_product == "spot"
    {
        candidates.push((
            "binance-spot".into(),
            Binding::BinanceSpot {
                enabled: true,
                transport: BinanceSpotTransport::Websocket,
                endpoint: None,
                snapshot_interval_ms: 1_000,
            },
        ));
    }
    let [(source_id, binding)] = candidates.as_slice() else {
        return Err(if candidates.is_empty() {
            format!(
                "no Market source supports exchange={} market_type={} asset_type={:?}",
                market.exchange_id, market.route.provider_product, market.asset_type
            )
        } else {
            format!(
                "market route is ambiguous; candidates={}",
                candidates
                    .iter()
                    .map(|(id, _)| id.as_str())
                    .collect::<Vec<_>>()
                    .join(",")
            )
        });
    };

    let mut staging = crate::MarketApplication::new_with_source_capacity(
        "market-source-activation",
        1,
        source_input_capacity,
    )
    .map_err(|error| error.to_string())?;
    super::attach_configured(&mut staging, &credentials_root, source_id, binding)?;
    staging.take_source_handle(&SourceId::new(source_id.clone())?)
}
