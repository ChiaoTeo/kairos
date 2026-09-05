use kairos_indexed_view::{MetadataSnapshot, RebuildState};
use kairos_primitives::decimal::{DecimalParts, Price, PriceDelta, Quantity};
use kairos_primitives::market::Provider;
use kairos_primitives::reference::{InstrumentId, MarketId, VenueId};
use kairos_primitives::time::{Sequence, UnixNanos};
use kairos_protocol::generated::kairos::common::v_2::Decimal64;
use kairos_protocol::generated::kairos::market::v_2 as fb;

use super::{
    MARKET_BARS_DATABASE, MARKET_FRESHNESS_DATABASE, MARKET_FUNDING_RATES_DATABASE,
    MARKET_GREEKS_DATABASE, MARKET_INDEX_PRICES_DATABASE, MARKET_MARK_PRICES_DATABASE,
    MARKET_OPEN_INTEREST_DATABASE, MARKET_ORDER_BOOKS_DATABASE, MARKET_QUOTES_DATABASE,
    MARKET_RATES_DATABASE, MARKET_TICKERS_DATABASE, MarketIndexedView, MarketViewKey,
    MarketViewKind, market_indexed_key, validate_current_identity,
};
use crate::{ContractError, ContractResult};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketCurrentEvidence {
    pub resource_epoch: u64,
    pub producer_incarnation: u64,
    pub applied_event_sequence: Sequence,
    pub committed_at: UnixNanos,
    pub source_event_id: Option<String>,
    pub synchronized: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum MarketObservationScope {
    Market(MarketId),
    Consolidated {
        instrument_id: InstrumentId,
        network_id: Option<String>,
    },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MarketBarKind {
    Trades,
    Quotes,
    Midpoint,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketQuoteCurrent {
    pub evidence: MarketCurrentEvidence,
    pub quote_id: Option<String>,
    pub scope: MarketObservationScope,
    pub instrument_id: InstrumentId,
    pub provider: Provider,
    pub bid_price: Option<Price>,
    pub bid_quantity: Option<Quantity>,
    pub ask_price: Option<Price>,
    pub ask_quantity: Option<Quantity>,
    pub bid_venue_id: Option<VenueId>,
    pub ask_venue_id: Option<VenueId>,
    pub bid_venue_code: Option<String>,
    pub ask_venue_code: Option<String>,
    pub tape: Option<u32>,
    pub source_observed_at: UnixNanos,
    pub received_at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketBarCurrent {
    pub evidence: MarketCurrentEvidence,
    pub scope: MarketObservationScope,
    pub instrument_id: InstrumentId,
    pub provider: Provider,
    pub bar_spec_id: String,
    pub kind: MarketBarKind,
    pub window_start: UnixNanos,
    pub window_end: UnixNanos,
    pub open: Price,
    pub high: Price,
    pub low: Price,
    pub close: Price,
    pub volume: Option<Quantity>,
    pub source_observed_at: UnixNanos,
    pub received_at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketGreeksCurrent {
    pub evidence: MarketCurrentEvidence,
    pub scope: MarketObservationScope,
    pub instrument_id: InstrumentId,
    pub provider: Provider,
    pub expiry: Option<UnixNanos>,
    pub strike: Option<Price>,
    pub delta: Option<DecimalParts>,
    pub gamma: Option<DecimalParts>,
    pub vega: Option<DecimalParts>,
    pub theta: Option<DecimalParts>,
    pub implied_volatility: Option<DecimalParts>,
    pub source_observed_at: UnixNanos,
    pub received_at: UnixNanos,
    pub derivation_id: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketRateCurrent {
    pub evidence: MarketCurrentEvidence,
    pub rate_id: String,
    pub scope: MarketObservationScope,
    pub instrument_id: InstrumentId,
    pub provider: Provider,
    pub basis: String,
    pub value: DecimalParts,
    pub mark_price: Option<Price>,
    pub source_observed_at: UnixNanos,
    pub received_at: UnixNanos,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketTicker24hCurrent {
    pub evidence: MarketCurrentEvidence,
    pub scope: MarketObservationScope,
    pub instrument_id: InstrumentId,
    pub provider: Provider,
    pub last_price: Option<Price>,
    pub bid_price: Option<Price>,
    pub bid_quantity: Option<Quantity>,
    pub ask_price: Option<Price>,
    pub ask_quantity: Option<Quantity>,
    pub open_price: Option<Price>,
    pub high_price: Option<Price>,
    pub low_price: Option<Price>,
    pub volume_base: Option<Quantity>,
    pub volume_quote: Option<DecimalParts>,
    pub price_change_abs: Option<PriceDelta>,
    pub price_change_pct: Option<DecimalParts>,
    pub vwap: Option<Price>,
    pub mark_price: Option<Price>,
    pub source_observed_at: UnixNanos,
    pub received_at: UnixNanos,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketMarkPriceCurrent {
    pub evidence: MarketCurrentEvidence,
    pub scope: MarketObservationScope,
    pub instrument_id: InstrumentId,
    pub provider: Provider,
    pub mark_price: Price,
    pub index_price: Option<Price>,
    pub estimated_settlement_price: Option<Price>,
    pub funding_rate: Option<DecimalParts>,
    pub next_funding_time: Option<UnixNanos>,
    pub source_observed_at: UnixNanos,
    pub received_at: UnixNanos,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketFundingRateCurrent {
    pub evidence: MarketCurrentEvidence,
    pub scope: MarketObservationScope,
    pub instrument_id: InstrumentId,
    pub provider: Provider,
    pub funding_rate: DecimalParts,
    pub funding_period_seconds: u64,
    pub next_funding_time: Option<UnixNanos>,
    pub source_observed_at: UnixNanos,
    pub received_at: UnixNanos,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketOpenInterestCurrent {
    pub evidence: MarketCurrentEvidence,
    pub scope: MarketObservationScope,
    pub instrument_id: InstrumentId,
    pub provider: Provider,
    pub contracts: Quantity,
    pub quote_value: Option<DecimalParts>,
    pub change_24h: Option<DecimalParts>,
    pub change_pct_24h: Option<DecimalParts>,
    pub source_observed_at: UnixNanos,
    pub received_at: UnixNanos,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketIndexPriceCurrent {
    pub evidence: MarketCurrentEvidence,
    pub scope: MarketObservationScope,
    pub instrument_id: InstrumentId,
    pub provider: Provider,
    pub spot_index_price: Option<Price>,
    pub contract_index_price: Option<Price>,
    pub index_price: Option<Price>,
    pub funding_rate: Option<DecimalParts>,
    pub source_observed_at: UnixNanos,
    pub received_at: UnixNanos,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketOrderBookLevel {
    pub price: Price,
    pub quantity: Quantity,
    pub order_count: u32,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketOrderBookCurrent {
    pub evidence: MarketCurrentEvidence,
    pub provider: Provider,
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub sequence: Sequence,
    pub source_observed_at: UnixNanos,
    pub received_at: UnixNanos,
    pub checksum: Option<String>,
    pub depth_policy: String,
    pub bids: Vec<MarketOrderBookLevel>,
    pub asks: Vec<MarketOrderBookLevel>,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MarketFreshnessStatus {
    Unknown,
    WarmingUp,
    Current,
    Stale,
    Degraded,
    Disconnected,
    ResyncRequired,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketFreshnessCurrent {
    pub evidence: MarketCurrentEvidence,
    pub provider: Provider,
    pub scope: MarketObservationScope,
    pub data_kind: String,
    pub last_event_time: Option<UnixNanos>,
    pub last_received_time: Option<UnixNanos>,
    pub age_nanos: u64,
    pub event_sequence: Sequence,
    pub status: MarketFreshnessStatus,
}

impl MarketIndexedView {
    pub fn quote(&self, key: &MarketViewKey) -> ContractResult<Option<MarketQuoteCurrent>> {
        ensure_kind(key, MarketViewKind::Quote)?;
        let encoded = market_indexed_key(key)?;
        self.reader
            .with_value_snapshot(MARKET_QUOTES_DATABASE, &encoded, |metadata, bytes| {
                ensure_ready(&metadata)?;
                let Some(bytes) = bytes else {
                    return Ok(None);
                };
                ensure_identifier_readable(bytes, "MQC3 MarketQuoteCurrent")?;
                if !fb::market_quote_current_buffer_has_identifier(bytes) {
                    return Err(ContractError::Invalid(
                        "expected MQC3 MarketQuoteCurrent".into(),
                    ));
                }
                let root = fb::root_as_market_quote_current(bytes)
                    .map_err(|error| ContractError::Invalid(error.to_string()))?;
                validate_current_identity(root.identity(), key)?;
                Ok(Some(quote_current(
                    &metadata,
                    root.identity(),
                    root.value(),
                )?))
            })
            .map_err(|error| ContractError::Transport(error.to_string()))?
    }

    pub fn bar(&self, key: &MarketViewKey) -> ContractResult<Option<MarketBarCurrent>> {
        ensure_kind(key, MarketViewKind::Bar)?;
        let encoded = market_indexed_key(key)?;
        self.reader
            .with_value_snapshot(MARKET_BARS_DATABASE, &encoded, |metadata, bytes| {
                ensure_ready(&metadata)?;
                let Some(bytes) = bytes else {
                    return Ok(None);
                };
                ensure_identifier_readable(bytes, "MBC3 MarketBarCurrent")?;
                if !fb::market_bar_current_buffer_has_identifier(bytes) {
                    return Err(ContractError::Invalid(
                        "expected MBC3 MarketBarCurrent".into(),
                    ));
                }
                let root = fb::root_as_market_bar_current(bytes)
                    .map_err(|error| ContractError::Invalid(error.to_string()))?;
                validate_current_identity(root.identity(), key)?;
                Ok(Some(bar_current(&metadata, root.identity(), root.value())?))
            })
            .map_err(|error| ContractError::Transport(error.to_string()))?
    }

    pub fn greeks(&self, key: &MarketViewKey) -> ContractResult<Option<MarketGreeksCurrent>> {
        ensure_kind(key, MarketViewKind::Greeks)?;
        let encoded = market_indexed_key(key)?;
        self.reader
            .with_value_snapshot(MARKET_GREEKS_DATABASE, &encoded, |metadata, bytes| {
                ensure_ready(&metadata)?;
                let Some(bytes) = bytes else {
                    return Ok(None);
                };
                ensure_identifier_readable(bytes, "MGC3 MarketGreeksCurrent")?;
                if !fb::market_greeks_current_buffer_has_identifier(bytes) {
                    return Err(ContractError::Invalid(
                        "expected MGC3 MarketGreeksCurrent".into(),
                    ));
                }
                let root = fb::root_as_market_greeks_current(bytes)
                    .map_err(|error| ContractError::Invalid(error.to_string()))?;
                validate_current_identity(root.identity(), key)?;
                Ok(Some(greeks_current(
                    &metadata,
                    root.identity(),
                    root.value(),
                )?))
            })
            .map_err(|error| ContractError::Transport(error.to_string()))?
    }

    pub fn rate(&self, key: &MarketViewKey) -> ContractResult<Option<MarketRateCurrent>> {
        self.read_current(
            key,
            MarketViewKind::Rate,
            MARKET_RATES_DATABASE,
            |m, i, b| {
                if !fb::market_rate_current_buffer_has_identifier(b) {
                    return Err(ContractError::Invalid(
                        "expected MRC3 MarketRateCurrent".into(),
                    ));
                }
                let r = fb::root_as_market_rate_current(b).map_err(invalid)?;
                validate_current_identity(r.identity(), key)?;
                rate_current(m, i, r.value())
            },
        )
    }
    pub fn ticker_24h(
        &self,
        key: &MarketViewKey,
    ) -> ContractResult<Option<MarketTicker24hCurrent>> {
        self.read_current(
            key,
            MarketViewKind::Ticker24h,
            MARKET_TICKERS_DATABASE,
            |m, i, b| {
                if !fb::market_ticker_24h_current_buffer_has_identifier(b) {
                    return Err(ContractError::Invalid(
                        "expected MTC3 MarketTicker24hCurrent".into(),
                    ));
                }
                let r = fb::root_as_market_ticker_24h_current(b).map_err(invalid)?;
                validate_current_identity(r.identity(), key)?;
                ticker_current(m, i, r.value())
            },
        )
    }
    pub fn mark_price(
        &self,
        key: &MarketViewKey,
    ) -> ContractResult<Option<MarketMarkPriceCurrent>> {
        self.read_current(
            key,
            MarketViewKind::MarkPrice,
            MARKET_MARK_PRICES_DATABASE,
            |m, i, b| {
                if !fb::market_mark_price_current_buffer_has_identifier(b) {
                    return Err(ContractError::Invalid(
                        "expected MMP3 MarketMarkPriceCurrent".into(),
                    ));
                }
                let r = fb::root_as_market_mark_price_current(b).map_err(invalid)?;
                validate_current_identity(r.identity(), key)?;
                mark_current(m, i, r.value())
            },
        )
    }
    pub fn funding_rate(
        &self,
        key: &MarketViewKey,
    ) -> ContractResult<Option<MarketFundingRateCurrent>> {
        self.read_current(
            key,
            MarketViewKind::FundingRate,
            MARKET_FUNDING_RATES_DATABASE,
            |m, i, b| {
                if !fb::market_funding_rate_current_buffer_has_identifier(b) {
                    return Err(ContractError::Invalid(
                        "expected MFR3 MarketFundingRateCurrent".into(),
                    ));
                }
                let r = fb::root_as_market_funding_rate_current(b).map_err(invalid)?;
                validate_current_identity(r.identity(), key)?;
                funding_current(m, i, r.value())
            },
        )
    }
    pub fn open_interest(
        &self,
        key: &MarketViewKey,
    ) -> ContractResult<Option<MarketOpenInterestCurrent>> {
        self.read_current(
            key,
            MarketViewKind::OpenInterest,
            MARKET_OPEN_INTEREST_DATABASE,
            |m, i, b| {
                if !fb::market_open_interest_current_buffer_has_identifier(b) {
                    return Err(ContractError::Invalid(
                        "expected MOI3 MarketOpenInterestCurrent".into(),
                    ));
                }
                let r = fb::root_as_market_open_interest_current(b).map_err(invalid)?;
                validate_current_identity(r.identity(), key)?;
                open_interest_current(m, i, r.value())
            },
        )
    }
    pub fn index_price(
        &self,
        key: &MarketViewKey,
    ) -> ContractResult<Option<MarketIndexPriceCurrent>> {
        self.read_current(
            key,
            MarketViewKind::IndexPrice,
            MARKET_INDEX_PRICES_DATABASE,
            |m, i, b| {
                if !fb::market_index_price_current_buffer_has_identifier(b) {
                    return Err(ContractError::Invalid(
                        "expected MIP3 MarketIndexPriceCurrent".into(),
                    ));
                }
                let r = fb::root_as_market_index_price_current(b).map_err(invalid)?;
                validate_current_identity(r.identity(), key)?;
                index_current(m, i, r.value())
            },
        )
    }
    pub fn order_book(
        &self,
        key: &MarketViewKey,
    ) -> ContractResult<Option<MarketOrderBookCurrent>> {
        self.read_current(
            key,
            MarketViewKind::OrderBook,
            MARKET_ORDER_BOOKS_DATABASE,
            |m, i, b| {
                if !fb::market_order_book_current_buffer_has_identifier(b) {
                    return Err(ContractError::Invalid(
                        "expected MOB3 MarketOrderBookCurrent".into(),
                    ));
                }
                let r = fb::root_as_market_order_book_current(b).map_err(invalid)?;
                validate_current_identity(r.identity(), key)?;
                order_book_current(m, i, r.value())
            },
        )
    }
    pub fn freshness(&self, key: &MarketViewKey) -> ContractResult<Option<MarketFreshnessCurrent>> {
        self.read_current(
            key,
            MarketViewKind::Freshness,
            MARKET_FRESHNESS_DATABASE,
            |m, i, b| {
                if !fb::market_freshness_current_buffer_has_identifier(b) {
                    return Err(ContractError::Invalid(
                        "expected MFS3 MarketFreshnessCurrent".into(),
                    ));
                }
                let r = fb::root_as_market_freshness_current(b).map_err(invalid)?;
                validate_current_identity(r.identity(), key)?;
                freshness_current(m, i, r.value())
            },
        )
    }

    fn read_current<T>(
        &self,
        key: &MarketViewKey,
        kind: MarketViewKind,
        database: &str,
        project: impl FnOnce(
            &MetadataSnapshot,
            fb::MarketCurrentIdentity<'_>,
            &[u8],
        ) -> ContractResult<T>,
    ) -> ContractResult<Option<T>> {
        ensure_kind(key, kind)?;
        let encoded = market_indexed_key(key)?;
        self.reader
            .with_value_snapshot(database, &encoded, |metadata, bytes| {
                ensure_ready(&metadata)?;
                let Some(bytes) = bytes else { return Ok(None) };
                ensure_identifier_readable(bytes, "Market current view")?;
                let identity = current_identity(kind, bytes)?;
                project(&metadata, identity, bytes).map(Some)
            })
            .map_err(|e| ContractError::Transport(e.to_string()))?
    }
}

fn ensure_identifier_readable(bytes: &[u8], expected: &str) -> ContractResult<()> {
    if kairos_protocol::flatbuffer::identifier_is_readable(bytes) {
        Ok(())
    } else {
        Err(ContractError::Invalid(format!(
            "truncated {expected} FlatBuffers value"
        )))
    }
}

fn invalid(e: flatbuffers::InvalidFlatbuffer) -> ContractError {
    ContractError::Invalid(e.to_string())
}
fn current_identity(
    kind: MarketViewKind,
    b: &[u8],
) -> ContractResult<fb::MarketCurrentIdentity<'_>> {
    Ok(match kind {
        MarketViewKind::Rate => fb::root_as_market_rate_current(b)
            .map_err(invalid)?
            .identity(),
        MarketViewKind::Ticker24h => fb::root_as_market_ticker_24h_current(b)
            .map_err(invalid)?
            .identity(),
        MarketViewKind::MarkPrice => fb::root_as_market_mark_price_current(b)
            .map_err(invalid)?
            .identity(),
        MarketViewKind::FundingRate => fb::root_as_market_funding_rate_current(b)
            .map_err(invalid)?
            .identity(),
        MarketViewKind::OpenInterest => fb::root_as_market_open_interest_current(b)
            .map_err(invalid)?
            .identity(),
        MarketViewKind::IndexPrice => fb::root_as_market_index_price_current(b)
            .map_err(invalid)?
            .identity(),
        MarketViewKind::OrderBook => fb::root_as_market_order_book_current(b)
            .map_err(invalid)?
            .identity(),
        MarketViewKind::Freshness => fb::root_as_market_freshness_current(b)
            .map_err(invalid)?
            .identity(),
        _ => {
            return Err(ContractError::Invalid(
                "unsupported Market field-read kind".into(),
            ));
        },
    })
}

fn ensure_kind(key: &MarketViewKey, expected: MarketViewKind) -> ContractResult<()> {
    if key.kind != expected {
        return Err(ContractError::Invalid(format!(
            "Market current-view key kind `{}` does not match requested `{}`",
            key.kind.as_str(),
            expected.as_str()
        )));
    }
    Ok(())
}

fn ensure_ready(metadata: &MetadataSnapshot) -> ContractResult<()> {
    if metadata.rebuild_state != RebuildState::Ready {
        return Err(ContractError::Transport(
            "Market indexed current view is not ready".into(),
        ));
    }
    Ok(())
}

fn evidence(
    metadata: &MetadataSnapshot,
    identity: fb::MarketCurrentIdentity<'_>,
) -> MarketCurrentEvidence {
    MarketCurrentEvidence {
        resource_epoch: metadata.resource_epoch,
        producer_incarnation: metadata.producer_incarnation,
        applied_event_sequence: Sequence::new(metadata.applied_event_sequence),
        committed_at: UnixNanos::new(metadata.committed_at_unix_nanos),
        source_event_id: identity.source_event_id().map(ToOwned::to_owned),
        synchronized: identity.synchronized(),
    }
}

fn observation_scope(scope: fb::ObservationScope<'_>) -> ContractResult<MarketObservationScope> {
    match scope.kind() {
        fb::ObservationScopeKind::MARKET => Ok(MarketObservationScope::Market(
            MarketId::new(required(scope.market_id(), "market_id")?)
                .map_err(|error| ContractError::Invalid(error.to_string()))?,
        )),
        fb::ObservationScopeKind::CONSOLIDATED => Ok(MarketObservationScope::Consolidated {
            instrument_id: InstrumentId::new(required(scope.instrument_id(), "instrument_id")?)
                .map_err(|error| ContractError::Invalid(error.to_string()))?,
            network_id: scope.network_id().map(ToOwned::to_owned),
        }),
        _ => Err(ContractError::Invalid(
            "Market observation scope kind is unspecified or unknown".into(),
        )),
    }
}

fn quote_current(
    metadata: &MetadataSnapshot,
    identity: fb::MarketCurrentIdentity<'_>,
    value: fb::Quote<'_>,
) -> ContractResult<MarketQuoteCurrent> {
    let provider = provider(value.provider())?;
    if provider.as_str() != identity.provider() {
        return Err(ContractError::Invalid(
            "Market quote provider does not match current identity".into(),
        ));
    }
    Ok(MarketQuoteCurrent {
        evidence: evidence(metadata, identity),
        quote_id: value.quote_id().map(ToOwned::to_owned),
        scope: observation_scope(value.scope())?,
        instrument_id: instrument(value.instrument_id())?,
        provider,
        bid_price: value.bid_price().map(price).transpose()?,
        bid_quantity: value.bid_quantity().map(quantity).transpose()?,
        ask_price: value.ask_price().map(price).transpose()?,
        ask_quantity: value.ask_quantity().map(quantity).transpose()?,
        bid_venue_code: value.bid_venue_code().map(ToOwned::to_owned),
        bid_venue_id: value
            .bid_venue_id()
            .map(VenueId::new)
            .transpose()
            .map_err(|error| ContractError::Invalid(error.to_string()))?,
        ask_venue_id: value
            .ask_venue_id()
            .map(VenueId::new)
            .transpose()
            .map_err(|error| ContractError::Invalid(error.to_string()))?,
        ask_venue_code: value.ask_venue_code().map(ToOwned::to_owned),
        tape: (value.tape() != 0).then_some(value.tape()),
        source_observed_at: UnixNanos::new(value.source_observed_at_unix_nanos()),
        received_at: UnixNanos::new(value.received_at_unix_nanos()),
    })
}

fn bar_current(
    metadata: &MetadataSnapshot,
    identity: fb::MarketCurrentIdentity<'_>,
    value: fb::Bar<'_>,
) -> ContractResult<MarketBarCurrent> {
    let provider = provider(value.provider())?;
    if provider.as_str() != identity.provider() {
        return Err(ContractError::Invalid(
            "Market bar provider does not match current identity".into(),
        ));
    }
    let kind = match value.kind() {
        fb::BarKind::TRADES => MarketBarKind::Trades,
        fb::BarKind::QUOTES => MarketBarKind::Quotes,
        fb::BarKind::MIDPOINT => MarketBarKind::Midpoint,
        _ => {
            return Err(ContractError::Invalid(
                "Market bar kind is unspecified or unknown".into(),
            ));
        },
    };
    Ok(MarketBarCurrent {
        evidence: evidence(metadata, identity),
        scope: observation_scope(value.scope())?,
        instrument_id: instrument(value.instrument_id())?,
        provider,
        bar_spec_id: required(Some(value.bar_spec_id()), "bar_spec_id")?.to_owned(),
        kind,
        window_start: UnixNanos::new(value.window_start_unix_nanos()),
        window_end: UnixNanos::new(value.window_end_unix_nanos()),
        open: price(value.open())?,
        high: price(value.high())?,
        low: price(value.low())?,
        close: price(value.close())?,
        volume: value.volume().map(quantity).transpose()?,
        source_observed_at: UnixNanos::new(value.source_observed_at_unix_nanos()),
        received_at: UnixNanos::new(value.received_at_unix_nanos()),
    })
}

fn greeks_current(
    metadata: &MetadataSnapshot,
    identity: fb::MarketCurrentIdentity<'_>,
    value: fb::Greeks<'_>,
) -> ContractResult<MarketGreeksCurrent> {
    let provider = provider(value.provider())?;
    if provider.as_str() != identity.provider() {
        return Err(ContractError::Invalid(
            "Market Greeks provider does not match current identity".into(),
        ));
    }
    Ok(MarketGreeksCurrent {
        evidence: evidence(metadata, identity),
        scope: observation_scope(value.scope())?,
        instrument_id: instrument(value.instrument_id())?,
        provider,
        expiry: value.expiry_unix_nanos().map(UnixNanos::new),
        strike: value.strike().map(price).transpose()?,
        delta: value.delta().map(decimal).transpose()?,
        gamma: value.gamma().map(decimal).transpose()?,
        vega: value.vega().map(decimal).transpose()?,
        theta: value.theta().map(decimal).transpose()?,
        implied_volatility: value.implied_volatility().map(decimal).transpose()?,
        source_observed_at: UnixNanos::new(value.source_observed_at_unix_nanos()),
        received_at: UnixNanos::new(value.received_at_unix_nanos()),
        derivation_id: value.derivation_id().map(ToOwned::to_owned),
    })
}

fn common(
    metadata: &MetadataSnapshot,
    identity: fb::MarketCurrentIdentity<'_>,
    scope: fb::ObservationScope<'_>,
    instrument_id: &str,
    provider_value: &str,
) -> ContractResult<(
    MarketCurrentEvidence,
    MarketObservationScope,
    InstrumentId,
    Provider,
)> {
    let p = provider(provider_value)?;
    if p.as_str() != identity.provider() {
        return Err(ContractError::Invalid(
            "Market value provider does not match current identity".into(),
        ));
    }
    Ok((
        evidence(metadata, identity),
        observation_scope(scope)?,
        instrument(instrument_id)?,
        p,
    ))
}
fn parts(v: &Decimal64) -> ContractResult<DecimalParts> {
    DecimalParts::new(v.mantissa(), v.scale()).map_err(|e| ContractError::Invalid(e.to_string()))
}
fn optional_time(v: u64) -> Option<UnixNanos> {
    (v != 0).then(|| UnixNanos::new(v))
}
fn rate_current(
    m: &MetadataSnapshot,
    i: fb::MarketCurrentIdentity<'_>,
    v: fb::Rate<'_>,
) -> ContractResult<MarketRateCurrent> {
    let (e, s, x, p) = common(m, i, v.scope(), v.instrument_id(), v.provider())?;
    Ok(MarketRateCurrent {
        evidence: e,
        rate_id: v.rate_id().to_owned(),
        scope: s,
        instrument_id: x,
        provider: p,
        basis: v.basis().to_owned(),
        value: parts(v.value())?,
        mark_price: v.mark_price().map(price).transpose()?,
        source_observed_at: UnixNanos::new(v.source_observed_at_unix_nanos()),
        received_at: UnixNanos::new(v.received_at_unix_nanos()),
    })
}
fn ticker_current(
    m: &MetadataSnapshot,
    i: fb::MarketCurrentIdentity<'_>,
    v: fb::Ticker24h<'_>,
) -> ContractResult<MarketTicker24hCurrent> {
    let (e, s, x, p) = common(m, i, v.scope(), v.instrument_id(), v.provider())?;
    Ok(MarketTicker24hCurrent {
        evidence: e,
        scope: s,
        instrument_id: x,
        provider: p,
        last_price: v.last_price().map(price).transpose()?,
        bid_price: v.bid_price().map(price).transpose()?,
        bid_quantity: v.bid_quantity().map(quantity).transpose()?,
        ask_price: v.ask_price().map(price).transpose()?,
        ask_quantity: v.ask_quantity().map(quantity).transpose()?,
        open_price: v.open_price().map(price).transpose()?,
        high_price: v.high_price().map(price).transpose()?,
        low_price: v.low_price().map(price).transpose()?,
        volume_base: v.volume_base().map(quantity).transpose()?,
        volume_quote: v.volume_quote().map(parts).transpose()?,
        price_change_abs: v.price_change_abs().map(price_delta).transpose()?,
        price_change_pct: v.price_change_pct().map(parts).transpose()?,
        vwap: v.vwap().map(price).transpose()?,
        mark_price: v.mark_price().map(price).transpose()?,
        source_observed_at: UnixNanos::new(v.source_observed_at_unix_nanos()),
        received_at: UnixNanos::new(v.received_at_unix_nanos()),
    })
}
fn mark_current(
    m: &MetadataSnapshot,
    i: fb::MarketCurrentIdentity<'_>,
    v: fb::MarkPrice<'_>,
) -> ContractResult<MarketMarkPriceCurrent> {
    let (e, s, x, p) = common(m, i, v.scope(), v.instrument_id(), v.provider())?;
    Ok(MarketMarkPriceCurrent {
        evidence: e,
        scope: s,
        instrument_id: x,
        provider: p,
        mark_price: price(v.mark_price())?,
        index_price: v.index_price().map(price).transpose()?,
        estimated_settlement_price: v.estimated_settlement_price().map(price).transpose()?,
        funding_rate: v.funding_rate().map(parts).transpose()?,
        next_funding_time: optional_time(v.next_funding_time_unix_nanos()),
        source_observed_at: UnixNanos::new(v.source_observed_at_unix_nanos()),
        received_at: UnixNanos::new(v.received_at_unix_nanos()),
    })
}
fn funding_current(
    m: &MetadataSnapshot,
    i: fb::MarketCurrentIdentity<'_>,
    v: fb::FundingRate<'_>,
) -> ContractResult<MarketFundingRateCurrent> {
    let (e, s, x, p) = common(m, i, v.scope(), v.instrument_id(), v.provider())?;
    Ok(MarketFundingRateCurrent {
        evidence: e,
        scope: s,
        instrument_id: x,
        provider: p,
        funding_rate: parts(v.funding_rate())?,
        funding_period_seconds: v.funding_period_seconds(),
        next_funding_time: optional_time(v.next_funding_time_unix_nanos()),
        source_observed_at: UnixNanos::new(v.source_observed_at_unix_nanos()),
        received_at: UnixNanos::new(v.received_at_unix_nanos()),
    })
}
fn open_interest_current(
    m: &MetadataSnapshot,
    i: fb::MarketCurrentIdentity<'_>,
    v: fb::OpenInterest<'_>,
) -> ContractResult<MarketOpenInterestCurrent> {
    let (e, s, x, p) = common(m, i, v.scope(), v.instrument_id(), v.provider())?;
    Ok(MarketOpenInterestCurrent {
        evidence: e,
        scope: s,
        instrument_id: x,
        provider: p,
        contracts: quantity(v.contracts())?,
        quote_value: v.quote_value().map(parts).transpose()?,
        change_24h: v.change_24h().map(parts).transpose()?,
        change_pct_24h: v.change_pct_24h().map(parts).transpose()?,
        source_observed_at: UnixNanos::new(v.source_observed_at_unix_nanos()),
        received_at: UnixNanos::new(v.received_at_unix_nanos()),
    })
}
fn index_current(
    m: &MetadataSnapshot,
    i: fb::MarketCurrentIdentity<'_>,
    v: fb::IndexPrice<'_>,
) -> ContractResult<MarketIndexPriceCurrent> {
    let (e, s, x, p) = common(m, i, v.scope(), v.instrument_id(), v.provider())?;
    Ok(MarketIndexPriceCurrent {
        evidence: e,
        scope: s,
        instrument_id: x,
        provider: p,
        spot_index_price: v.spot_index_price().map(price).transpose()?,
        contract_index_price: v.contract_index_price().map(price).transpose()?,
        index_price: v.index_price().map(price).transpose()?,
        funding_rate: v.funding_rate().map(parts).transpose()?,
        source_observed_at: UnixNanos::new(v.source_observed_at_unix_nanos()),
        received_at: UnixNanos::new(v.received_at_unix_nanos()),
    })
}
fn level(v: fb::OrderBookLevel<'_>) -> ContractResult<MarketOrderBookLevel> {
    Ok(MarketOrderBookLevel {
        price: price(v.price())?,
        quantity: quantity(v.quantity())?,
        order_count: v.order_count(),
    })
}
fn order_book_current(
    m: &MetadataSnapshot,
    i: fb::MarketCurrentIdentity<'_>,
    v: fb::OrderBookSnapshotValue<'_>,
) -> ContractResult<MarketOrderBookCurrent> {
    let x = v.identity();
    let p = provider(x.provider())?;
    if p.as_str() != i.provider() {
        return Err(ContractError::Invalid(
            "Market order book provider does not match current identity".into(),
        ));
    }
    Ok(MarketOrderBookCurrent {
        evidence: evidence(m, i),
        provider: p,
        market_id: MarketId::new(x.market_id())
            .map_err(|e| ContractError::Invalid(e.to_string()))?,
        instrument_id: instrument(x.instrument_id())?,
        sequence: Sequence::new(v.sequence()),
        source_observed_at: UnixNanos::new(v.source_observed_at_unix_nanos()),
        received_at: UnixNanos::new(v.received_at_unix_nanos()),
        checksum: v.checksum().map(ToOwned::to_owned),
        depth_policy: v.depth_policy().to_owned(),
        bids: v.bids().iter().map(level).collect::<Result<_, _>>()?,
        asks: v.asks().iter().map(level).collect::<Result<_, _>>()?,
    })
}
fn freshness_current(
    m: &MetadataSnapshot,
    i: fb::MarketCurrentIdentity<'_>,
    v: fb::FreshnessEntry<'_>,
) -> ContractResult<MarketFreshnessCurrent> {
    let p = provider(v.provider())?;
    if p.as_str() != i.provider() {
        return Err(ContractError::Invalid(
            "Market freshness provider does not match current identity".into(),
        ));
    }
    let status = match v.status() {
        fb::FreshnessStatus::UNKNOWN => MarketFreshnessStatus::Unknown,
        fb::FreshnessStatus::WARMING_UP => MarketFreshnessStatus::WarmingUp,
        fb::FreshnessStatus::CURRENT => MarketFreshnessStatus::Current,
        fb::FreshnessStatus::STALE => MarketFreshnessStatus::Stale,
        fb::FreshnessStatus::DEGRADED => MarketFreshnessStatus::Degraded,
        fb::FreshnessStatus::DISCONNECTED => MarketFreshnessStatus::Disconnected,
        fb::FreshnessStatus::RESYNC_REQUIRED => MarketFreshnessStatus::ResyncRequired,
        _ => {
            return Err(ContractError::Invalid(
                "unknown Market freshness status".into(),
            ));
        },
    };
    Ok(MarketFreshnessCurrent {
        evidence: evidence(m, i),
        provider: p,
        scope: observation_scope(v.scope())?,
        data_kind: v.data_kind().to_owned(),
        last_event_time: optional_time(v.last_event_time_unix_nanos()),
        last_received_time: optional_time(v.last_received_time_unix_nanos()),
        age_nanos: v.age_nanos(),
        event_sequence: Sequence::new(v.event_sequence()),
        status,
    })
}

fn provider(value: &str) -> ContractResult<Provider> {
    Provider::new(value).map_err(|error| ContractError::Invalid(error.to_string()))
}

fn instrument(value: &str) -> ContractResult<InstrumentId> {
    InstrumentId::new(value).map_err(|error| ContractError::Invalid(error.to_string()))
}

fn price(value: &Decimal64) -> ContractResult<Price> {
    Price::new(value.mantissa(), value.scale())
        .map_err(|error| ContractError::Invalid(error.to_string()))
}

fn price_delta(value: &Decimal64) -> ContractResult<PriceDelta> {
    PriceDelta::new(value.mantissa(), value.scale())
        .map_err(|error| ContractError::Invalid(error.to_string()))
}

fn quantity(value: &Decimal64) -> ContractResult<Quantity> {
    Quantity::new(value.mantissa(), value.scale())
        .map_err(|error| ContractError::Invalid(error.to_string()))
}

fn decimal(value: &Decimal64) -> ContractResult<DecimalParts> {
    DecimalParts::new(value.mantissa(), value.scale())
        .map_err(|error| ContractError::Invalid(error.to_string()))
}

fn required<'a>(value: Option<&'a str>, name: &str) -> ContractResult<&'a str> {
    value
        .filter(|value| !value.is_empty())
        .ok_or_else(|| ContractError::Invalid(format!("Market current value requires `{name}`")))
}
