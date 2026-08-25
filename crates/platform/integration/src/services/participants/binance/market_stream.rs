//! Binance-specific planning facts for market WebSocket streams.

use std::collections::BTreeMap;
use std::time::Duration;

use crate::{IntegrationError, MarketDataKind, MarketFeed};

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub(crate) enum StreamRoute {
    Default,
    Public,
    Market,
}

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub(crate) struct StreamShard {
    pub(crate) route: StreamRoute,
    pub(crate) index: u32,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(crate) struct PlannedStream {
    pub(crate) route: StreamRoute,
    pub(crate) name: String,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(crate) struct AssignedStream {
    pub(crate) stream: PlannedStream,
    pub(crate) shard: StreamShard,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct ReconciliationDelta {
    pub(crate) missing: Vec<String>,
    pub(crate) stale: Vec<String>,
}

pub(crate) fn reconciliation_delta(
    desired: &std::collections::BTreeSet<String>,
    actual: &std::collections::BTreeSet<String>,
) -> ReconciliationDelta {
    ReconciliationDelta {
        missing: desired.difference(actual).cloned().collect(),
        stale: actual.difference(desired).cloned().collect(),
    }
}

pub(crate) fn feed_accepts_event(feed: &MarketFeed, event: &crate::MarketEvent) -> bool {
    let same_symbol = feed
        .symbol
        .as_ref()
        .is_some_and(|symbol| symbol.as_str().eq_ignore_ascii_case(event.symbol.as_str()));
    same_symbol
        && matches!(
            (feed.kind, event.kind),
            (MarketDataKind::Quote, crate::MarketEventKind::Quote)
                | (MarketDataKind::Trade, crate::MarketEventKind::Trade)
                | (MarketDataKind::Bar, crate::MarketEventKind::Bar)
                | (
                    MarketDataKind::TradeBar,
                    crate::MarketEventKind::TradeBar | crate::MarketEventKind::Bar
                )
                | (
                    MarketDataKind::QuoteBar,
                    crate::MarketEventKind::QuoteBar | crate::MarketEventKind::Bar
                )
                | (
                    MarketDataKind::OrderBook,
                    crate::MarketEventKind::BookSnapshot | crate::MarketEventKind::BookDelta
                )
                | (MarketDataKind::Greeks, crate::MarketEventKind::Greeks)
                | (MarketDataKind::Ticker24h, crate::MarketEventKind::Ticker24h)
                | (MarketDataKind::MarkPrice, crate::MarketEventKind::MarkPrice)
                | (
                    MarketDataKind::IndexPrice,
                    crate::MarketEventKind::IndexPrice
                )
                | (
                    MarketDataKind::FundingRate,
                    crate::MarketEventKind::FundingRate
                )
                | (
                    MarketDataKind::OpenInterest,
                    crate::MarketEventKind::OpenInterest
                )
                | (
                    MarketDataKind::InstrumentStatus,
                    crate::MarketEventKind::InstrumentStatus
                )
        )
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct MarketStreamPolicy {
    family: &'static str,
    endpoints: BTreeMap<StreamRoute, String>,
    max_streams_per_socket: usize,
    max_incoming_messages_per_second: u32,
    session_lifetime: Duration,
}

impl MarketStreamPolicy {
    pub(crate) fn new(
        family: &'static str,
        configured_endpoint: &str,
    ) -> Result<Self, IntegrationError> {
        let configured_endpoint = configured_endpoint.trim().trim_end_matches('/');
        if configured_endpoint.is_empty() {
            return Err(IntegrationError::InvalidRequest(format!(
                "Binance {family} WebSocket endpoint is required"
            )));
        }
        let (endpoints, max_streams_per_socket, max_incoming_messages_per_second) = match family {
            "usdm" | "options" => {
                validate_routed_base(family, configured_endpoint)?;
                let mut endpoints = BTreeMap::new();
                endpoints.insert(
                    StreamRoute::Public,
                    format!("{configured_endpoint}/public/ws"),
                );
                endpoints.insert(
                    StreamRoute::Market,
                    format!("{configured_endpoint}/market/ws"),
                );
                let maximum = if family == "options" { 200 } else { 1_024 };
                (endpoints, maximum, 10)
            },
            "spot" => (
                BTreeMap::from([(StreamRoute::Default, configured_endpoint.to_owned())]),
                1_024,
                5,
            ),
            "coinm" => (
                BTreeMap::from([(StreamRoute::Default, configured_endpoint.to_owned())]),
                1_024,
                10,
            ),
            // Margin and Alpha currently use the Spot-style dynamic-subscription
            // transport. They retain their configured endpoint until their own
            // protocol policy has a demonstrated production difference.
            "margin" | "alpha" => (
                BTreeMap::from([(StreamRoute::Default, configured_endpoint.to_owned())]),
                1_024,
                5,
            ),
            unsupported => {
                return Err(IntegrationError::InvalidRequest(format!(
                    "unsupported Binance market stream family: {unsupported}"
                )));
            },
        };
        Ok(Self {
            family,
            endpoints,
            max_streams_per_socket,
            max_incoming_messages_per_second,
            session_lifetime: Duration::from_secs(24 * 60 * 60),
        })
    }

    pub(crate) fn family(&self) -> &'static str {
        self.family
    }

    #[cfg(test)]
    pub(crate) fn endpoints(&self) -> &BTreeMap<StreamRoute, String> {
        &self.endpoints
    }

    pub(crate) fn endpoint(&self, route: StreamRoute) -> Result<&str, IntegrationError> {
        self.endpoints
            .get(&route)
            .map(String::as_str)
            .ok_or_else(|| {
                IntegrationError::InvalidRequest(format!(
                    "Binance {} does not support stream route {route:?}",
                    self.family
                ))
            })
    }

    pub(crate) fn initial_shards(&self) -> impl Iterator<Item = (StreamShard, &str)> {
        self.endpoints.iter().map(|(route, endpoint)| {
            (
                StreamShard {
                    route: *route,
                    index: 0,
                },
                endpoint.as_str(),
            )
        })
    }

    #[cfg(test)]
    pub(crate) fn shard_plan(
        &self,
        streams: impl IntoIterator<Item = PlannedStream>,
    ) -> BTreeMap<StreamShard, Vec<String>> {
        let mut by_route = BTreeMap::<StreamRoute, std::collections::BTreeSet<String>>::new();
        for stream in streams {
            by_route
                .entry(stream.route)
                .or_default()
                .insert(stream.name);
        }
        let mut result = BTreeMap::new();
        for (route, streams) in by_route {
            let streams = streams.into_iter().collect::<Vec<_>>();
            for (index, chunk) in streams.chunks(self.max_streams_per_socket).enumerate() {
                result.insert(
                    StreamShard {
                        route,
                        index: u32::try_from(index).unwrap_or(u32::MAX),
                    },
                    chunk.to_vec(),
                );
            }
        }
        result
    }

    pub(crate) fn max_streams_per_socket(&self) -> usize {
        self.max_streams_per_socket
    }

    pub(crate) fn max_incoming_messages_per_second(&self) -> u32 {
        self.max_incoming_messages_per_second
    }

    pub(crate) fn session_lifetime(&self) -> Duration {
        self.session_lifetime
    }

    pub(crate) fn plan(&self, feed: &MarketFeed) -> Result<PlannedStream, IntegrationError> {
        plan(feed, self.family)
    }
}

fn validate_routed_base(family: &str, endpoint: &str) -> Result<(), IntegrationError> {
    let path = endpoint
        .split_once("://")
        .map(|(_, suffix)| suffix)
        .unwrap_or(endpoint)
        .split_once('/')
        .map(|(_, path)| path)
        .unwrap_or_default();
    if !path.is_empty() {
        return Err(IntegrationError::InvalidRequest(format!(
            "Binance {family} WebSocket endpoint must be the product base URL; legacy or routed path `{path}` is not accepted"
        )));
    }
    Ok(())
}

fn plan(feed: &MarketFeed, family: &str) -> Result<PlannedStream, IntegrationError> {
    let symbol = feed.symbol.as_ref().ok_or_else(|| {
        IntegrationError::InvalidRequest(format!("Binance {:?} feed requires a symbol", feed.kind))
    })?;
    let symbol = symbol.as_str().to_ascii_lowercase();
    let (route, name) = match family {
        "spot" | "margin" | "alpha" => (StreamRoute::Default, spot_channel(feed, family)?),
        "usdm" => usdm_channel(feed)?,
        "coinm" => (StreamRoute::Default, futures_channel(feed, family)?),
        "options" => return options_stream(feed, &symbol),
        unsupported => {
            return Err(IntegrationError::InvalidRequest(format!(
                "unsupported Binance market stream family: {unsupported}"
            )));
        },
    };
    Ok(PlannedStream {
        route,
        name: format!("{symbol}@{name}"),
    })
}

fn spot_channel(feed: &MarketFeed, family: &str) -> Result<String, IntegrationError> {
    match feed.kind {
        MarketDataKind::Quote => Ok("bookTicker".into()),
        MarketDataKind::Ticker24h => Ok("ticker".into()),
        MarketDataKind::Trade => Ok("trade".into()),
        MarketDataKind::OrderBook => Ok(depth_channel(feed, false)),
        MarketDataKind::Bar | MarketDataKind::TradeBar | MarketDataKind::QuoteBar => {
            Ok(kline_channel(feed))
        },
        unsupported => unsupported_feed(family, unsupported),
    }
}

fn usdm_channel(feed: &MarketFeed) -> Result<(StreamRoute, String), IntegrationError> {
    match feed.kind {
        MarketDataKind::Quote => Ok((StreamRoute::Public, "bookTicker".into())),
        MarketDataKind::OrderBook => Ok((StreamRoute::Public, depth_channel(feed, false))),
        MarketDataKind::Ticker24h => Ok((StreamRoute::Market, "ticker".into())),
        MarketDataKind::Trade => Ok((StreamRoute::Market, "aggTrade".into())),
        MarketDataKind::Bar | MarketDataKind::TradeBar | MarketDataKind::QuoteBar => {
            Ok((StreamRoute::Market, kline_channel(feed)))
        },
        MarketDataKind::MarkPrice | MarketDataKind::FundingRate => {
            Ok((StreamRoute::Market, "markPrice".into()))
        },
        unsupported => unsupported_feed("usdm", unsupported),
    }
}

fn futures_channel(feed: &MarketFeed, family: &str) -> Result<String, IntegrationError> {
    match feed.kind {
        MarketDataKind::Quote => Ok("bookTicker".into()),
        MarketDataKind::Ticker24h => Ok("ticker".into()),
        MarketDataKind::Trade => Ok("aggTrade".into()),
        MarketDataKind::OrderBook => Ok(depth_channel(feed, false)),
        MarketDataKind::Bar | MarketDataKind::TradeBar | MarketDataKind::QuoteBar => {
            Ok(kline_channel(feed))
        },
        MarketDataKind::MarkPrice | MarketDataKind::FundingRate => Ok("markPrice".into()),
        MarketDataKind::IndexPrice => Ok("indexPrice".into()),
        unsupported => unsupported_feed(family, unsupported),
    }
}

fn options_stream(feed: &MarketFeed, contract: &str) -> Result<PlannedStream, IntegrationError> {
    let contract_parts = contract.split('-').collect::<Vec<_>>();
    let underlying = if contract_parts.len() >= 2 {
        format!("{}usdt", contract_parts[0])
    } else {
        contract.to_owned()
    };
    let (route, name) = match feed.kind {
        MarketDataKind::Quote => (StreamRoute::Public, format!("{contract}@bookTicker")),
        MarketDataKind::Ticker24h => {
            let expiration = contract_parts.get(1).ok_or_else(|| {
                IntegrationError::InvalidRequest(
                    "Binance options ticker requires a contract symbol containing its expiration"
                        .into(),
                )
            })?;
            (
                StreamRoute::Public,
                format!("{underlying}@optionTicker@{expiration}"),
            )
        },
        MarketDataKind::Greeks | MarketDataKind::MarkPrice => {
            (StreamRoute::Market, format!("{underlying}@optionMarkPrice"))
        },
        MarketDataKind::OrderBook => (
            StreamRoute::Public,
            format!("{contract}@{}", depth_channel(feed, true)),
        ),
        MarketDataKind::Trade => (StreamRoute::Public, format!("{underlying}@optionTrade")),
        MarketDataKind::Bar | MarketDataKind::TradeBar | MarketDataKind::QuoteBar => (
            StreamRoute::Market,
            format!("{contract}@{}", kline_channel(feed)),
        ),
        unsupported => return unsupported_feed("options", unsupported),
    };
    Ok(PlannedStream { route, name })
}

fn depth_channel(feed: &MarketFeed, depth_required: bool) -> String {
    let depth = if depth_required {
        feed.depth.or(Some(20))
    } else {
        feed.depth
    };
    let mut channel = depth.map_or_else(|| "depth".into(), |depth| format!("depth{depth}"));
    if let Some(speed) = feed.update_speed_millis {
        channel.push_str(&format!("@{speed}ms"));
    } else if depth_required {
        channel.push_str("@100ms");
    }
    channel
}

fn kline_channel(feed: &MarketFeed) -> String {
    format!("kline_{}", feed.interval.as_deref().unwrap_or("1m"))
}

fn unsupported_feed<T>(family: &str, kind: MarketDataKind) -> Result<T, IntegrationError> {
    Err(IntegrationError::InvalidRequest(format!(
        "Binance {family} WebSocket does not support {kind:?}"
    )))
}

#[cfg(test)]
mod tests {
    use kairos_primitives::integration::ParticipantSymbol;

    use super::*;

    fn feed(kind: MarketDataKind) -> MarketFeed {
        MarketFeed {
            kind,
            symbol: Some(ParticipantSymbol::new("BTCUSDT").unwrap()),
            interval: None,
            depth: None,
            update_speed_millis: None,
        }
    }

    #[test]
    fn usdm_splits_public_and_market_streams() {
        let policy = MarketStreamPolicy::new("usdm", "wss://fstream.binance.com").unwrap();

        assert_eq!(
            policy.plan(&feed(MarketDataKind::OrderBook)).unwrap(),
            PlannedStream {
                route: StreamRoute::Public,
                name: "btcusdt@depth".into(),
            }
        );
        assert_eq!(
            policy.plan(&feed(MarketDataKind::MarkPrice)).unwrap(),
            PlannedStream {
                route: StreamRoute::Market,
                name: "btcusdt@markPrice".into(),
            }
        );
        assert_eq!(
            policy.endpoints()[&StreamRoute::Public],
            "wss://fstream.binance.com/public/ws"
        );
        assert_eq!(
            policy.endpoints()[&StreamRoute::Market],
            "wss://fstream.binance.com/market/ws"
        );
    }

    #[test]
    fn routed_products_reject_legacy_or_preselected_paths() {
        for endpoint in [
            "wss://fstream.binance.com/ws",
            "wss://fstream.binance.com/stream",
            "wss://fstream.binance.com/public",
        ] {
            assert!(MarketStreamPolicy::new("usdm", endpoint).is_err());
        }
    }

    #[test]
    fn options_use_current_public_and_market_stream_names() {
        let policy = MarketStreamPolicy::new("options", "wss://fstream.binance.com").unwrap();
        let option_feed = |kind| MarketFeed {
            kind,
            symbol: Some(ParticipantSymbol::new("BTC-260925-100000-C").unwrap()),
            interval: None,
            depth: None,
            update_speed_millis: None,
        };

        assert_eq!(
            policy.plan(&option_feed(MarketDataKind::Quote)).unwrap(),
            PlannedStream {
                route: StreamRoute::Public,
                name: "btc-260925-100000-c@bookTicker".into(),
            }
        );
        assert_eq!(
            policy.plan(&option_feed(MarketDataKind::Trade)).unwrap(),
            PlannedStream {
                route: StreamRoute::Public,
                name: "btcusdt@optionTrade".into(),
            }
        );
        assert_eq!(
            policy
                .plan(&option_feed(MarketDataKind::Ticker24h))
                .unwrap(),
            PlannedStream {
                route: StreamRoute::Public,
                name: "btcusdt@optionTicker@260925".into(),
            }
        );
        let mark = policy
            .plan(&option_feed(MarketDataKind::MarkPrice))
            .unwrap();
        assert_eq!(
            mark,
            policy.plan(&option_feed(MarketDataKind::Greeks)).unwrap()
        );
        assert_eq!(mark.route, StreamRoute::Market);
        assert_eq!(mark.name, "btcusdt@optionMarkPrice");
        assert_eq!(policy.max_streams_per_socket(), 200);
    }

    #[test]
    fn futures_trade_uses_aggregate_trade_stream() {
        for family in ["usdm", "coinm"] {
            let endpoint = if family == "usdm" {
                "wss://fstream.binance.com"
            } else {
                "wss://dstream.binance.com/ws"
            };
            assert_eq!(
                MarketStreamPolicy::new(family, endpoint)
                    .unwrap()
                    .plan(&feed(MarketDataKind::Trade))
                    .unwrap()
                    .name,
                "btcusdt@aggTrade"
            );
        }
    }

    #[test]
    fn published_connection_limits_are_typed_policy() {
        let spot = MarketStreamPolicy::new("spot", "wss://stream.binance.com:9443/ws").unwrap();
        let coinm = MarketStreamPolicy::new("coinm", "wss://dstream.binance.com/ws").unwrap();

        assert_eq!(spot.max_streams_per_socket(), 1_024);
        assert_eq!(spot.max_incoming_messages_per_second(), 5);
        assert_eq!(coinm.max_streams_per_socket(), 1_024);
        assert_eq!(coinm.max_incoming_messages_per_second(), 10);
        assert_eq!(spot.session_lifetime(), Duration::from_secs(86_400));
    }

    #[test]
    fn capacity_plan_creates_deterministic_product_shards() {
        let options = MarketStreamPolicy::new("options", "wss://fstream.binance.com").unwrap();
        let streams = (0..401)
            .map(|index| PlannedStream {
                route: StreamRoute::Public,
                name: format!("option-{index:04}@ticker"),
            })
            .collect::<Vec<_>>();

        let plan = options.shard_plan(streams);

        assert_eq!(plan.len(), 3);
        assert_eq!(
            plan[&StreamShard {
                route: StreamRoute::Public,
                index: 0
            }]
                .len(),
            200
        );
        assert_eq!(
            plan[&StreamShard {
                route: StreamRoute::Public,
                index: 1
            }]
                .len(),
            200
        );
        assert_eq!(
            plan[&StreamShard {
                route: StreamRoute::Public,
                index: 2
            }]
                .len(),
            1
        );
    }

    #[test]
    fn reconciliation_repairs_both_missing_and_stale_streams() {
        let desired = ["btcusdt@depth", "ethusdt@depth"]
            .into_iter()
            .map(str::to_owned)
            .collect();
        let actual = ["btcusdt@depth", "solusdt@depth"]
            .into_iter()
            .map(str::to_owned)
            .collect();

        assert_eq!(
            reconciliation_delta(&desired, &actual),
            ReconciliationDelta {
                missing: vec!["ethusdt@depth".into()],
                stale: vec!["solusdt@depth".into()],
            }
        );
    }
}
