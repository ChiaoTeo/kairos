//! Concrete consumption of the external Reference contract.

mod universe;

pub use universe::resolve_market_universe as resolve_reference_market_universe;
pub(crate) use universe::{build_market_universe_resolver, resolve_market_universe};

#[derive(Clone, Debug, serde::Serialize)]
pub struct CliReferenceUniverseResult {
    pub generation: kairos_primitives::time::Generation,
    pub event_sequence: kairos_primitives::time::Sequence,
    pub reference_markets: usize,
    pub resolved_markets: usize,
    pub massive_option_routes: usize,
    pub sample: Option<crate::ResolvedMarket>,
}

pub fn cli_reference_universe(
    workspace: &kairos_workspace::Workspace,
    instrument_kind: kairos_primitives::reference::InstrumentKind,
    limit: u64,
) -> Result<CliReferenceUniverseResult, Box<dyn std::error::Error>> {
    use kairos_primitives::reference::{InstrumentKind, ReferenceStatus};
    use kairos_reference_contract::{
        MarketCatalogQuery, MarketReferenceSnapshot, ReferenceCatalog,
    };

    let database = workspace.child(&["state", "reference", "reference.sqlite"])?;
    let reader = ReferenceCatalog::open(&database)?;
    let catalog_page = reader.market_catalog(&MarketCatalogQuery {
        instrument_kind: Some(instrument_kind),
        statuses: vec![ReferenceStatus::Active, ReferenceStatus::Trading],
        limit,
        ..MarketCatalogQuery::default()
    })?;
    let snapshot = MarketReferenceSnapshot {
        generation: catalog_page.watermark.generation,
        event_sequence: catalog_page.watermark.event_sequence,
        instruments: catalog_page.instruments.into_values().collect(),
        markets: catalog_page.markets,
        ..MarketReferenceSnapshot::default()
    };
    let config = super::MarketCompositionConfig::load(workspace)?;
    let update = resolve_reference_market_universe(&snapshot, &config.providers)?;
    let massive_option_routes = update
        .markets
        .iter()
        .filter(|market| {
            market.runtime_route().is_some_and(|route| {
                route.provider.as_str() == "massive" && route.provider_segment == "options"
            }) && market.instrument_kind == InstrumentKind::Option
        })
        .count();
    let sample = update
        .markets
        .iter()
        .find(|market| {
            market.runtime_route().is_some_and(|route| {
                route.provider.as_str() == "massive" && route.provider_segment == "options"
            }) && market.instrument_kind == InstrumentKind::Option
        })
        .cloned();
    Ok(CliReferenceUniverseResult {
        generation: update.generation,
        event_sequence: update.event_sequence,
        reference_markets: snapshot.markets.len(),
        resolved_markets: update.markets.len(),
        massive_option_routes,
        sample,
    })
}
