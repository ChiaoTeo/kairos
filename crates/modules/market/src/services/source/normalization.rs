//! Provider event normalization into Market-owned facts.

use kairos_conflux::{MarketEvent, MarketEventKind};
use kairos_primitives::decimal::Money;

use super::messages::{SourceInput, SourceOrderBookUpdate};
use crate::domain::market::ResolvedMarket;
use crate::domain::observation::order_book::PriceLevel;
use crate::domain::observation::{
    Bar, FundingRate, IndexPrice, MarkPrice, MarketObservation, OpenInterest, OptionGreeks, Quote,
    QuoteBar, Rate, Ticker24h, Trade, TradeBar,
};
use crate::domain::source::{SourceEpoch, SourceId};

pub(crate) enum Normalized {
    Observation(MarketObservation),
    OrderBook(SourceOrderBookUpdate),
}

pub(crate) fn with_epoch(
    value: Normalized,
    source_id: SourceId,
    epoch: SourceEpoch,
) -> SourceInput {
    match value {
        Normalized::Observation(observation) => SourceInput::Observation {
            source_id,
            epoch,
            observation,
        },
        Normalized::OrderBook(update) => SourceInput::OrderBook {
            source_id,
            epoch,
            update,
        },
    }
}

pub(crate) fn normalize(
    source_id: &SourceId,
    market: &ResolvedMarket,
    event: MarketEvent,
) -> Result<Option<Normalized>, String> {
    let source_id = source_id.clone();
    let venue = event.venue.clone();
    let aggregate_scope = || {
        if market.route.provider_id.eq_ignore_ascii_case("massive") {
            match &market.scope {
                crate::ObservationScope::Consolidated { .. } => market.scope.clone(),
                crate::ObservationScope::Market { .. } => {
                    crate::ObservationScope::consolidated(market.instrument_id.to_string(), None)
                        .expect("resolved market has a valid instrument identity")
                },
            }
        } else {
            market.scope.clone()
        }
    };
    let observation = match event.kind {
        MarketEventKind::Quote | MarketEventKind::Snapshot => MarketObservation::Quote(Quote {
            scope: aggregate_scope(),
            instrument_id: market.instrument_id.clone(),
            bid_price: event.price,
            bid_quantity: event.quantity,
            ask_price: event.ask_price,
            ask_quantity: event.ask_quantity,
            bid_venue_code: venue.bid_exchange.clone(),
            ask_venue_code: venue.ask_exchange.clone(),
            tape: venue.tape,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::Trade => MarketObservation::Trade(Trade {
            scope: trade_scope(market, &venue)?,
            instrument_id: market.instrument_id.clone(),
            trade_id: None,
            price: event.price.ok_or("trade event has no price")?,
            quantity: event.quantity.ok_or("trade event has no quantity")?,
            cost: None,
            aggressor_side: None,
            venue_code: venue.trade_exchange.clone(),
            tape: venue.tape,
            trf_id: venue.trf_id,
            participant_timestamp_unix_nanos: venue.participant_timestamp_unix_nanos,
            trf_timestamp_unix_nanos: venue.trf_timestamp_unix_nanos,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::Bar | MarketEventKind::TradeBar | MarketEventKind::QuoteBar => {
            let value = event.bar.ok_or("bar event has no bar payload")?;
            let bar = Bar {
                scope: aggregate_scope(),
                instrument_id: market.instrument_id.clone(),
                timeframe: value.timeframe,
                open: value.open,
                high: value.high,
                low: value.low,
                close: value.close,
                volume: value.volume,
                observed_at_unix_nanos: event.observed_at_unix_nanos,
                source_id,
                derivation: value.derivation,
            };
            match event.kind {
                MarketEventKind::TradeBar => MarketObservation::TradeBar(TradeBar { bar }),
                MarketEventKind::QuoteBar => MarketObservation::QuoteBar(QuoteBar { bar }),
                _ => MarketObservation::Bar(bar),
            }
        },
        MarketEventKind::Greeks => {
            let value = event.greeks.ok_or("greeks event has no greeks payload")?;
            MarketObservation::OptionGreeks(OptionGreeks {
                scope: aggregate_scope(),
                instrument_id: market.instrument_id.clone(),
                expiry_unix_nanos: value.expiry_unix_nanos,
                strike: value.strike,
                delta: value.delta,
                gamma: value.gamma,
                vega: value.vega,
                theta: value.theta,
                implied_volatility: value.implied_volatility,
                observed_at_unix_nanos: event.observed_at_unix_nanos,
                source_id,
                derivation: value.derivation,
            })
        },
        MarketEventKind::Rate => MarketObservation::Rate(Rate {
            rate_id: format!("funding:{}", market.scope.key()),
            scope: aggregate_scope(),
            instrument_id: market.instrument_id.clone(),
            basis: "funding".into(),
            value: event.rate.ok_or("rate event has no value")?,
            mark_price: event.ask_price,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::Ticker24h => MarketObservation::Ticker24h(Ticker24h {
            scope: aggregate_scope(),
            instrument_id: market.instrument_id.clone(),
            last_price: event.price,
            bid_price: None,
            bid_quantity: None,
            ask_price: event.ask_price,
            ask_quantity: event.ask_quantity,
            open_price: None,
            high_price: None,
            low_price: None,
            volume_base: event.quantity,
            volume_quote: None,
            price_change_abs: None,
            price_change_pct: None,
            vwap: None,
            mark_price: None,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::MarkPrice => MarketObservation::MarkPrice(MarkPrice {
            scope: aggregate_scope(),
            instrument_id: market.instrument_id.clone(),
            mark_price: event.price.ok_or("mark price event has no price")?,
            index_price: event.ask_price,
            estimated_settlement_price: None,
            funding_rate: None,
            next_funding_time_unix_nanos: None,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::IndexPrice => MarketObservation::IndexPrice(IndexPrice {
            scope: aggregate_scope(),
            instrument_id: market.instrument_id.clone(),
            spot_index_price: event.price,
            contract_index_price: None,
            index_price: event.price,
            funding_rate: None,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::FundingRate => MarketObservation::FundingRate(FundingRate {
            scope: aggregate_scope(),
            instrument_id: market.instrument_id.clone(),
            funding_rate: event.rate.ok_or("funding rate event has no value")?,
            funding_period_seconds: None,
            next_funding_time_unix_nanos: None,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::OpenInterest => MarketObservation::OpenInterest(OpenInterest {
            scope: aggregate_scope(),
            instrument_id: market.instrument_id.clone(),
            contracts: event
                .quantity
                .ok_or("open interest event has no quantity")?,
            quote_value: event
                .price
                .map(|value| Money::new(value.mantissa(), value.scale()))
                .transpose()
                .map_err(|error| error.to_string())?,
            change_24h: None,
            change_pct_24h: None,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::InstrumentStatus => {
            return Err("InstrumentStatus is not part of Market v2".into());
        },
        MarketEventKind::BookSnapshot | MarketEventKind::BookDelta => {
            let market_id = market
                .market_id()
                .cloned()
                .ok_or("order book observations require a canonical market scope")?;
            let update = SourceOrderBookUpdate {
                market: Box::new(market.clone()),
                source_id,
                market_id,
                instrument_id: market.instrument_id.clone(),
                first_sequence: event
                    .first_sequence
                    .ok_or("order book event has no first sequence")?,
                last_sequence: event
                    .last_sequence
                    .or(event.sequence)
                    .ok_or("order book event has no last sequence")?,
                event_time_unix_nanos: event.observed_at_unix_nanos,
                bids: event
                    .bids
                    .into_iter()
                    .map(|(price, quantity)| PriceLevel { price, quantity })
                    .collect(),
                asks: event
                    .asks
                    .into_iter()
                    .map(|(price, quantity)| PriceLevel { price, quantity })
                    .collect(),
                snapshot: event.kind == MarketEventKind::BookSnapshot,
            };
            return Ok(Some(Normalized::OrderBook(update)));
        },
        MarketEventKind::Heartbeat => return Ok(None),
    };
    Ok(Some(Normalized::Observation(observation)))
}

fn trade_scope(
    market: &ResolvedMarket,
    evidence: &kairos_conflux::MarketVenueEvidence,
) -> Result<crate::ObservationScope, String> {
    if !market.route.provider_id.eq_ignore_ascii_case("massive") {
        return Ok(market.scope.clone());
    }
    let code = evidence
        .trade_exchange
        .as_deref()
        .ok_or("Massive trade has no venue code; observation quarantined")?;
    let exchange = match code {
        "19" => "exchange:cboe-bzx",
        "4" => {
            return Err(format!(
                "Massive trade venue {code} is a reporting facility; observation quarantined"
            ));
        },
        _ => {
            return Err(format!(
                "Massive trade venue code {code} is unresolved; observation quarantined"
            ));
        },
    };
    let Some(market_id) = market.market_id() else {
        return Err(format!(
            "Massive trade venue {exchange} cannot be joined to a canonical market from a consolidated subscription; observation quarantined"
        ));
    };
    if !market
        .exchange_id
        .as_ref()
        .is_some_and(|value| value.as_str().eq_ignore_ascii_case(exchange))
    {
        return Err(format!(
            "Massive trade venue {exchange} does not match subscribed canonical market {}; observation quarantined",
            market_id
        ));
    }
    Ok(crate::ObservationScope::from(market_id.clone()))
}

#[cfg(test)]
mod tests {
    use kairos_conflux::{
        Bar as IntegrationBar, MarketEvent, MarketEventKind, MarketVenueEvidence,
    };

    use super::{Normalized, normalize};
    use crate::{MarketDataRoute, ObservationScope, ResolvedMarket, SourceId};

    fn massive_market() -> ResolvedMarket {
        ResolvedMarket::new(
            "market:cboe-bzx:equity:AAPL",
            "instrument:equity:US:AAPL:common",
            kairos_primitives::reference::InstrumentKind::Equity,
            "exchange:cboe-bzx",
            MarketDataRoute::new("route:massive:AAPL", "massive", "equity", "AAPL").unwrap(),
        )
        .unwrap()
    }

    fn massive_consolidated() -> ResolvedMarket {
        ResolvedMarket::consolidated(
            "instrument:equity:US:AAPL:common",
            Some("sip".into()),
            kairos_primitives::reference::InstrumentKind::Equity,
            MarketDataRoute::new("route:massive:AAPL", "massive", "equity", "AAPL").unwrap(),
        )
        .unwrap()
    }

    fn event(kind: MarketEventKind) -> MarketEvent {
        MarketEvent {
            kind,
            symbol: kairos_primitives::integration::ParticipantSymbol::new("AAPL").unwrap(),
            price: Some("100".parse().unwrap()),
            quantity: Some("1".parse().unwrap()),
            rate: None,
            ask_price: None,
            ask_quantity: None,
            bids: Vec::new(),
            asks: Vec::new(),
            bar: None,
            greeks: None,
            first_sequence: None,
            last_sequence: None,
            sequence: None,
            observed_at_unix_nanos: 10.into(),
            venue: MarketVenueEvidence::default(),
        }
    }

    #[test]
    fn massive_nbbo_quote_is_consolidated_and_keeps_both_venue_codes() {
        let mut event = event(MarketEventKind::Quote);
        event.venue.bid_exchange = Some("19".into());
        event.venue.ask_exchange = Some("11".into());
        event.venue.tape = Some(3);
        let Normalized::Observation(crate::MarketObservation::Quote(quote)) = normalize(
            &SourceId::new("massive-equity").unwrap(),
            &massive_market(),
            event,
        )
        .unwrap()
        .unwrap() else {
            panic!("expected quote");
        };
        assert!(matches!(quote.scope, ObservationScope::Consolidated { .. }));
        assert_eq!(quote.bid_venue_code.as_deref(), Some("19"));
        assert_eq!(quote.ask_venue_code.as_deref(), Some("11"));
        assert_eq!(quote.tape, Some(3));
    }

    #[test]
    fn massive_bar_from_instrument_route_is_consolidated_without_a_fake_market() {
        let mut event = event(MarketEventKind::Bar);
        event.bar = Some(IntegrationBar {
            timeframe: "1m".into(),
            open: "100".parse().unwrap(),
            high: "102".parse().unwrap(),
            low: "99".parse().unwrap(),
            close: "101".parse().unwrap(),
            volume: Some("10".parse().unwrap()),
            derivation: "provider".into(),
        });
        let Normalized::Observation(crate::MarketObservation::Bar(bar)) = normalize(
            &SourceId::new("massive-equity").unwrap(),
            &massive_consolidated(),
            event,
        )
        .unwrap()
        .unwrap() else {
            panic!("expected bar");
        };
        assert_eq!(
            bar.scope,
            ObservationScope::consolidated("instrument:equity:US:AAPL:common", Some("sip".into()),)
                .unwrap()
        );
        assert!(bar.scope.market_id().is_none());
    }

    #[test]
    fn massive_trade_uses_actual_venue_and_quarantines_reporting_facility() {
        let mut venue_trade = event(MarketEventKind::Trade);
        venue_trade.venue.trade_exchange = Some("19".into());
        let Normalized::Observation(crate::MarketObservation::Trade(trade)) = normalize(
            &SourceId::new("massive-equity").unwrap(),
            &massive_market(),
            venue_trade,
        )
        .unwrap()
        .unwrap() else {
            panic!("expected trade");
        };
        assert_eq!(
            trade.scope.market_id().map(|value| value.as_str()),
            Some("market:cboe-bzx:equity:AAPL")
        );

        let mut trf_trade = event(MarketEventKind::Trade);
        trf_trade.venue.trade_exchange = Some("4".into());
        trf_trade.venue.trf_id = Some(201);
        let error = normalize(
            &SourceId::new("massive-equity").unwrap(),
            &massive_market(),
            trf_trade,
        )
        .err()
        .expect("TRF trade must be quarantined");
        assert!(error.contains("reporting facility"));
    }
}
