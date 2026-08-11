//! Process composition for concrete provider feeds.

use kairos_integration::application::credential::load_workspace_credential;
use kairos_integration::participants::binance::{
    self, ConnectionDomain as BinanceConnectionDomain,
};
use kairos_integration::participants::massive::{
    MarketType as MassiveMarketType, MassiveConnection, MassiveConnectionConfig,
};
use kairos_integration::participants::okx::{
    InstrumentType as OkxInstrumentType, OkxConnection, OkxConnectionConfig,
};
use kairos_workspace::workspace::Workspace;

pub use crate::application::{MarketFeed, MarketFeedRoute};
use crate::application::{MarketSnapshotPublisher, ReferenceChangeSource, ReferenceEvent};
use crate::services::composite::{MarketFeedFactory, MarketRoute};

pub use kairos_market_contract::transport::AeronReferenceChangeSource;

/// Market-owned source routing classification. Provider adapters map this to
/// their own native vocabulary at composition time.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MarketProduct {
    Spot,
    UsdMFutures,
    CoinMFutures,
    Options,
    Equity,
}

impl ReferenceChangeSource for AeronReferenceChangeSource {
    fn next_event(&mut self) -> Result<Option<ReferenceEvent>, String> {
        self.next_change()
            .map(|value| {
                value.map(|change| ReferenceEvent {
                    sequence: change.sequence.into(),
                })
            })
            .map_err(|error| error.to_string())
    }
}
pub struct MmapMarketSnapshotPublisher {
    inner: kairos_market_contract::encoding::MmapMarketSnapshotPublisher,
}

impl MmapMarketSnapshotPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        event_stream_id: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            inner: kairos_market_contract::encoding::MmapMarketSnapshotPublisher::create(
                path,
                slot_size,
                actor_id,
                event_stream_id,
            )
            .map_err(|error| error.to_string())?,
        })
    }

    pub fn create_with_identity(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        event_stream_id: impl Into<String>,
        identity: kairos_protocol::InstanceIdentity,
    ) -> Result<Self, String> {
        Ok(Self {
            inner:
                kairos_market_contract::encoding::MmapMarketSnapshotPublisher::create_with_identity(
                    path,
                    slot_size,
                    actor_id,
                    event_stream_id,
                    identity,
                )
                .map_err(|error| error.to_string())?,
        })
    }

    pub fn publish(
        &mut self,
        snapshot: &crate::domain::snapshot::MarketSnapshot,
    ) -> Result<(), String> {
        let mut value = serde_json::to_value(snapshot).map_err(|error| error.to_string())?;
        normalize_orderbook_decimals(&mut value)?;
        normalize_observation_decimals(&mut value)?;
        let contract: kairos_market_contract::MarketSnapshot =
            serde_json::from_value(value).map_err(|error| error.to_string())?;
        self.inner.publish(&contract)
    }
}

/// Convert domain fixed-decimal value objects to the decimal strings used by
/// the cross-process Market contract. This belongs at the composition
/// boundary; the domain keeps its typed Price and Quantity invariants while
/// the wire contract remains stable and provider-neutral.
fn normalize_orderbook_decimals(value: &mut serde_json::Value) -> Result<(), String> {
    let order_books = value
        .get_mut("order_books")
        .and_then(serde_json::Value::as_object_mut)
        .ok_or_else(|| "market snapshot is missing order_books".to_string())?;

    for book in order_books.values_mut() {
        let book = book
            .as_object_mut()
            .ok_or_else(|| "market order book must be an object".to_string())?;
        for side in ["bids", "asks"] {
            let levels = book
                .get_mut(side)
                .and_then(serde_json::Value::as_array_mut)
                .ok_or_else(|| format!("market order book is missing {side}"))?;
            for level in levels {
                let level = level
                    .as_object_mut()
                    .ok_or_else(|| "market price level must be an object".to_string())?;
                for field in ["price", "quantity"] {
                    let decimal = level
                        .get(field)
                        .ok_or_else(|| format!("market price level is missing {field}"))?;
                    level.insert(field.to_string(), fixed_decimal_string(decimal)?);
                }
            }
        }
    }
    Ok(())
}

