//! Process composition for concrete provider feeds.

use kairos_integration::participants::binance;
use kairos_integration::participants::hyperliquid::{
    HyperliquidConnection, HyperliquidConnectionConfig,
};
use kairos_integration::participants::massive::{
    MarketType as MassiveMarketType, MassiveConnection, MassiveConnectionConfig,
};
use kairos_integration::participants::okx::{
    InstrumentType as OkxInstrumentType, OkxConnection, OkxConnectionConfig,
};
use kairos_workspace::{
    Workspace, WorkspaceBinanceDerivativeProduct, WorkspaceBinanceSpotTransport,
    WorkspaceMarketSourceBinding, WorkspaceMarketSourceBinding as Binding,
};

use crate::application::{MarketSnapshotPublisher, ReferenceChangeSource, ReferenceEvent};
use crate::domain::source::{SourceDescriptor, SourceId};
use crate::services::sources::{
    spawn_binance, spawn_replay, spawn_snapshot, spawn_stream, ReplaySource, SourceActivator,
    SourceHandle,
};
use crate::MarketApplication;

mod config;
mod diagnostic;
mod process;
mod sources;

pub use config::{
    MarketProcessRequest, MarketReplayClock, MarketReplayConfig, MarketRuntimeProfile,
    MarketRuntimeScope,
};
pub use diagnostic::{
    attach_binance_derivatives_source, attach_binance_spot_rest_source, attach_binance_spot_source,
};
pub use process::{build_market_process, MarketStartupError};

pub use kairos_market_contract::transport::AeronReferenceChangeSource;

/// Demand-driven source construction for live and paper Market processes.
///
/// The activator contains only immutable workspace/configuration facts. The
/// active source map and all subscription state remain owned by MarketActor.
pub(crate) struct WorkspaceMarketSourceActivator {
    workspace: Workspace,
}

impl WorkspaceMarketSourceActivator {
    pub(crate) fn new(workspace: Workspace) -> Self {
        Self { workspace }
    }
}

