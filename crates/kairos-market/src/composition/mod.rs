//! Process composition for concrete provider feeds.

use kairos_integration::application::{
    AccessScope, ConnectionSpec, IntegrationCapability, ProductFamily, TransportKind,
};
use kairos_integration::credentials::load_workspace_credential;
use kairos_integration::{Integration, IntegrationRoute};
use kairos_workspace::workspace::Workspace;

pub use crate::services::actor::MarketActor;
pub use crate::services::aeron::AeronReferenceChangeSource;
pub use crate::services::composite::{CompositeMarketFeed, MarketFeedFactory, MarketRoute};
pub use crate::services::integration::IntegrationMarketFeed;
pub use crate::services::publication::{
    MmapMarketSnapshotPublisher, MmapOrderBookSnapshotPublisher,
};
pub use crate::services::replay::ReplayMarketFeed;

/// Default market capability used by a workspace that has not declared an
/// explicit connection catalog. The provider choice remains in composition;
/// strategies only declare their market-data intent.
pub fn default_market_feed() -> Result<CompositeMarketFeed, String> {
    let mut factories: std::collections::BTreeMap<MarketRoute, MarketFeedFactory> =
        std::collections::BTreeMap::new();
    add_public_factories(&mut factories);
    CompositeMarketFeed::new(factories)
}

/// Build the workspace-default market capability directory.
///
/// Public product connections are always available from built-in composition.
/// Credentialed products are added when a matching Workspace credential is
/// discovered; users do not need to describe provider routes in `kairos.toml`.
pub fn workspace_market_feed(workspace: &Workspace) -> Result<CompositeMarketFeed, String> {
    let mut factories = std::collections::BTreeMap::new();
    add_public_factories(&mut factories);

    let credentials_root = workspace
        .child(&["credentials"])
        .map_err(|error| error.to_string())?;
    if let Some(credential) = load_workspace_credential(&credentials_root, "binance", None)? {
        if !credential.api_key.trim().is_empty() && !credential.secret.trim().is_empty() {
            let endpoint = default_endpoint("binance-equity-rest").to_owned();
            let api_key = credential.api_key;
            let secret = credential.secret;
            factories.insert(
                MarketRoute::with_asset_type("binance", "equity", "equity"),
                Box::new(move || {
                    Ok(Box::new(binance_equity_rest_feed(
                        api_key.clone(),
                        secret.clone(),
                        endpoint.clone(),
                    )?)
                        as Box<dyn crate::application::protocol::MarketFeed>)
                }),
            );
        }
    }

    if let Some(credential) = load_workspace_credential(&credentials_root, "massive", None)? {
        if !credential.api_key.trim().is_empty() {
            let api_key = credential.api_key;
            let equity_key = api_key.clone();
            let options_key = api_key;
            let equity_endpoint = massive_websocket_endpoint(workspace, ProductFamily::Equity);
            let options_endpoint = massive_websocket_endpoint(workspace, ProductFamily::Options);
            factories.insert(
                MarketRoute::with_asset_type("massive", "equity", "equity"),
                Box::new(move || {
                    Ok(Box::new(massive_market_websocket_feed(
                        ProductFamily::Equity,
                        equity_key.clone(),
                        equity_endpoint.clone(),
                    )?)
                        as Box<dyn crate::application::protocol::MarketFeed>)
                }),
            );
            factories.insert(
                MarketRoute::with_asset_type("massive", "options", "equity"),
                Box::new(move || {
                    Ok(Box::new(massive_market_websocket_feed(
                        ProductFamily::Options,
                        options_key.clone(),
                        options_endpoint.clone(),
                    )?)
                        as Box<dyn crate::application::protocol::MarketFeed>)
                }),
            );
        }
    }

    CompositeMarketFeed::new(factories)
}