fn normalize_observation_decimals(value: &mut serde_json::Value) -> Result<(), String> {
    for field in ["latest", "views"] {
        let Some(values) = value
            .get_mut(field)
            .and_then(serde_json::Value::as_object_mut)
        else {
            continue;
        };
        for observation in values.values_mut() {
            normalize_observation_value(observation)?;
        }
    }
    Ok(())
}

fn normalize_observation_value(value: &mut serde_json::Value) -> Result<(), String> {
    let object = value
        .as_object_mut()
        .ok_or_else(|| "market observation must be an object".to_string())?;
    for kind in ["Quote", "Trade", "Bar"] {
        if let Some(payload) = object
            .get_mut(kind)
            .and_then(serde_json::Value::as_object_mut)
        {
            let fields = match kind {
                "Quote" => ["bid_price", "bid_quantity", "ask_price", "ask_quantity"].as_slice(),
                "Trade" => ["price", "quantity", "cost"].as_slice(),
                _ => ["open", "high", "low", "close", "volume"].as_slice(),
            };
            for field in fields {
                if let Some(decimal) = payload.get_mut(*field) {
                    if !decimal.is_null() {
                        *decimal = fixed_decimal_string(decimal)?;
                    }
                }
            }
        }
    }
    for kind in ["TradeBar", "QuoteBar"] {
        if let Some(payload) = object
            .get_mut(kind)
            .and_then(serde_json::Value::as_object_mut)
        {
            if let Some(bar) = payload.get_mut("bar") {
                if let Some(bar) = bar.as_object_mut() {
                    for field in ["open", "high", "low", "close", "volume"] {
                        if let Some(decimal) = bar.get_mut(field) {
                            if !decimal.is_null() {
                                *decimal = fixed_decimal_string(decimal)?;
                            }
                        }
                    }
                }
            }
        }
    }
    let decimal_fields: &[(&str, &[&str])] = &[
        (
            "OptionGreeks",
            &[
                "strike",
                "delta",
                "gamma",
                "vega",
                "theta",
                "implied_volatility",
            ],
        ),
        ("Rate", &["value", "mark_price"]),
        (
            "Ticker24h",
            &[
                "last_price",
                "bid_price",
                "bid_quantity",
                "ask_price",
                "ask_quantity",
                "open_price",
                "high_price",
                "low_price",
                "volume_base",
                "volume_quote",
                "price_change_abs",
                "price_change_pct",
                "vwap",
                "mark_price",
            ],
        ),
        (
            "MarkPrice",
            &[
                "mark_price",
                "index_price",
                "estimated_settlement_price",
                "funding_rate",
            ],
        ),
        (
            "IndexPrice",
            &[
                "spot_index_price",
                "contract_index_price",
                "index_price",
                "funding_rate",
            ],
        ),
        ("FundingRate", &["funding_rate"]),
        (
            "OpenInterest",
            &["contracts", "quote_value", "change_24h", "change_pct_24h"],
        ),
    ];
    for (kind, fields) in decimal_fields {
        if let Some(payload) = object
            .get_mut(*kind)
            .and_then(serde_json::Value::as_object_mut)
        {
            for field in *fields {
                if let Some(decimal) = payload.get_mut(*field) {
                    if !decimal.is_null() {
                        *decimal = fixed_decimal_string(decimal)?;
                    }
                }
            }
        }
    }
    Ok(())
}

fn fixed_decimal_string(value: &serde_json::Value) -> Result<serde_json::Value, String> {
    if value.is_string() {
        return Ok(value.clone());
    }
    let object = value
        .as_object()
        .ok_or_else(|| "market observation decimal must be an object".to_string())?;
    let mantissa = object
        .get("mantissa")
        .and_then(serde_json::Value::as_i64)
        .ok_or_else(|| "market observation decimal has invalid mantissa".to_string())?;
    let scale = object
        .get("scale")
        .and_then(serde_json::Value::as_u64)
        .ok_or_else(|| "market observation decimal has invalid scale".to_string())?;
    let scale = u32::try_from(scale).map_err(|_| "decimal scale is too large")?;
    let divisor = 10_i128
        .checked_pow(scale)
        .ok_or_else(|| "market observation decimal scale overflows".to_string())?;
    let mantissa = i128::from(mantissa);
    let negative = mantissa < 0;
    let absolute = mantissa.abs();
    let whole = absolute / divisor;
    let fraction = format!("{:0width$}", absolute % divisor, width = scale as usize);
    let rendered = if scale == 0 {
        whole.to_string()
    } else {
        format!("{whole}.{fraction}")
    };
    Ok(serde_json::Value::String(if negative {
        format!("-{rendered}")
    } else {
        rendered
    }))
}