impl SourceActivator for WorkspaceMarketSourceActivator {
    fn activate<'a>(
        &'a mut self,
        market: &'a crate::MarketDescriptor,
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
    market: crate::MarketDescriptor,
    source_input_capacity: usize,
) -> Result<SourceHandle, String> {
    let credentials_root = workspace
        .child(&["credentials"])
        .map_err(|error| error.to_string())?;
    let configured_route_exists = workspace
        .market_config()
        .sources
        .values()
        .any(|binding| binding_matches_market(binding, &market));
    let mut candidates = workspace
        .market_config()
        .sources
        .iter()
        .filter(|(id, binding)| {
            binding.enabled()
                && market
                    .source_id
                    .as_deref()
                    .is_none_or(|requested| requested.eq_ignore_ascii_case(id))
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
        && market_exchange(&market).eq_ignore_ascii_case("binance")
        && market.market_type.eq_ignore_ascii_case("spot")
    {
        candidates.push((
            "binance-spot".into(),
            Binding::BinanceSpot {
                enabled: true,
                transport: WorkspaceBinanceSpotTransport::Websocket,
                endpoint: None,
                snapshot_interval_ms: 1_000,
            },
        ));
    }
    let [(source_id, binding)] = candidates.as_slice() else {
        return Err(if candidates.is_empty() {
            format!(
                "no Market source supports exchange={} market_type={} asset_type={:?}",
                market.exchange_id, market.market_type, market.asset_type
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
    attach_configured_market_source(&mut staging, &credentials_root, source_id, binding)?;
    staging.take_source_handle(&SourceId::new(source_id.clone())?)
}

fn binding_matches_market(
    binding: &WorkspaceMarketSourceBinding,
    market: &crate::MarketDescriptor,
) -> bool {
    let exchange = market_exchange(market);
    let market_type = market.market_type.as_str();
    let asset_type = market.asset_type.as_deref();
    match binding {
        WorkspaceMarketSourceBinding::BinanceSpot { .. } => {
            exchange.eq_ignore_ascii_case("binance")
                && market_type.eq_ignore_ascii_case("spot")
                && asset_type.is_none_or(|value| value.eq_ignore_ascii_case("crypto"))
        }
        WorkspaceMarketSourceBinding::BinanceEquity { .. } => {
            exchange.eq_ignore_ascii_case("binance")
                && market_type.eq_ignore_ascii_case("equity")
                && asset_type.is_none_or(|value| value.eq_ignore_ascii_case("equity"))
        }
        WorkspaceMarketSourceBinding::BinanceDerivatives { product, .. } => {
            let expected = match product {
                WorkspaceBinanceDerivativeProduct::UsdMFutures => "usd-m-futures",
                WorkspaceBinanceDerivativeProduct::CoinMFutures => "coin-m-futures",
                WorkspaceBinanceDerivativeProduct::Options => "options",
            };
            exchange.eq_ignore_ascii_case("binance")
                && market_type.eq_ignore_ascii_case(expected)
                && asset_type.is_none_or(|value| value.eq_ignore_ascii_case("crypto"))
        }
        WorkspaceMarketSourceBinding::Massive { product, .. } => {
            exchange.eq_ignore_ascii_case("massive")
                && market_type.eq_ignore_ascii_case(match product {
                    kairos_workspace::WorkspaceMassiveMarketProduct::Equity => "equity",
                    kairos_workspace::WorkspaceMassiveMarketProduct::Options => "options",
                })
                && asset_type.is_none_or(|value| value.eq_ignore_ascii_case("equity"))
        }
        WorkspaceMarketSourceBinding::Okx {
            instrument_type, ..
        } => {
            exchange.eq_ignore_ascii_case("okx")
                && market_type.eq_ignore_ascii_case(match instrument_type {
                    kairos_workspace::WorkspaceOkxInstrumentType::Spot => "spot",
                    kairos_workspace::WorkspaceOkxInstrumentType::Swap => "swap",
                    kairos_workspace::WorkspaceOkxInstrumentType::Futures => "futures",
                    kairos_workspace::WorkspaceOkxInstrumentType::Options => "options",
                })
        }
        WorkspaceMarketSourceBinding::Hyperliquid {
            market_type: configured,
            ..
        } => {
            exchange.eq_ignore_ascii_case("hyperliquid")
                && market_type.eq_ignore_ascii_case(match configured {
                    kairos_workspace::WorkspaceHyperliquidMarketType::Spot => "spot",
                    kairos_workspace::WorkspaceHyperliquidMarketType::Perpetual => "perpetual",
                })
        }
    }
}

fn market_exchange(market: &crate::MarketDescriptor) -> &str {
    market
        .exchange_id
        .as_str()
        .strip_prefix("exchange:")
        .unwrap_or(market.exchange_id.as_str())
}

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
    ) -> Result<Self, String> {
        Ok(Self {
            inner: kairos_market_contract::encoding::MmapMarketSnapshotPublisher::create(
                path, slot_size, actor_id,
            )
            .map_err(|error| error.to_string())?,
        })
    }

    pub fn create_with_identity(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        identity: kairos_protocol::InstanceIdentity,
    ) -> Result<Self, String> {
        Ok(Self {
            inner:
                kairos_market_contract::encoding::MmapMarketSnapshotPublisher::create_with_identity(
                    path, slot_size, actor_id, identity,
                )
                .map_err(|error| error.to_string())?,
        })
    }

    pub fn publish(
        &mut self,
        snapshot: &crate::domain::snapshot::MarketCurrentView,
    ) -> Result<(), String> {
        self.inner.publish(&market_contract_snapshot(snapshot))
    }
}

fn market_contract_snapshot(
    snapshot: &crate::domain::snapshot::MarketCurrentView,
) -> kairos_market_contract::MarketCurrentView {
    use kairos_market_contract::model as contract;

    contract::MarketCurrentView {
        actor_id: snapshot.actor_id.to_string(),
        generation: snapshot.generation.get(),
        latest: snapshot
            .latest
            .iter()
            .map(|(key, value)| (key.clone(), market_contract_observation(value)))
            .collect(),
        views: snapshot
            .views
            .iter()
            .map(|(key, value)| (key.clone(), market_contract_observation(value)))
            .collect(),
        order_books: snapshot
            .order_books
            .iter()
            .map(|(key, value)| (key.clone(), market_contract_orderbook(value)))
            .collect(),
        freshness: snapshot
            .freshness
            .iter()
            .map(|(key, value)| {
                (
                    key.clone(),
                    contract::MarketFreshness {
                        source_id: value.source_id.clone(),
                        market_id: value.market_id.to_string(),
                        data_kind: value.data_kind.clone(),
                        last_event_time_unix_nanos: value.last_event_time_unix_nanos.get(),
                        last_received_time_unix_nanos: value.last_received_time_unix_nanos.get(),
                        status: match value.status {
                            crate::DataFreshnessStatus::Unknown => {
                                contract::DataFreshnessStatus::Unknown
                            }
                            crate::DataFreshnessStatus::Current => {
                                contract::DataFreshnessStatus::Current
                            }
                            crate::DataFreshnessStatus::Stale => {
                                contract::DataFreshnessStatus::Stale
                            }
                        },
                    },
                )
            })
            .collect(),
        subscriptions: snapshot
            .subscriptions
            .iter()
            .map(|value| contract::SubscriptionState {
                id: value.id.0.clone(),
                owner_id: value.owner_id.clone(),
                mode: match value.mode {
                    crate::SubscriptionMode::Static => "static",
                    crate::SubscriptionMode::Dynamic => "dynamic",
                }
                .into(),
                selectors: value.selectors.clone(),
                members: value
                    .members
                    .iter()
                    .map(|(key, member)| {
                        (
                            key.clone(),
                            contract::MarketDescriptor {
                                market_id: member.market_id.to_string(),
                                instrument_id: member.instrument_id.to_string(),
                                exchange_id: member.exchange_id.to_string(),
                                market_type: member.market_type.clone(),
                                asset_type: member.asset_type.clone(),
                                underlying_instrument_id: member.underlying_instrument_id.clone(),
                                source_symbol: member.source_symbol.to_string(),
                                status: member.status.as_str().into(),
                            },
                        )
                    })
                    .collect(),
            })
            .collect(),
        feed_status: match snapshot.feed_status {
            crate::FeedStatus::Disconnected => contract::FeedStatus::Disconnected,
            crate::FeedStatus::Ready => contract::FeedStatus::Ready,
            crate::FeedStatus::Reconnecting => contract::FeedStatus::Reconnecting,
            crate::FeedStatus::WarmingUp => contract::FeedStatus::WarmingUp,
            crate::FeedStatus::Degraded => contract::FeedStatus::Degraded,
        },
    }
}

fn market_contract_observation(
    value: &crate::MarketObservation,
) -> kairos_market_contract::model::MarketObservation {
    use crate::MarketObservation as Domain;
    use kairos_market_contract::model as contract;

    fn bar(value: &crate::Bar) -> contract::Bar {
        contract::Bar {
            market_id: value.market_id.to_string(),
            instrument_id: value.instrument_id.to_string(),
            timeframe: value.timeframe.clone(),
            open: value.open.to_string(),
            high: value.high.to_string(),
            low: value.low.to_string(),
            close: value.close.to_string(),
            volume: value.volume.map(|value| value.to_string()),
            observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            source_id: value.source_id.clone(),
            derivation: value.derivation.clone(),
        }
    }

    match value {
        Domain::Quote(value) => contract::MarketObservation::Quote(contract::Quote {
            market_id: value.market_id.to_string(),
            instrument_id: value.instrument_id.to_string(),
            bid_price: value.bid_price.map(|value| value.to_string()),
            bid_quantity: value.bid_quantity.map(|value| value.to_string()),
            ask_price: value.ask_price.map(|value| value.to_string()),
            ask_quantity: value.ask_quantity.map(|value| value.to_string()),
            observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            source_id: value.source_id.clone(),
        }),
        Domain::Trade(value) => contract::MarketObservation::Trade(contract::Trade {
            market_id: value.market_id.to_string(),
            instrument_id: value.instrument_id.to_string(),
            trade_id: value.trade_id.clone(),
            price: value.price.to_string(),
            quantity: value.quantity.to_string(),
            cost: value.cost.map(|value| value.to_string()),
            aggressor_side: value.aggressor_side.clone(),
            observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            source_id: value.source_id.clone(),
        }),
        Domain::Bar(value) => contract::MarketObservation::Bar(bar(value)),
        Domain::TradeBar(value) => contract::MarketObservation::TradeBar(contract::TradeBar {
            bar: bar(&value.bar),
        }),
        Domain::QuoteBar(value) => contract::MarketObservation::QuoteBar(contract::QuoteBar {
            bar: bar(&value.bar),
        }),
        Domain::OptionGreeks(value) => {
            contract::MarketObservation::OptionGreeks(contract::OptionGreeks {
                market_id: value.market_id.to_string(),
                instrument_id: value.instrument_id.to_string(),
                expiry_unix_nanos: value.expiry_unix_nanos.map(|value| value.get()),
                strike: value.strike.map(|value| value.to_string()),
                delta: value.delta.map(|value| value.to_string()),
                gamma: value.gamma.map(|value| value.to_string()),
                vega: value.vega.map(|value| value.to_string()),
                theta: value.theta.map(|value| value.to_string()),
                implied_volatility: value.implied_volatility.map(|value| value.to_string()),
                observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
                source_id: value.source_id.clone(),
                derivation: value.derivation.clone(),
            })
        }
        Domain::Rate(value) => contract::MarketObservation::Rate(contract::Rate {
            rate_id: value.rate_id.clone(),
            market_id: value.market_id.to_string(),
            instrument_id: value.instrument_id.to_string(),
            basis: value.basis.clone(),
            value: value.value.to_string(),
            mark_price: value.mark_price.map(|value| value.to_string()),
            observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            source_id: value.source_id.clone(),
        }),
        Domain::Ticker24h(value) => contract::MarketObservation::Ticker24h(contract::Ticker24h {
            market_id: value.market_id.to_string(),
            instrument_id: value.instrument_id.to_string(),
            last_price: value.last_price.map(|value| value.to_string()),
            bid_price: value.bid_price.map(|value| value.to_string()),
            bid_quantity: value.bid_quantity.map(|value| value.to_string()),
            ask_price: value.ask_price.map(|value| value.to_string()),
            ask_quantity: value.ask_quantity.map(|value| value.to_string()),
            open_price: value.open_price.map(|value| value.to_string()),
            high_price: value.high_price.map(|value| value.to_string()),
            low_price: value.low_price.map(|value| value.to_string()),
            volume_base: value.volume_base.map(|value| value.to_string()),
            volume_quote: value.volume_quote.map(|value| value.to_string()),
            price_change_abs: value.price_change_abs.map(|value| value.to_string()),
            price_change_pct: value.price_change_pct.map(|value| value.to_string()),
            vwap: value.vwap.map(|value| value.to_string()),
            mark_price: value.mark_price.map(|value| value.to_string()),
            observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            source_id: value.source_id.clone(),
        }),
        Domain::MarkPrice(value) => contract::MarketObservation::MarkPrice(contract::MarkPrice {
            market_id: value.market_id.to_string(),
            instrument_id: value.instrument_id.to_string(),
            mark_price: value.mark_price.to_string(),
            index_price: value.index_price.map(|value| value.to_string()),
            estimated_settlement_price: value
                .estimated_settlement_price
                .map(|value| value.to_string()),
            funding_rate: value.funding_rate.map(|value| value.to_string()),
            next_funding_time_unix_nanos: value
                .next_funding_time_unix_nanos
                .map(|value| value.get()),
            observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            source_id: value.source_id.clone(),
        }),
        Domain::IndexPrice(value) => {
            contract::MarketObservation::IndexPrice(contract::IndexPrice {
                market_id: value.market_id.to_string(),
                instrument_id: value.instrument_id.to_string(),
                spot_index_price: value.spot_index_price.map(|value| value.to_string()),
                contract_index_price: value.contract_index_price.map(|value| value.to_string()),
                index_price: value.index_price.map(|value| value.to_string()),
                funding_rate: value.funding_rate.map(|value| value.to_string()),
                observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
                source_id: value.source_id.clone(),
            })
        }
        Domain::FundingRate(value) => {
            contract::MarketObservation::FundingRate(contract::FundingRate {
                market_id: value.market_id.to_string(),
                instrument_id: value.instrument_id.to_string(),
                funding_rate: value.funding_rate.to_string(),
                funding_period_seconds: value.funding_period_seconds,
                next_funding_time_unix_nanos: value
                    .next_funding_time_unix_nanos
                    .map(|value| value.get()),
                observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
                source_id: value.source_id.clone(),
            })
        }
        Domain::OpenInterest(value) => {
            contract::MarketObservation::OpenInterest(contract::OpenInterest {
                market_id: value.market_id.to_string(),
                instrument_id: value.instrument_id.to_string(),
                contracts: value.contracts.to_string(),
                quote_value: value.quote_value.map(|value| value.to_string()),
                change_24h: value.change_24h.map(|value| value.to_string()),
                change_pct_24h: value.change_pct_24h.map(|value| value.to_string()),
                observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
                source_id: value.source_id.clone(),
            })
        }
        Domain::InstrumentStatus(value) => {
            contract::MarketObservation::InstrumentStatus(contract::InstrumentStatus {
                market_id: value.market_id.to_string(),
                instrument_id: value.instrument_id.to_string(),
                status: value.status.as_str().into(),
                reason: value.reason.clone(),
                effective_at_unix_nanos: value.effective_at_unix_nanos.map(|value| value.get()),
                observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
                source_id: value.source_id.clone(),
            })
        }
    }
}

fn market_contract_orderbook(value: &crate::OrderBook) -> kairos_market_contract::model::OrderBook {
    use kairos_market_contract::model as contract;
    let levels = |values: &[crate::PriceLevel]| {
        values
            .iter()
            .map(|value| contract::PriceLevel {
                price: value.price.to_string(),
                quantity: value.quantity.to_string(),
            })
            .collect()
    };
    contract::OrderBook {
        source_id: value.source_id.clone(),
        market_id: value.market_id.to_string(),
        instrument_id: value.instrument_id.to_string(),
        sequence: value.sequence.get(),
        event_time_unix_nanos: value.event_time_unix_nanos.get(),
        bids: levels(&value.bids),
        asks: levels(&value.asks),
        synchronized: value.synchronized,
        depth_policy: match value.depth_policy {
            crate::domain::orderbook::DepthPolicy::Full => contract::DepthPolicy::Full,
            crate::domain::orderbook::DepthPolicy::TopN(value) => {
                contract::DepthPolicy::TopN(value)
            }
        },
        cursor: contract::DepthCursor {
            first_sequence: value.cursor.first_sequence.get(),
            last_sequence: value.cursor.last_sequence.get(),
            checksum: value.cursor.checksum.clone(),
        },
        checksum: value.checksum.clone(),
    }
}

impl MarketSnapshotPublisher for MmapMarketSnapshotPublisher {
    fn publish(
        &mut self,
        snapshot: &crate::domain::snapshot::MarketCurrentView,
    ) -> Result<(), String> {
        Self::publish(self, snapshot)
    }
}
fn attach_configured_market_source(
    runtime: &mut MarketApplication,
    credentials_root: &std::path::Path,
    source_id: &str,
    binding: &WorkspaceMarketSourceBinding,
) -> Result<(), String> {
    sources::attach_configured(runtime, credentials_root, source_id, binding)
}

/// Canonical endpoint defaults shared by the one-shot CLI and Market server.
pub fn default_endpoint(provider: &str) -> &'static str {
    match provider {
        "binance-spot-websocket" => "wss://stream.binance.com:9443/ws",
        "binance-equity" => "https://api.binance.com",
        "binance-usdm-futures-websocket" => "wss://fstream.binance.com/ws",
        "binance-coinm-futures-websocket" => "wss://dstream.binance.com/ws",
        "binance-usdm-futures-rest" => "https://fapi.binance.com",
        "binance-coinm-futures-rest" => "https://dapi.binance.com",
        "binance-options-rest" => "https://eapi.binance.com",
        // Binance Options market streams are served by the futures stream
        // gateway. The Options-specific host currently returns 404.
        "binance-options-websocket" => "wss://fstream.binance.com/ws",
        "okx-spot-rest" | "okx-swap-rest" | "okx-futures-rest" | "okx-options-rest" => {
            "https://www.okx.com"
        }
        "okx-public-websocket" => "wss://ws.okx.com:8443/ws/v5/public",
        "massive-equity-websocket" => "http://socket.massiveprivateserver.site/stocks",
        "massive-options-websocket" => "http://socket.massiveprivateserver.site/options",
        "hyperliquid-info" => "https://api.hyperliquid.xyz/info",
        "hyperliquid-websocket" => "wss://api.hyperliquid.xyz/ws",
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

pub(super) fn attach_stream<
    C: kairos_integration::application::AsyncMarketEventSource + 'static,
>(
    runtime: &mut MarketApplication,
    source_id: &str,
    exchange: &str,
    market_type: &str,
    asset_type: &str,
    connection: C,
) -> Result<(), String> {
    let descriptor = SourceDescriptor::new(
        SourceId::new(source_id)?,
        kairos_domain_types::Exchange::new(exchange).map_err(|error| error.to_string())?,
        market_type,
        Some(asset_type.into()),
    )?;
    let input_capacity = runtime.source_input_capacity();
    runtime.attach_source(spawn_stream(descriptor, connection, input_capacity))
}

fn attach_binance_stream(
    runtime: &mut MarketApplication,
    source_id: &str,
    market_type: &str,
    asset_type: &str,
    connection: binance::BinanceAsyncMarket,
) -> Result<(), String> {
    let descriptor = SourceDescriptor::new(
        SourceId::new(source_id)?,
        kairos_domain_types::Exchange::new("binance").map_err(|error| error.to_string())?,
        market_type,
        Some(asset_type.into()),
    )?;
    let input_capacity = runtime.source_input_capacity();
    runtime.attach_source(spawn_binance(descriptor, connection, input_capacity))
}

pub(super) fn attach_binance_snapshot<C>(
    runtime: &mut MarketApplication,
    source_id: &str,
    market_type: &str,
    asset_type: &str,
    connection: C,
    interval: std::time::Duration,
) -> Result<(), String>
where
    C: kairos_integration::application::AsyncMarketSnapshotConnection + 'static,
{
    let descriptor = SourceDescriptor::new(
        SourceId::new(source_id)?,
        kairos_domain_types::Exchange::new("binance").map_err(|error| error.to_string())?,
        market_type,
        Some(asset_type.into()),
    )?;
    let input_capacity = runtime.source_input_capacity();
    runtime.attach_source(spawn_snapshot(
        descriptor,
        connection,
        interval,
        input_capacity,
    ))
}

pub fn attach_okx_snapshot_source(
    runtime: &mut MarketApplication,
    source_id: &str,
    market_type: &str,
    asset_type: &str,
    instrument_type: OkxInstrumentType,
    endpoint: impl Into<String>,
    interval: std::time::Duration,
) -> Result<(), String> {
    let provider = OkxConnection::connect(OkxConnectionConfig {
        environment: "public".into(),
        rest_base_url: endpoint.into(),
        shared_quota: None,
    })
    .map_err(|error| error.to_string())?;
    let descriptor = SourceDescriptor::new(
        SourceId::new(source_id)?,
        kairos_domain_types::Exchange::new("okx").map_err(|error| error.to_string())?,
        market_type,
        Some(asset_type.into()),
    )?;
    let handle = spawn_snapshot(
        descriptor,
        provider.market_snapshot(instrument_type),
        interval,
        runtime.source_input_capacity(),
    );
    runtime.attach_source(handle)
}

pub fn attach_okx_live_source(
    runtime: &mut MarketApplication,
    source_id: &str,
    market_type: &str,
    endpoint: impl Into<String>,
) -> Result<(), String> {
    let provider = OkxConnection::connect(OkxConnectionConfig {
        environment: "public".into(),
        rest_base_url: "https://www.okx.com".into(),
        shared_quota: None,
    })
    .map_err(|error| error.to_string())?;
    attach_stream(
        runtime,
        source_id,
        "okx",
        market_type,
        "crypto",
        provider
            .live_market(endpoint)
            .map_err(|error| error.to_string())?,
    )
}

pub fn attach_hyperliquid_snapshot_source(
    runtime: &mut MarketApplication,
    source_id: &str,
    market_type: &str,
    endpoint: impl Into<String>,
    interval: std::time::Duration,
) -> Result<(), String> {
    let provider = HyperliquidConnection::connect(HyperliquidConnectionConfig {
        environment: "public".into(),
        info_endpoint: endpoint.into(),
    })
    .map_err(|error| error.to_string())?;
    let descriptor = SourceDescriptor::new(
        SourceId::new(source_id)?,
        kairos_domain_types::Exchange::new("hyperliquid").map_err(|error| error.to_string())?,
        market_type,
        Some("crypto".into()),
    )?;
    let handle = spawn_snapshot(
        descriptor,
        provider.market_snapshot(),
        interval,
        runtime.source_input_capacity(),
    );
    runtime.attach_source(handle)
}

pub fn attach_hyperliquid_live_source(
    runtime: &mut MarketApplication,
    source_id: &str,
    market_type: &str,
    endpoint: impl Into<String>,
) -> Result<(), String> {
    let provider = HyperliquidConnection::connect(HyperliquidConnectionConfig {
        environment: "public".into(),
        info_endpoint: "https://api.hyperliquid.xyz/info".into(),
    })
    .map_err(|error| error.to_string())?;
    attach_stream(
        runtime,
        source_id,
        "hyperliquid",
        market_type,
        "crypto",
        provider
            .live_market(endpoint)
            .map_err(|error| error.to_string())?,
    )
}

/// Attach the async-first Massive live source to the Actor input channel.
pub fn attach_massive_market_source(
    runtime: &mut MarketApplication,
    product: MarketProduct,
    api_key: impl Into<String>,
    endpoint: impl Into<String>,
) -> Result<(), String> {
    let (source_id, market_type) = match product {
        MarketProduct::Equity => ("massive.public.websocket.equity", "equity"),
        MarketProduct::Options => ("massive.public.websocket.options", "options"),
        _ => return Err("Massive market source requires equity or options product".into()),
    };
    attach_massive_source_with_id(
        runtime,
        source_id,
        "massive",
        market_type,
        "equity",
        product,
        api_key,
        endpoint,
    )
}

#[allow(clippy::too_many_arguments)]
fn attach_massive_source_with_id(
    runtime: &mut MarketApplication,
    source_id: &str,
    exchange: &str,
    route_market_type: &str,
    asset_type: &str,
    product: MarketProduct,
    api_key: impl Into<String>,
    endpoint: impl Into<String>,
) -> Result<(), String> {
    let market_type = match product {
        MarketProduct::Equity => MassiveMarketType::Equity,
        MarketProduct::Options => MassiveMarketType::Option,
        _ => return Err("Massive market source requires equity or options product".into()),
    };
    let endpoint = endpoint.into();
    let provider = MassiveConnection::connect(MassiveConnectionConfig {
        environment: "public".into(),
        rest_base_url: endpoint.clone(),
        api_key: secrecy::SecretString::new(api_key.into().into()),
    })
    .map_err(|error| error.to_string())?;
    let connection = provider
        .live_market(
            market_type,
            endpoint,
            kairos_integration::participants::massive::MassiveChannelConfig {
                event_queue_capacity: 4_096,
            },
        )
        .map_err(|error| error.to_string())?;
    let descriptor = SourceDescriptor::new(
        SourceId::new(source_id)?,
        kairos_domain_types::Exchange::new(exchange).map_err(|error| error.to_string())?,
        route_market_type,
        Some(asset_type.into()),
    )?;
    let handle = spawn_stream(descriptor, connection, runtime.source_input_capacity());
    runtime.attach_source(handle)
}

/// Attach deterministic replay to the same wake-driven Actor input path used
/// by live providers.
pub fn attach_replay_source(
    runtime: &mut MarketApplication,
    events: impl IntoIterator<Item = crate::domain::observations::MarketObservation>,
) -> Result<(), String> {
    attach_replay(runtime, ReplaySource::new(events))
}

pub fn attach_replay_source_with_checkpoint(
    runtime: &mut MarketApplication,
    events: impl IntoIterator<Item = crate::domain::observations::MarketObservation>,
    start_unix_nanos: Option<u64>,
    end_unix_nanos: Option<u64>,
    checkpoint: impl Into<std::path::PathBuf>,
) -> Result<(), String> {
    attach_replay(
        runtime,
        ReplaySource::with_checkpoint(events, start_unix_nanos, end_unix_nanos, checkpoint)?,
    )
}

pub fn attach_replay_source_with_policy(
    runtime: &mut MarketApplication,
    events: impl IntoIterator<Item = crate::domain::observations::MarketObservation>,
    start_unix_nanos: Option<u64>,
    end_unix_nanos: Option<u64>,
    checkpoint: impl Into<std::path::PathBuf>,
    clock: MarketReplayClock,
    speed_multiplier: u32,
    start_paused: bool,
) -> Result<(), String> {
    attach_replay(
        runtime,
        ReplaySource::with_policy(
            events,
            start_unix_nanos,
            end_unix_nanos,
            checkpoint,
            clock,
            speed_multiplier,
            start_paused,
        )?,
    )
}

fn attach_replay(runtime: &mut MarketApplication, source: ReplaySource) -> Result<(), String> {
    let descriptor = SourceDescriptor::all_routes(SourceId::new("replay")?);
    let handle = spawn_replay(descriptor, source, runtime.source_input_capacity());
    runtime.attach_source(handle)
}
