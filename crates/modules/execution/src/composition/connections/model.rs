use super::*;

#[derive(Default)]
pub struct SimulatedOrderEntry;

/// Explicit provider environment selected for one Execution route.
///
/// It is deliberately not inferred from endpoint text: REST and private
/// streams can use unrelated hostnames, so guessing can cross the test/live
/// safety boundary.
#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionVenueEnvironment {
    Live,
    Testnet,
    Demo,
    Paper,
}

impl ExecutionVenueEnvironment {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Live => "live",
            Self::Testnet => "testnet",
            Self::Demo => "demo",
            Self::Paper => "paper",
        }
    }

    pub const fn is_external_non_live(self) -> bool {
        matches!(self, Self::Testnet | Self::Demo)
    }
}

#[derive(Clone, Debug)]
pub struct ExecutionConnectionOptions {
    /// Business route identity. It is never sent to Integration or a provider.
    pub route_id: String,
    /// Required routes gate process readiness. Optional routes may start and
    /// recover independently while the process reports degraded.
    pub required: bool,
    /// Business account and segment served by this route.
    pub account_id: String,
    pub segment_key: String,
    /// Integration participant selected for this route (for example an
    /// exchange such as `binance` or a broker such as `ibkr`).  This is not
    /// an Account-owned broker identity and must not be used as a generic
    /// vendor/provider bucket.
    pub broker_id: String,
    /// Execution channel. For OKX this remains independent from the
    /// order/account trading mode below.
    pub execution_channel: String,
    pub environment: ExecutionVenueEnvironment,
    pub trading_mode: Option<String>,
    pub api_key: SecretString,
    pub secret: SecretString,
    pub passphrase: SecretString,
    pub base_url: String,
    pub websocket_url: String,
    /// Order-entry symbol required by Binance isolated-margin listen-key scope.
    pub isolated_symbol: Option<String>,
    /// Execution-owned participant addresses. Broker and smart-routed products
    /// may target an Instrument without claiming a canonical destination.
    pub instruments: Vec<ExecutionInstrumentRoute>,
    pub request_weight_per_minute: u32,
    pub cancel_reserve_weight: u32,
    pub order_event_queue_capacity: usize,
    pub shared_quota_ledger_path: Option<PathBuf>,
    pub egress_scope_id: String,
    pub principal_scope_id: String,
    pub orders_per_10_seconds: u32,
    pub orders_per_day: u32,
    pub host: String,
    pub port: u16,
    pub client_id: i32,
    pub initial_margin_rate_bps: Option<u32>,
    pub margin_rule_id: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutionInstrumentRoute {
    pub instrument_id: String,
    pub order_entry_symbol: String,
    pub destination_market_id: Option<String>,
}

/// Resolve Execution-owned route candidates from configured connections and
/// canonical Reference identity. The execution channel and order-entry symbol remain owned by
/// the configured Execution route; Reference never supplies broker coverage.
pub fn load_execution_routes_from_reference_markets(
    catalog: &kairos_reference_contract::MarketSearchResponse,
    configured_routes: &[ExecutionConnectionOptions],
) -> Result<
    Vec<(
        crate::application::ExecutionRouteCandidate,
        ParticipantInstrumentRef,
    )>,
    String,
> {
    let mut candidates = Vec::new();
    for configured in configured_routes {
        if !configured.instruments.is_empty() {
            for address in &configured.instruments {
                let instrument = catalog
                    .instruments
                    .values()
                    .find(|value| value.instrument_id == address.instrument_id)
                    .ok_or_else(|| {
                        format!(
                            "execution route {} references missing instrument {}",
                            configured.route_id, address.instrument_id
                        )
                    })?;
                let destination = address
                    .destination_market_id
                    .as_deref()
                    .map(|market_id| {
                        catalog
                            .markets
                            .iter()
                            .find(|market| market.market_id == market_id)
                            .filter(|market| market.instrument_id == instrument.instrument_id)
                            .ok_or_else(|| {
                                format!(
                                    "execution route {} destination {market_id} does not resolve to instrument {}",
                                    configured.route_id, instrument.instrument_id
                                )
                            })
                    })
                    .transpose()?;
                candidates.push(candidate_for_address(
                    configured,
                    &instrument.instrument_id,
                    destination.map(|market| market.market_id.as_str()),
                    &address.order_entry_symbol,
                )?);
            }
            continue;
        }
        for market in catalog.markets.iter().filter(|market| {
            let instrument_kind = catalog
                .instruments
                .get(&market.instrument_id)
                .map(|instrument| instrument.instrument_type);
            matches!(market.status.as_str(), "active" | "trading")
                && canonical_venue_matches_participant(
                    market.execution_venue_id.as_str(),
                    &configured.broker_id,
                )
                && instrument_kind.is_some_and(|kind| {
                    route_product_supports_instrument_kind(&configured.execution_channel, kind)
                })
                && market.venue_symbol.is_some()
        }) {
            let order_entry_symbol = market
                .venue_symbol
                .as_deref()
                .expect("filtered venue symbol");
            let participant_instrument = participant_instrument_for_route(
                &configured.broker_id,
                &configured.execution_channel,
                order_entry_symbol,
            )?;
            let route_id = kairos_primitives::execution::ExecutionRouteId::new(format!(
                "{}:{}",
                configured.route_id, market.market_id
            ))
            .map_err(|error| error.to_string())?;
            candidates.push((
                crate::application::ExecutionRouteCandidate {
                    route_id,
                    account_id: Some(
                        kairos_primitives::account::AccountId::new(&configured.account_id)
                            .map_err(|error| error.to_string())?,
                    ),
                    segment_key: Some(
                        kairos_primitives::account::SegmentKey::new(&configured.segment_key)
                            .map_err(|error| error.to_string())?,
                    ),
                    instrument_id: Some(market.instrument_id.clone()),
                    market_id: Some(market.market_id.clone()),
                    broker_id: kairos_primitives::account::BrokerId::new(&configured.broker_id)
                        .map_err(|error| error.to_string())?,
                    execution_channel: kairos_primitives::execution::ExecutionChannelCode::new(
                        &configured.execution_channel,
                    )
                    .map_err(|error| error.to_string())?,
                    order_entry_symbol: kairos_primitives::execution::OrderEntrySymbol::new(
                        order_entry_symbol,
                    )
                    .map_err(|error| error.to_string())?,
                    supported_order_types: vec![
                        crate::application::OrderType::Market,
                        crate::application::OrderType::Limit,
                    ],
                    supported_options: supported_order_options(
                        &configured.broker_id,
                        &configured.execution_channel,
                    ),
                    ready: true,
                    initial_margin_rate_bps: margin_rule(configured).map(|value| value.0),
                    margin_rule_id: margin_rule(configured).map(|value| value.1),
                },
                participant_instrument,
            ));
        }
    }
    Ok(candidates)
}

pub(super) fn candidate_for_address(
    configured: &ExecutionConnectionOptions,
    instrument_id: &str,
    destination_market_id: Option<&str>,
    order_entry_symbol: &str,
) -> Result<
    (
        crate::application::ExecutionRouteCandidate,
        ParticipantInstrumentRef,
    ),
    String,
> {
    let participant_instrument = participant_instrument_for_route(
        &configured.broker_id,
        &configured.execution_channel,
        order_entry_symbol,
    )?;
    let route_id = kairos_primitives::execution::ExecutionRouteId::new(format!(
        "{}:{}",
        configured.route_id, instrument_id
    ))
    .map_err(|error| error.to_string())?;
    Ok((
        crate::application::ExecutionRouteCandidate {
            route_id,
            account_id: Some(
                kairos_primitives::account::AccountId::new(&configured.account_id)
                    .map_err(|error| error.to_string())?,
            ),
            segment_key: Some(
                kairos_primitives::account::SegmentKey::new(&configured.segment_key)
                    .map_err(|error| error.to_string())?,
            ),
            instrument_id: Some(
                kairos_primitives::reference::InstrumentId::new(instrument_id)
                    .map_err(|error| error.to_string())?,
            ),
            market_id: destination_market_id
                .map(kairos_primitives::reference::MarketId::new)
                .transpose()
                .map_err(|error| error.to_string())?,
            broker_id: kairos_primitives::account::BrokerId::new(&configured.broker_id)
                .map_err(|error| error.to_string())?,
            execution_channel: kairos_primitives::execution::ExecutionChannelCode::new(
                &configured.execution_channel,
            )
            .map_err(|error| error.to_string())?,
            order_entry_symbol: kairos_primitives::execution::OrderEntrySymbol::new(
                order_entry_symbol,
            )
            .map_err(|error| error.to_string())?,
            supported_order_types: vec![
                crate::application::OrderType::Market,
                crate::application::OrderType::Limit,
            ],
            supported_options: supported_order_options(
                &configured.broker_id,
                &configured.execution_channel,
            ),
            ready: true,
            initial_margin_rate_bps: margin_rule(configured).map(|value| value.0),
            margin_rule_id: margin_rule(configured).map(|value| value.1),
        },
        participant_instrument,
    ))
}

fn margin_rule(
    configured: &ExecutionConnectionOptions,
) -> Option<(u32, kairos_primitives::risk::MarginRuleCode)> {
    match (
        configured.initial_margin_rate_bps,
        configured.margin_rule_id.as_ref(),
    ) {
        (Some(rate), Some(id)) if rate > 0 && rate <= 10_000 && !id.trim().is_empty() => {
            kairos_primitives::risk::MarginRuleCode::new(id)
                .ok()
                .map(|id| (rate, id))
        },
        (None, None) if configured.execution_channel.eq_ignore_ascii_case("spot") => {
            kairos_primitives::risk::MarginRuleCode::new(format!(
                "route:{}:fully-funded",
                configured.route_id
            ))
            .ok()
            .map(|id| (10_000, id))
        },
        _ => None,
    }
}

fn canonical_venue_matches_participant(exchange_id: &str, broker_id: &str) -> bool {
    let venue = exchange_id
        .strip_prefix("venue:")
        .or_else(|| exchange_id.strip_prefix("exchange:"))
        .unwrap_or(exchange_id);
    venue.eq_ignore_ascii_case(broker_id)
        || (broker_id.eq_ignore_ascii_case("okex") && venue.eq_ignore_ascii_case("okx"))
}

fn route_product_supports_instrument_kind(
    execution_channel: &str,
    kind: kairos_primitives::reference::InstrumentKind,
) -> bool {
    use kairos_primitives::reference::InstrumentKind::{Future, Option, Perpetual, Spot};
    match execution_channel.trim().to_ascii_lowercase().as_str() {
        "spot" | "margin" => kind == Spot,
        "swap" | "perpetual" | "usd-m-futures" | "coin-m-futures" => {
            matches!(kind, Perpetual | Future)
        },
        "future" | "futures" => kind == Future,
        "option" | "options" => kind == Option,
        _ => false,
    }
}

fn supported_order_options(broker_id: &str, execution_channel: &str) -> Vec<String> {
    let values: &[&str] = match broker_id.trim().to_ascii_lowercase().as_str() {
        "binance" if execution_channel.eq_ignore_ascii_case("spot") => {
            &["time_in_force", "post_only", "quote_asset"]
        },
        "binance" => &["time_in_force", "reduce_only", "post_only", "position_side"],
        "okx" | "okex" => &["time_in_force", "reduce_only", "post_only", "position_side"],
        "ibkr" => &["time_in_force", "trading_session"],
        "simulated" => &[
            "time_in_force",
            "reduce_only",
            "post_only",
            "position_side",
            "quote_asset",
            "wallet_type",
            "trading_session",
            "tokenize",
        ],
        _ => &[],
    };
    values.iter().map(|value| (*value).to_owned()).collect()
}

pub(super) fn participant_instrument_for_route(
    broker_id: &str,
    execution_channel: &str,
    order_entry_symbol: &str,
) -> Result<ParticipantInstrumentRef, String> {
    let participant_kind = match (broker_id, execution_channel) {
        ("binance", "equity") => ParticipantKind::Broker,
        ("binance" | "okx" | "hyperliquid", _) => ParticipantKind::Exchange,
        ("ibkr", _) => ParticipantKind::Broker,
        (provider, _) => {
            return Err(format!(
                "unsupported execution route participant: {provider}"
            ));
        },
    };
    ParticipantInstrumentRef::new(
        ParticipantRef::new(participant_kind, broker_id).map_err(|error| error.to_string())?,
        Some(
            ParticipantInstrumentTypeRef::new(execution_channel)
                .map_err(|error| error.to_string())?,
        ),
        order_entry_symbol,
    )
    .map_err(|error| error.to_string())
}