impl MarketSnapshotPublisher for MmapMarketSnapshotPublisher {
    fn publish(
        &mut self,
        snapshot: &crate::domain::snapshot::MarketSnapshot,
    ) -> Result<(), String> {
        Self::publish(self, snapshot)
    }
}
/// Default market capability used by a workspace that has not declared an
/// explicit connection catalog. The provider choice remains in composition;
/// strategies only declare their market-data intent.
pub fn default_market_feed() -> Result<Box<dyn MarketFeed>, String> {
    let mut factories: std::collections::BTreeMap<MarketRoute, MarketFeedFactory> =
        std::collections::BTreeMap::new();
    add_public_factories(&mut factories);
    Ok(Box::new(
        crate::services::composite::CompositeMarketFeed::new(factories)?,
    ))
}

/// Build the workspace-default market capability directory.
///
/// Public product connections are always available from built-in composition.
/// Credentialed products are added when a matching Workspace credential is
/// discovered; users do not need to describe provider routes in `kairos.toml`.
pub fn workspace_market_feed(workspace: &Workspace) -> Result<Box<dyn MarketFeed>, String> {
    let mut factories = std::collections::BTreeMap::new();
    add_public_factories(&mut factories);
    add_configured_source_factories(workspace, &mut factories)?;

    let credentials_root = workspace
        .child(&["credentials"])
        .map_err(|error| error.to_string())?;
    if let Some(credential) = load_workspace_credential(&credentials_root, "binance", None)? {
        let secret = credential.secret_value().to_owned();
        if !credential.api_key.trim().is_empty() && !secret.trim().is_empty() {
            let endpoint = default_endpoint("binance-equity-rest").to_owned();
            let api_key = credential.api_key;
            factories.insert(
                MarketRoute::with_asset_type("binance", "equity", "equity"),
                Box::new(move || {
                    binance_equity_rest_feed(api_key.clone(), secret.clone(), endpoint.clone())
                }),
            );
        }
    }

    if let Some(credential) = load_workspace_credential(&credentials_root, "massive", None)? {
        if !credential.api_key.trim().is_empty() {
            let api_key = credential.api_key;
            let equity_key = api_key.clone();
            let options_key = api_key;
            let equity_endpoint = massive_websocket_endpoint(workspace, MarketProduct::Equity);
            let options_endpoint = massive_websocket_endpoint(workspace, MarketProduct::Options);
            factories.insert(
                MarketRoute::with_asset_type("massive", "equity", "equity"),
                Box::new(move || {
                    massive_market_websocket_feed(
                        MarketProduct::Equity,
                        equity_key.clone(),
                        equity_endpoint.clone(),
                    )
                }),
            );
            factories.insert(
                MarketRoute::with_asset_type("massive", "options", "equity"),
                Box::new(move || {
                    massive_market_websocket_feed(
                        MarketProduct::Options,
                        options_key.clone(),
                        options_endpoint.clone(),
                    )
                }),
            );
        }
    }

    Ok(Box::new(
        crate::services::composite::CompositeMarketFeed::new(factories)?,
    ))
}