fn add_public_factories(
    factories: &mut std::collections::BTreeMap<MarketRoute, MarketFeedFactory>,
) {
    let binance_spot_endpoint = default_endpoint("binance-spot-websocket").to_owned();
    factories.insert(
        MarketRoute::with_asset_type("binance", "spot", "crypto"),
        Box::new(move || {
            Ok(
                Box::new(binance_spot_websocket_feed(binance_spot_endpoint.clone())?)
                    as Box<dyn crate::application::protocol::MarketFeed>,
            )
        }),
    );

    for (route, product, endpoint, path) in [
        (
            MarketRoute::with_asset_type("binance", "usd-m-futures", "crypto"),
            ProductFamily::UsdMFutures,
            default_endpoint("binance-usdm-futures-rest"),
            "/fapi/v1/ticker/bookTicker",
        ),
        (
            MarketRoute::with_asset_type("binance", "coin-m-futures", "crypto"),
            ProductFamily::CoinMFutures,
            default_endpoint("binance-coinm-futures-rest"),
            "/dapi/v1/ticker/bookTicker",
        ),
        (
            MarketRoute::with_asset_type("binance", "options", "crypto"),
            ProductFamily::Options,
            default_endpoint("binance-options-rest"),
            "/eapi/v1/ticker",
        ),
    ] {
        let endpoint = endpoint.to_owned();
        let path = path.to_owned();
        factories.insert(
            route,
            Box::new(move || {
                Ok(Box::new(binance_derivatives_rest_feed(
                    product,
                    endpoint.clone(),
                    path.clone(),
                )?)
                    as Box<dyn crate::application::protocol::MarketFeed>)
            }),
        );
    }

    for (route, product) in [
        (
            MarketRoute::with_asset_type("okx", "spot", "crypto"),
            ProductFamily::Spot,
        ),
        (
            MarketRoute::with_asset_type("okx", "swap", "crypto"),
            ProductFamily::UsdMFutures,
        ),
        (
            MarketRoute::with_asset_type("okx", "futures", "crypto"),
            ProductFamily::CoinMFutures,
        ),
        (
            MarketRoute::with_asset_type("okx", "options", "crypto"),
            ProductFamily::Options,
        ),
        (
            MarketRoute::with_asset_type("okx", "spot", "equity"),
            ProductFamily::Spot,
        ),
    ] {
        factories.insert(
            route,
            Box::new(move || {
                Ok(Box::new(okx_market_rest_feed(
                    product,
                    default_endpoint("okx-spot-rest"),
                )?)
                    as Box<dyn crate::application::protocol::MarketFeed>)
            }),
        );
    }
}

/// Canonical endpoint defaults shared by the one-shot CLI and Market server.
pub fn default_endpoint(provider: &str) -> &'static str {
    match provider {
        "binance-spot-websocket" => "wss://stream.binance.com:9443/ws",
        "binance-usdm-futures-rest" => "https://fapi.binance.com",
        "binance-coinm-futures-rest" => "https://dapi.binance.com",
        "binance-options-rest" => "https://eapi.binance.com",
        "okx-spot-rest" | "okx-swap-rest" | "okx-futures-rest" | "okx-options-rest" => {
            "https://www.okx.com"
        }
        "massive-equity-websocket" => "http://socket.massiveprivateserver.site/stocks",
        "massive-options-websocket" => "http://socket.massiveprivateserver.site/options",
        _ => "https://api.binance.com",
    }
}

/// Return the configured Massive WebSocket endpoint, falling back to the
/// bundled private proxy. The CLI `--endpoint` remains the highest-precedence
/// option for direct provider mode.
pub fn massive_websocket_endpoint(workspace: &Workspace, product: ProductFamily) -> String {
    let base = workspace
        .market_config()
        .massive
        .websocket_base_url
        .clone()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "http://socket.massiveprivateserver.site".to_owned());
    let path = match product {
        ProductFamily::Options => "/options",
        _ => "/stocks",
    };
    format!("{}{}", base.trim_end_matches('/'), path)
}