fn add_configured_source_factories(
    workspace: &Workspace,
    factories: &mut std::collections::BTreeMap<
        crate::services::composite::MarketRoute,
        crate::services::composite::MarketFeedFactory,
    >,
) -> Result<(), String> {
    let credentials_root = workspace
        .child(&["credentials"])
        .map_err(|error| error.to_string())?;
    for (source_id, config) in &workspace.market_config().sources {
        if config.enabled == Some(false) {
            continue;
        }
        let provider = config.provider.to_ascii_lowercase();
        let route = match config.asset_type.as_deref() {
            Some(asset_type) => {
                crate::services::composite::MarketRoute::with_source_and_asset_type(
                    source_id,
                    &config.exchange,
                    &config.market_type,
                    asset_type,
                )
            }
            None => crate::services::composite::MarketRoute::with_source(
                source_id,
                &config.exchange,
                &config.market_type,
            ),
        };
        let transport = config
            .transport
            .as_deref()
            .unwrap_or("websocket")
            .to_ascii_lowercase();
        let endpoint = config.endpoint.clone().unwrap_or_else(|| {
            default_endpoint(if provider == "binance" && config.market_type == "spot" {
                if transport == "rest" {
                    "binance-spot-rest"
                } else {
                    "binance-spot-websocket"
                }
            } else if provider == "massive" {
                "massive-equity-websocket"
            } else {
                provider.as_str()
            })
            .to_owned()
        });
        let factory: MarketFeedFactory = match (provider.as_str(), config.market_type.as_str()) {
            ("binance", "spot") if transport == "rest" => {
                Box::new(move || binance_spot_rest_feed(endpoint.clone()))
            }
            ("binance", "spot") => Box::new(move || binance_spot_websocket_feed(endpoint.clone())),
            ("massive", "equity") | ("massive", "options") => {
                let credential_id = config.credential_id.as_deref();
                let credential =
                    load_workspace_credential(&credentials_root, "massive", credential_id)?
                        .ok_or_else(|| {
                            format!("market source {source_id} requires a Massive credential")
                        })?;
                let api_key = credential.api_key;
                if api_key.trim().is_empty() {
                    return Err(format!(
                        "market source {source_id} credential has no API key"
                    ));
                }
                let product = if config.market_type == "options" {
                    MarketProduct::Options
                } else {
                    MarketProduct::Equity
                };
                Box::new(move || {
                    massive_market_websocket_feed(product, api_key.clone(), endpoint.clone())
                })
            }
            _ => {
                return Err(format!(
                    "unsupported configured market source {source_id}: {provider}/{}",
                    config.market_type
                ))
            }
        };
        factories.insert(route, factory);
    }
    Ok(())
}

fn add_public_factories(
    factories: &mut std::collections::BTreeMap<
        crate::services::composite::MarketRoute,
        crate::services::composite::MarketFeedFactory,
    >,
) {
    let binance_spot_endpoint = default_endpoint("binance-spot-websocket").to_owned();
    factories.insert(
        crate::services::composite::MarketRoute::with_source_and_asset_type(
            "binance.public.websocket",
            "binance",
            "spot",
            "crypto",
        ),
        Box::new(move || binance_spot_websocket_feed(binance_spot_endpoint.clone())),
    );

    for (route, product, endpoint, path) in [
        (
            crate::services::composite::MarketRoute::with_source_and_asset_type(
                "binance.public.rest.usd-m-futures",
                "binance",
                "usd-m-futures",
                "crypto",
            ),
            MarketProduct::UsdMFutures,
            default_endpoint("binance-usdm-futures-rest"),
            "/fapi/v1/ticker/bookTicker",
        ),
        (
            crate::services::composite::MarketRoute::with_source_and_asset_type(
                "binance.public.rest.coin-m-futures",
                "binance",
                "coin-m-futures",
                "crypto",
            ),
            MarketProduct::CoinMFutures,
            default_endpoint("binance-coinm-futures-rest"),
            "/dapi/v1/ticker/bookTicker",
        ),
    ] {
        let endpoint = endpoint.to_owned();
        let path = path.to_owned();
        factories.insert(
            route,
            Box::new(move || {
                binance_derivatives_rest_feed(product, endpoint.clone(), path.clone())
            }),
        );
    }

    let endpoint = default_endpoint("binance-options-websocket").to_owned();
    factories.insert(
        crate::services::composite::MarketRoute::with_source_and_asset_type(
            "binance.public.websocket.options",
            "binance",
            "options",
            "crypto",
        ),
        Box::new(move || binance_options_websocket_feed(endpoint.clone())),
    );

    for (route, instrument_type) in [
        (
            crate::services::composite::MarketRoute::with_source_and_asset_type(
                "okx.public.rest.spot",
                "okx",
                "spot",
                "crypto",
            ),
            OkxInstrumentType::Spot,
        ),
        (
            crate::services::composite::MarketRoute::with_source_and_asset_type(
                "okx.public.rest.swap",
                "okx",
                "swap",
                "crypto",
            ),
            OkxInstrumentType::Swap,
        ),
        (
            crate::services::composite::MarketRoute::with_source_and_asset_type(
                "okx.public.rest.futures",
                "okx",
                "futures",
                "crypto",
            ),
            OkxInstrumentType::Futures,
        ),
        (
            crate::services::composite::MarketRoute::with_source_and_asset_type(
                "okx.public.rest.options",
                "okx",
                "options",
                "crypto",
            ),
            OkxInstrumentType::Option,
        ),
        (
            crate::services::composite::MarketRoute::with_source_and_asset_type(
                "okx.public.rest.equity",
                "okx",
                "spot",
                "equity",
            ),
            OkxInstrumentType::Spot,
        ),
    ] {
        factories.insert(
            route,
            Box::new(move || {
                okx_market_rest_feed(instrument_type, default_endpoint("okx-spot-rest"))
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
        // Binance Options market streams are served by the futures stream
        // gateway. The Options-specific host currently returns 404.
        "binance-options-websocket" => "wss://fstream.binance.com/ws",
        "okx-spot-rest" | "okx-swap-rest" | "okx-futures-rest" | "okx-options-rest" => {
            "https://www.okx.com"
        }
        "massive-equity-websocket" => "http://socket.massiveprivateserver.site/stocks",
        "massive-options-websocket" => "http://socket.massiveprivateserver.site/options",
        _ => "https://api.binance.com",
    }
}

#[cfg(test)]
#[allow(clippy::items_after_test_module)]
mod tests {
    use super::default_endpoint;

    #[test]
    fn binance_spot_websocket_uses_a_websocket_endpoint() {
        assert_eq!(
            default_endpoint("binance-spot-websocket"),
            "wss://stream.binance.com:9443/ws"
        );
    }
}

/// Return the configured Massive WebSocket endpoint, falling back to the
/// bundled private proxy. The CLI `--endpoint` remains the highest-precedence
/// option for direct provider mode.
pub fn massive_websocket_endpoint(workspace: &Workspace, product: MarketProduct) -> String {
    let base = workspace
        .market_config()
        .massive
        .websocket_base_url
        .clone()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "http://socket.massiveprivateserver.site".to_owned());
    let path = match product {
        MarketProduct::Options => "/options",
        _ => "/stocks",
    };
    format!("{}{}", base.trim_end_matches('/'), path)
}

pub fn binance_spot_rest_feed(endpoint: impl Into<String>) -> Result<Box<dyn MarketFeed>, String> {
    let connection = binance::blocking::spot_rest_market(endpoint).map_err(|e| e.to_string())?;
    crate::services::integration::IntegrationMarketFeed::with_source(
        connection,
        "binance.public.rest",
    )
    .map(|feed| Box::new(feed) as Box<dyn MarketFeed>)
}

pub fn binance_spot_websocket_feed(
    endpoint: impl Into<String>,
) -> Result<Box<dyn MarketFeed>, String> {
    let connection =
        binance::blocking::spot_websocket_market(endpoint).map_err(|e| e.to_string())?;
    crate::services::integration::IntegrationMarketFeed::with_source(
        connection,
        "binance.public.websocket",
    )
    .map(|feed| Box::new(feed) as Box<dyn MarketFeed>)
}

pub fn binance_equity_rest_feed(
    api_key: impl Into<String>,
    secret: impl Into<String>,
    endpoint: impl Into<String>,
) -> Result<Box<dyn MarketFeed>, String> {
    let connection = binance::blocking::equity_rest_market(api_key, secret, endpoint)
        .map_err(|e| e.to_string())?;
    crate::services::integration::IntegrationMarketFeed::with_source(
        connection,
        "binance.private.equity.rest",
    )
    .map(|feed| Box::new(feed) as Box<dyn MarketFeed>)
}

pub fn binance_derivatives_rest_feed(
    product: MarketProduct,
    endpoint: impl Into<String>,
    path: impl Into<String>,
) -> Result<Box<dyn MarketFeed>, String> {
    let source_id = match product {
        MarketProduct::UsdMFutures => "binance.public.rest.usd-m-futures",
        MarketProduct::CoinMFutures => "binance.public.rest.coin-m-futures",
        MarketProduct::Options => "binance.public.rest.options",
        _ => "binance.public.rest.derivatives",
    };
    let product = match product {
        MarketProduct::UsdMFutures => BinanceConnectionDomain::UsdMFutures,
        MarketProduct::CoinMFutures => BinanceConnectionDomain::CoinMFutures,
        MarketProduct::Options => BinanceConnectionDomain::Options,
        _ => return Err("Binance derivatives feed requires futures or options product".into()),
    };
    let connection = binance::blocking::derivatives_rest_market(product, endpoint, path)
        .map_err(|e| e.to_string())?;
    crate::services::integration::IntegrationMarketFeed::with_source(connection, source_id)
        .map(|feed| Box::new(feed) as Box<dyn MarketFeed>)
}

pub fn binance_options_websocket_feed(
    endpoint: impl Into<String>,
) -> Result<Box<dyn MarketFeed>, String> {
    let connection =
        binance::blocking::options_websocket_market(endpoint).map_err(|e| e.to_string())?;
    crate::services::integration::IntegrationMarketFeed::with_source(
        connection,
        "binance.public.websocket.options",
    )
    .map(|feed| Box::new(feed) as Box<dyn MarketFeed>)
}

pub fn okx_market_rest_feed(
    instrument_type: OkxInstrumentType,
    endpoint: impl Into<String>,
) -> Result<Box<dyn MarketFeed>, String> {
    let provider = OkxConnection::connect(OkxConnectionConfig {
        environment: "public".into(),
        rest_base_url: endpoint.into(),
        shared_quota: None,
    })
    .map_err(|error| error.to_string())?;
    Ok(Box::new(
        crate::services::integration::OkxSnapshotMarketFeed::with_source(
            provider.blocking_market_snapshot(instrument_type),
            "okx.public.rest",
        ),
    ))
}

pub fn massive_market_websocket_feed(
    product: MarketProduct,
    api_key: impl Into<String>,
    endpoint: impl Into<String>,
) -> Result<Box<dyn MarketFeed>, String> {
    let endpoint = endpoint.into();
    let provider = MassiveConnection::connect(MassiveConnectionConfig {
        environment: "public".into(),
        rest_base_url: endpoint.clone(),
        api_key: secrecy::SecretString::new(api_key.into().into()),
    })
    .map_err(|error| error.to_string())?;
    let market_type = match product {
        MarketProduct::Equity => MassiveMarketType::Equity,
        MarketProduct::Options => MassiveMarketType::Option,
        _ => return Err("Massive market feed requires equity or options product".into()),
    };
    let connection = provider
        .blocking_live_market(market_type, endpoint)
        .map_err(|error| error.to_string())?;
    crate::services::integration::IntegrationMarketFeed::with_source(
        Box::new(connection),
        match product {
            MarketProduct::Equity => "massive.public.websocket.equity",
            MarketProduct::Options => "massive.public.websocket.options",
            _ => "massive.public.websocket",
        },
    )
    .map(|feed| Box::new(feed) as Box<dyn MarketFeed>)
}

/// Build the deterministic replay capability without exposing its stateful
/// implementation to callers outside composition.
pub fn replay_market_feed(
    events: impl IntoIterator<Item = crate::domain::observations::MarketObservation>,
) -> Box<dyn MarketFeed> {
    Box::new(crate::services::replay::ReplayMarketFeed::new(events))
}

/// Build a checkpointed replay capability for one runtime instance.
pub fn replay_market_feed_with_checkpoint(
    events: impl IntoIterator<Item = crate::domain::observations::MarketObservation>,
    start_unix_nanos: Option<u64>,
    end_unix_nanos: Option<u64>,
    checkpoint: impl Into<std::path::PathBuf>,
) -> Result<Box<dyn MarketFeed>, String> {
    Ok(Box::new(
        crate::services::replay::ReplayMarketFeed::with_checkpoint(
            events,
            start_unix_nanos,
            end_unix_nanos,
            checkpoint,
        )?,
    ))
}