pub fn binance_spot_rest_feed(
    endpoint: impl Into<String>,
) -> Result<IntegrationMarketFeed, String> {
    let integration = Integration::new()
        .with_binance_spot_rest_market_stream(endpoint)
        .map_err(|error| error.to_string())?;
    let connection = integration
        .connect_market_stream(&ConnectionSpec {
            connection_id: "market.binance.spot.rest".into(),
            route: IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Public,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::MarketStream,
            credential_id: None,
            asset_type: None,
        })
        .map_err(|error| error.to_string())?;
    IntegrationMarketFeed::new(connection)
}

pub fn binance_spot_websocket_feed(
    endpoint: impl Into<String>,
) -> Result<IntegrationMarketFeed, String> {
    let integration = Integration::new()
        .with_binance_spot_websocket_market_stream(endpoint)
        .map_err(|error| error.to_string())?;
    let connection = integration
        .connect_market_stream(&ConnectionSpec {
            connection_id: "market.binance.spot.websocket".into(),
            route: IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Public,
            transport: TransportKind::WebSocket,
            capability: IntegrationCapability::MarketStream,
            credential_id: None,
            asset_type: None,
        })
        .map_err(|error| error.to_string())?;
    IntegrationMarketFeed::new(connection)
}

pub fn binance_equity_rest_feed(
    api_key: impl Into<String>,
    secret: impl Into<String>,
    endpoint: impl Into<String>,
) -> Result<IntegrationMarketFeed, String> {
    let integration = Integration::new()
        .with_binance_equity_market_stream(api_key, secret, endpoint)
        .map_err(|error| error.to_string())?;
    let connection = integration
        .connect_market_stream(&ConnectionSpec {
            connection_id: "market.binance.equity.rest".into(),
            route: IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Equity),
            access: AccessScope::Public,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::MarketStream,
            credential_id: None,
            asset_type: Some(kairos_integration::domain::AssetType::Equity),
        })
        .map_err(|error| error.to_string())?;
    IntegrationMarketFeed::new(connection)
}

pub fn binance_derivatives_rest_feed(
    product: ProductFamily,
    endpoint: impl Into<String>,
    path: impl Into<String>,
) -> Result<IntegrationMarketFeed, String> {
    let integration = Integration::new()
        .with_binance_derivatives_market_stream(product, endpoint, path)
        .map_err(|error| error.to_string())?;
    let connection = integration
        .connect_market_stream(&ConnectionSpec {
            connection_id: format!("market.binance.{product:?}.rest"),
            route: IntegrationRoute::exchange("binance"),
            product: Some(product),
            access: AccessScope::Public,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::MarketStream,
            credential_id: None,
            asset_type: None,
        })
        .map_err(|error| error.to_string())?;
    IntegrationMarketFeed::new(connection)
}

pub fn okx_market_rest_feed(
    product: ProductFamily,
    endpoint: impl Into<String>,
) -> Result<IntegrationMarketFeed, String> {
    let integration = Integration::new()
        .with_okx_market_stream(product, endpoint)
        .map_err(|error| error.to_string())?;
    let connection = integration
        .connect_market_stream(&ConnectionSpec {
            connection_id: format!("market.okx.{product:?}.rest"),
            route: IntegrationRoute::exchange("okx"),
            product: Some(product),
            access: AccessScope::Public,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::MarketStream,
            credential_id: None,
            asset_type: None,
        })
        .map_err(|error| error.to_string())?;
    IntegrationMarketFeed::new(connection)
}

pub fn massive_market_websocket_feed(
    product: ProductFamily,
    api_key: impl Into<String>,
    endpoint: impl Into<String>,
) -> Result<IntegrationMarketFeed, String> {
    let integration = Integration::new()
        .with_massive_market_stream(product, api_key, endpoint)
        .map_err(|error| error.to_string())?;
    let connection = integration
        .connect_market_stream(&ConnectionSpec {
            connection_id: format!("market.massive.{product:?}.websocket"),
            route: IntegrationRoute::data_provider("massive"),
            product: Some(product),
            access: AccessScope::Public,
            transport: TransportKind::WebSocket,
            capability: IntegrationCapability::MarketStream,
            credential_id: None,
            asset_type: None,
        })
        .map_err(|error| error.to_string())?;
    IntegrationMarketFeed::new(connection)
}
