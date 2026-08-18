use flatbuffers::FlatBufferBuilder;
use kairos_market_contract::{view_metadata, EncodeContext, MarketViewKey, MarketViewKind};
use kairos_protocol::generated::kairos::common::v_2::Decimal64;
use kairos_protocol::generated::kairos::market::v_2 as market_fb;
use kairos_protocol::InstanceIdentity;

use crate::domain::events::{MarketChange, MarketViewUpdate};
use crate::domain::freshness::MarketFreshness;

pub(crate) struct EncodedMarketView {
    pub(crate) key: MarketViewKey,
    pub(crate) bytes: Vec<u8>,
}

pub(crate) fn encode_change_view(
    actor_id: &str,
    identity: &InstanceIdentity,
    change: &MarketChange,
) -> Result<Option<EncodedMarketView>, String> {
    let sequence = change.sequence.get();
    let Some(view) = change.view.as_ref() else {
        return Ok(None);
    };
    let encoded =
        match view {
            MarketViewUpdate::Observation(crate::MarketObservation::Quote(value)) => {
                let key = MarketViewKey::new(
                    value.scope.key(),
                    value.source_id.clone(),
                    MarketViewKind::Quote,
                    None::<String>,
                )
                .map_err(|error| error.to_string())?;
                let bytes = encode_quote(actor_id, identity, sequence, &key, value)?;
                Some(EncodedMarketView { key, bytes })
            }
            MarketViewUpdate::Observation(crate::MarketObservation::Rate(value)) => {
                let key = MarketViewKey::new(
                    value.scope.key(),
                    value.source_id.clone(),
                    MarketViewKind::Rate,
                    Some(value.rate_id.clone()),
                )
                .map_err(|error| error.to_string())?;
                let bytes = encode_rate_view(actor_id, identity, sequence, &key, value)?;
                Some(EncodedMarketView { key, bytes })
            }
            MarketViewUpdate::Observation(crate::MarketObservation::Ticker24h(value)) => {
                let key = MarketViewKey::new(
                    value.scope.key(),
                    value.source_id.clone(),
                    MarketViewKind::Ticker24h,
                    None::<String>,
                )
                .map_err(|error| error.to_string())?;
                let bytes = encode_ticker_view(actor_id, identity, sequence, &key, value)?;
                Some(EncodedMarketView { key, bytes })
            }
            MarketViewUpdate::Observation(crate::MarketObservation::MarkPrice(value)) => {
                let key = MarketViewKey::new(
                    value.scope.key(),
                    value.source_id.clone(),
                    MarketViewKind::MarkPrice,
                    None::<String>,
                )
                .map_err(|error| error.to_string())?;
                let bytes = encode_mark_price_view(actor_id, identity, sequence, &key, value)?;
                Some(EncodedMarketView { key, bytes })
            }
            MarketViewUpdate::Observation(crate::MarketObservation::FundingRate(value)) => {
                let key = MarketViewKey::new(
                    value.scope.key(),
                    value.source_id.clone(),
                    MarketViewKind::FundingRate,
                    None::<String>,
                )
                .map_err(|error| error.to_string())?;
                let bytes = encode_funding_view(actor_id, identity, sequence, &key, value)?;
                Some(EncodedMarketView { key, bytes })
            }
            MarketViewUpdate::Observation(crate::MarketObservation::OpenInterest(value)) => {
                let key = MarketViewKey::new(
                    value.scope.key(),
                    value.source_id.clone(),
                    MarketViewKind::OpenInterest,
                    None::<String>,
                )
                .map_err(|error| error.to_string())?;
                let bytes = encode_open_interest_view(actor_id, identity, sequence, &key, value)?;
                Some(EncodedMarketView { key, bytes })
            }
            MarketViewUpdate::Observation(crate::MarketObservation::IndexPrice(value)) => {
                let key = MarketViewKey::new(
                    value.scope.key(),
                    value.source_id.clone(),
                    MarketViewKind::IndexPrice,
                    None::<String>,
                )
                .map_err(|error| error.to_string())?;
                let bytes = encode_index_price_view(actor_id, identity, sequence, &key, value)?;
                Some(EncodedMarketView { key, bytes })
            }
            MarketViewUpdate::Observation(crate::MarketObservation::Bar(value)) => Some(
                encode_bar(actor_id, identity, sequence, value, "unspecified")?,
            ),
            MarketViewUpdate::Observation(crate::MarketObservation::TradeBar(value)) => Some(
                encode_bar(actor_id, identity, sequence, &value.bar, "trades")?,
            ),
            MarketViewUpdate::Observation(crate::MarketObservation::QuoteBar(value)) => Some(
                encode_bar(actor_id, identity, sequence, &value.bar, "quotes")?,
            ),
            MarketViewUpdate::Observation(crate::MarketObservation::OptionGreeks(value)) => {
                let key = MarketViewKey::new(
                    value.scope.key(),
                    value.source_id.clone(),
                    MarketViewKind::Greeks,
                    None::<String>,
                )
                .map_err(|error| error.to_string())?;
                let bytes = encode_greeks_view(actor_id, identity, sequence, &key, value)?;
                Some(EncodedMarketView { key, bytes })
            }
            MarketViewUpdate::OrderBook(value) => {
                let key = MarketViewKey::new(
                    value.market_id.to_string(),
                    value.source_id.clone(),
                    MarketViewKind::OrderBook,
                    Some(value.instrument_id.to_string()),
                )
                .map_err(|error| error.to_string())?;
                let bytes = encode_orderbook_view(actor_id, identity, sequence, &key, value)?;
                Some(EncodedMarketView { key, bytes })
            }
            MarketViewUpdate::Freshness(value) => {
                let key = MarketViewKey::new(
                    value.scope.key(),
                    value.source_id.clone(),
                    MarketViewKind::Freshness,
                    Some(value.data_kind.as_str()),
                )
                .map_err(|error| error.to_string())?;
                let bytes = encode_freshness(actor_id, identity, sequence, &key, value)?;
                Some(EncodedMarketView { key, bytes })
            }
            _ => None,
        };
    Ok(encoded)
}

fn encode_bar(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    value: &crate::Bar,
    kind: &str,
) -> Result<EncodedMarketView, String> {
    let key = MarketViewKey::new(
        value.scope.key(),
        value.source_id.clone(),
        MarketViewKind::BarWindow,
        Some(value.timeframe.clone()),
    )
    .map_err(|error| error.to_string())?;
    let bytes = encode_bar_view(actor_id, identity, sequence, &key, value, kind)?;
    Ok(EncodedMarketView { key, bytes })
}

fn observation_scope<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &crate::ObservationScope,
) -> flatbuffers::WIPOffset<market_fb::ObservationScope<'a>> {
    let (kind, market_id, instrument_id, network_id) = match value {
        crate::ObservationScope::Market { market_id } => (
            market_fb::ObservationScopeKind::MARKET,
            Some(builder.create_string(market_id.as_str())),
            None,
            None,
        ),
        crate::ObservationScope::Consolidated {
            instrument_id,
            network_id,
        } => (
            market_fb::ObservationScopeKind::CONSOLIDATED,
            None,
            Some(builder.create_string(instrument_id.as_str())),
            network_id
                .as_deref()
                .map(|value| builder.create_string(value)),
        ),
    };
    market_fb::ObservationScope::create(
        builder,
        &market_fb::ObservationScopeArgs {
            kind,
            market_id,
            instrument_id,
            network_id,
        },
    )
}

fn encode_quote(
    actor_id: &str,
    identity: &InstanceIdentity,
    generation: u64,
    key: &MarketViewKey,
    value: &crate::Quote,
) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let context = EncodeContext::view(
        actor_id,
        actor_id,
        identity.clone(),
        generation,
        key.resource_id(),
    );
    let metadata = view_metadata(
        &mut builder,
        &context,
        key,
        value.observed_at_unix_nanos.get(),
    );
    let scope = observation_scope(&mut builder, &value.scope);
    let instrument_id = builder.create_string(value.instrument_id.as_str());
    let source_id = builder.create_string(&value.source_id);
    let bid_price = value
        .bid_price
        .map(|v| Decimal64::new(v.mantissa(), v.scale()));
    let bid_quantity = value
        .bid_quantity
        .map(|v| Decimal64::new(v.mantissa(), v.scale()));
    let ask_price = value
        .ask_price
        .map(|v| Decimal64::new(v.mantissa(), v.scale()));
    let ask_quantity = value
        .ask_quantity
        .map(|v| Decimal64::new(v.mantissa(), v.scale()));
    let bid_venue_code = value
        .bid_venue_code
        .as_deref()
        .map(|value| builder.create_string(value));
    let ask_venue_code = value
        .ask_venue_code
        .as_deref()
        .map(|value| builder.create_string(value));
    let quote = market_fb::Quote::create(
        &mut builder,
        &market_fb::QuoteArgs {
            quote_id: None,
            scope: Some(scope),
            instrument_id: Some(instrument_id),
            source_id: Some(source_id),
            bid_price: bid_price.as_ref(),
            bid_quantity: bid_quantity.as_ref(),
            ask_price: ask_price.as_ref(),
            ask_quantity: ask_quantity.as_ref(),
            bid_venue_code,
            ask_venue_code,
            tape: value.tape.unwrap_or_default(),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    let source_event_id = builder.create_string(&format!("market:{generation}"));
    let latest = market_fb::LatestQuote::create(
        &mut builder,
        &market_fb::LatestQuoteArgs {
            value: Some(quote),
            source_event_id: Some(source_event_id),
        },
    );
    let root = market_fb::QuoteLatestView::create(
        &mut builder,
        &market_fb::QuoteLatestViewArgs {
            metadata: Some(metadata),
            quote: Some(latest),
        },
    );
    market_fb::finish_quote_latest_view_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}

fn view_context<'a, A: flatbuffers::Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    actor_id: &str,
    identity: &InstanceIdentity,
    generation: u64,
    key: &MarketViewKey,
    as_of: u64,
) -> flatbuffers::WIPOffset<kairos_protocol::generated::kairos::common::v_2::ViewMetadata<'a>> {
    let context = EncodeContext::view(
        actor_id,
        actor_id,
        identity.clone(),
        generation,
        key.resource_id(),
    );
    view_metadata(builder, &context, key, as_of)
}

fn event_id<'a, A: flatbuffers::Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    sequence: u64,
) -> flatbuffers::WIPOffset<&'a str> {
    builder.create_string(&format!("market:{sequence}"))
}

fn encode_rate_view(
    actor_id: &str,
    identity: &InstanceIdentity,
    generation: u64,
    key: &MarketViewKey,
    value: &crate::Rate,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = view_context(
        &mut b,
        actor_id,
        identity,
        generation,
        key,
        value.observed_at_unix_nanos.get(),
    );
    let rate_id = b.create_string(&value.rate_id);
    let scope = observation_scope(&mut b, &value.scope);
    let instrument_id = b.create_string(value.instrument_id.as_str());
    let source_id = b.create_string(&value.source_id);
    let basis = b.create_string(&value.basis);
    let v = Decimal64::new(value.value.mantissa(), value.value.scale());
    let mark = value
        .mark_price
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let value_offset = market_fb::Rate::create(
        &mut b,
        &market_fb::RateArgs {
            rate_id: Some(rate_id),
            scope: Some(scope),
            instrument_id: Some(instrument_id),
            source_id: Some(source_id),
            basis: Some(basis),
            value: Some(&v),
            mark_price: mark.as_ref(),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    let source_event_id = event_id(&mut b, generation);
    let latest = market_fb::LatestRate::create(
        &mut b,
        &market_fb::LatestRateArgs {
            value: Some(value_offset),
            source_event_id: Some(source_event_id),
        },
    );
    let root = market_fb::RateLatestView::create(
        &mut b,
        &market_fb::RateLatestViewArgs {
            metadata: Some(metadata),
            rate: Some(latest),
        },
    );
    market_fb::finish_rate_latest_view_buffer(&mut b, root);
    Ok(b.finished_data().to_vec())
}

fn encode_ticker_view(
    actor_id: &str,
    identity: &InstanceIdentity,
    generation: u64,
    key: &MarketViewKey,
    value: &crate::Ticker24h,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = view_context(
        &mut b,
        actor_id,
        identity,
        generation,
        key,
        value.observed_at_unix_nanos.get(),
    );
    let scope = observation_scope(&mut b, &value.scope);
    let i = b.create_string(value.instrument_id.as_str());
    let s = b.create_string(&value.source_id);
    let lp = value
        .last_price
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let bp = value
        .bid_price
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let bq = value
        .bid_quantity
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let ap = value
        .ask_price
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let aq = value
        .ask_quantity
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let op = value
        .open_price
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let hi = value
        .high_price
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let lo = value
        .low_price
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let vb = value
        .volume_base
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let vq = value
        .volume_quote
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let ca = value
        .price_change_abs
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let cp = value
        .price_change_pct
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let vw = value.vwap.map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let mp = value
        .mark_price
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let v = market_fb::Ticker24h::create(
        &mut b,
        &market_fb::Ticker24hArgs {
            scope: Some(scope),
            instrument_id: Some(i),
            source_id: Some(s),
            last_price: lp.as_ref(),
            bid_price: bp.as_ref(),
            bid_quantity: bq.as_ref(),
            ask_price: ap.as_ref(),
            ask_quantity: aq.as_ref(),
            open_price: op.as_ref(),
            high_price: hi.as_ref(),
            low_price: lo.as_ref(),
            volume_base: vb.as_ref(),
            volume_quote: vq.as_ref(),
            price_change_abs: ca.as_ref(),
            price_change_pct: cp.as_ref(),
            vwap: vw.as_ref(),
            mark_price: mp.as_ref(),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    let source_event_id = event_id(&mut b, generation);
    let l = market_fb::LatestTicker24h::create(
        &mut b,
        &market_fb::LatestTicker24hArgs {
            value: Some(v),
            source_event_id: Some(source_event_id),
        },
    );
    let root = market_fb::Ticker24hLatestView::create(
        &mut b,
        &market_fb::Ticker24hLatestViewArgs {
            metadata: Some(metadata),
            ticker: Some(l),
        },
    );
    market_fb::finish_ticker_24h_latest_view_buffer(&mut b, root);
    Ok(b.finished_data().to_vec())
}

fn encode_mark_price_view(
    actor_id: &str,
    identity: &InstanceIdentity,
    generation: u64,
    key: &MarketViewKey,
    value: &crate::MarkPrice,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = view_context(
        &mut b,
        actor_id,
        identity,
        generation,
        key,
        value.observed_at_unix_nanos.get(),
    );
    let scope = observation_scope(&mut b, &value.scope);
    let i = b.create_string(value.instrument_id.as_str());
    let s = b.create_string(&value.source_id);
    let mp = Decimal64::new(value.mark_price.mantissa(), value.mark_price.scale());
    let ip = value
        .index_price
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let es = value
        .estimated_settlement_price
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let fr = value
        .funding_rate
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let v = market_fb::MarkPrice::create(
        &mut b,
        &market_fb::MarkPriceArgs {
            scope: Some(scope),
            instrument_id: Some(i),
            source_id: Some(s),
            mark_price: Some(&mp),
            index_price: ip.as_ref(),
            estimated_settlement_price: es.as_ref(),
            funding_rate: fr.as_ref(),
            next_funding_time_unix_nanos: value.next_funding_time_unix_nanos.map_or(0, |x| x.get()),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    let source_event_id = event_id(&mut b, generation);
    let l = market_fb::LatestMarkPrice::create(
        &mut b,
        &market_fb::LatestMarkPriceArgs {
            value: Some(v),
            source_event_id: Some(source_event_id),
        },
    );
    let root = market_fb::MarkPriceLatestView::create(
        &mut b,
        &market_fb::MarkPriceLatestViewArgs {
            metadata: Some(metadata),
            mark_price: Some(l),
        },
    );
    market_fb::finish_mark_price_latest_view_buffer(&mut b, root);
    Ok(b.finished_data().to_vec())
}

fn encode_funding_view(
    actor_id: &str,
    identity: &InstanceIdentity,
    generation: u64,
    key: &MarketViewKey,
    value: &crate::FundingRate,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = view_context(
        &mut b,
        actor_id,
        identity,
        generation,
        key,
        value.observed_at_unix_nanos.get(),
    );
    let scope = observation_scope(&mut b, &value.scope);
    let i = b.create_string(value.instrument_id.as_str());
    let s = b.create_string(&value.source_id);
    let fr = Decimal64::new(value.funding_rate.mantissa(), value.funding_rate.scale());
    let v = market_fb::FundingRate::create(
        &mut b,
        &market_fb::FundingRateArgs {
            scope: Some(scope),
            instrument_id: Some(i),
            source_id: Some(s),
            funding_rate: Some(&fr),
            funding_period_seconds: value.funding_period_seconds.unwrap_or_default(),
            next_funding_time_unix_nanos: value.next_funding_time_unix_nanos.map_or(0, |x| x.get()),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    let source_event_id = event_id(&mut b, generation);
    let l = market_fb::LatestFundingRate::create(
        &mut b,
        &market_fb::LatestFundingRateArgs {
            value: Some(v),
            source_event_id: Some(source_event_id),
        },
    );
    let root = market_fb::FundingRateLatestView::create(
        &mut b,
        &market_fb::FundingRateLatestViewArgs {
            metadata: Some(metadata),
            funding_rate: Some(l),
        },
    );
    market_fb::finish_funding_rate_latest_view_buffer(&mut b, root);
    Ok(b.finished_data().to_vec())
}

fn encode_open_interest_view(
    actor_id: &str,
    identity: &InstanceIdentity,
    generation: u64,
    key: &MarketViewKey,
    value: &crate::OpenInterest,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = view_context(
        &mut b,
        actor_id,
        identity,
        generation,
        key,
        value.observed_at_unix_nanos.get(),
    );
    let scope = observation_scope(&mut b, &value.scope);
    let i = b.create_string(value.instrument_id.as_str());
    let s = b.create_string(&value.source_id);
    let c = Decimal64::new(value.contracts.mantissa(), value.contracts.scale());
    let q = value
        .quote_value
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let ch = value
        .change_24h
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let cp = value
        .change_pct_24h
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let v = market_fb::OpenInterest::create(
        &mut b,
        &market_fb::OpenInterestArgs {
            scope: Some(scope),
            instrument_id: Some(i),
            source_id: Some(s),
            contracts: Some(&c),
            quote_value: q.as_ref(),
            change_24h: ch.as_ref(),
            change_pct_24h: cp.as_ref(),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    let source_event_id = event_id(&mut b, generation);
    let l = market_fb::LatestOpenInterest::create(
        &mut b,
        &market_fb::LatestOpenInterestArgs {
            value: Some(v),
            source_event_id: Some(source_event_id),
        },
    );
    let root = market_fb::OpenInterestLatestView::create(
        &mut b,
        &market_fb::OpenInterestLatestViewArgs {
            metadata: Some(metadata),
            open_interest: Some(l),
        },
    );
    market_fb::finish_open_interest_latest_view_buffer(&mut b, root);
    Ok(b.finished_data().to_vec())
}

fn encode_bar_view(
    actor_id: &str,
    identity: &InstanceIdentity,
    generation: u64,
    key: &MarketViewKey,
    value: &crate::Bar,
    kind: &str,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = view_context(
        &mut b,
        actor_id,
        identity,
        generation,
        key,
        value.observed_at_unix_nanos.get(),
    );
    let scope = observation_scope(&mut b, &value.scope);
    let i = b.create_string(value.instrument_id.as_str());
    let s = b.create_string(&value.source_id);
    let spec = b.create_string(&value.timeframe);
    let open = Decimal64::new(value.open.mantissa(), value.open.scale());
    let high = Decimal64::new(value.high.mantissa(), value.high.scale());
    let low = Decimal64::new(value.low.mantissa(), value.low.scale());
    let close = Decimal64::new(value.close.mantissa(), value.close.scale());
    let volume = value
        .volume
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let bar = market_fb::Bar::create(
        &mut b,
        &market_fb::BarArgs {
            scope: Some(scope),
            instrument_id: Some(i),
            source_id: Some(s),
            bar_spec_id: Some(spec),
            kind: if kind == "trades" {
                market_fb::BarKind::TRADES
            } else if kind == "quotes" {
                market_fb::BarKind::QUOTES
            } else {
                market_fb::BarKind::UNSPECIFIED
            },
            window_start_unix_nanos: 0,
            window_end_unix_nanos: value.observed_at_unix_nanos.get(),
            open: Some(&open),
            high: Some(&high),
            low: Some(&low),
            close: Some(&close),
            volume: volume.as_ref(),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    let source_event_id = event_id(&mut b, generation);
    let window_bar = market_fb::WindowBar::create(
        &mut b,
        &market_fb::WindowBarArgs {
            value: Some(bar),
            source_event_id: Some(source_event_id),
        },
    );
    let bars = b.create_vector(&[window_bar]);
    let root = market_fb::BarWindowView::create(
        &mut b,
        &market_fb::BarWindowViewArgs {
            metadata: Some(metadata),
            shard_id: 0,
            shard_count: 1,
            bars: Some(bars),
        },
    );
    market_fb::finish_bar_window_view_buffer(&mut b, root);
    Ok(b.finished_data().to_vec())
}

fn encode_greeks_view(
    actor_id: &str,
    identity: &InstanceIdentity,
    generation: u64,
    key: &MarketViewKey,
    value: &crate::OptionGreeks,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = view_context(
        &mut b,
        actor_id,
        identity,
        generation,
        key,
        value.observed_at_unix_nanos.get(),
    );
    let scope = observation_scope(&mut b, &value.scope);
    let i = b.create_string(value.instrument_id.as_str());
    let s = b.create_string(&value.source_id);
    let strike = value
        .strike
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let delta = value.delta.map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let gamma = value.gamma.map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let vega = value.vega.map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let theta = value.theta.map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let iv = value
        .implied_volatility
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let d = b.create_string(&value.derivation);
    let greeks = market_fb::Greeks::create(
        &mut b,
        &market_fb::GreeksArgs {
            scope: Some(scope),
            instrument_id: Some(i),
            source_id: Some(s),
            expiry_unix_nanos: value.expiry_unix_nanos.map(|x| x.get()),
            strike: strike.as_ref(),
            delta: delta.as_ref(),
            gamma: gamma.as_ref(),
            vega: vega.as_ref(),
            theta: theta.as_ref(),
            implied_volatility: iv.as_ref(),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
            derivation_id: Some(d),
        },
    );
    let source_event_id = event_id(&mut b, generation);
    let latest = market_fb::LatestGreeks::create(
        &mut b,
        &market_fb::LatestGreeksArgs {
            value: Some(greeks),
            source_event_id: Some(source_event_id),
        },
    );
    let root = market_fb::GreeksLatestView::create(
        &mut b,
        &market_fb::GreeksLatestViewArgs {
            metadata: Some(metadata),
            greeks: Some(latest),
        },
    );
    market_fb::finish_greeks_latest_view_buffer(&mut b, root);
    Ok(b.finished_data().to_vec())
}

fn encode_index_price_view(
    actor_id: &str,
    identity: &InstanceIdentity,
    generation: u64,
    key: &MarketViewKey,
    value: &crate::IndexPrice,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = view_context(
        &mut b,
        actor_id,
        identity,
        generation,
        key,
        value.observed_at_unix_nanos.get(),
    );
    let scope = observation_scope(&mut b, &value.scope);
    let i = b.create_string(value.instrument_id.as_str());
    let s = b.create_string(&value.source_id);
    let spot = value
        .spot_index_price
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let contract = value
        .contract_index_price
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let index = value
        .index_price
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let funding = value
        .funding_rate
        .map(|x| Decimal64::new(x.mantissa(), x.scale()));
    let v = market_fb::IndexPrice::create(
        &mut b,
        &market_fb::IndexPriceArgs {
            scope: Some(scope),
            instrument_id: Some(i),
            source_id: Some(s),
            spot_index_price: spot.as_ref(),
            contract_index_price: contract.as_ref(),
            index_price: index.as_ref(),
            funding_rate: funding.as_ref(),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    let source_event_id = event_id(&mut b, generation);
    let l = market_fb::LatestIndexPrice::create(
        &mut b,
        &market_fb::LatestIndexPriceArgs {
            value: Some(v),
            source_event_id: Some(source_event_id),
        },
    );
    let root = market_fb::IndexPriceLatestView::create(
        &mut b,
        &market_fb::IndexPriceLatestViewArgs {
            metadata: Some(metadata),
            index_price: Some(l),
        },
    );
    market_fb::finish_index_price_latest_view_buffer(&mut b, root);
    Ok(b.finished_data().to_vec())
}

fn encode_orderbook_view(
    actor_id: &str,
    identity: &InstanceIdentity,
    generation: u64,
    key: &MarketViewKey,
    book: &crate::OrderBook,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = view_context(
        &mut b,
        actor_id,
        identity,
        generation,
        key,
        book.event_time_unix_nanos.get(),
    );
    let source_id = b.create_string(&book.source_id);
    let market_id = b.create_string(book.market_id.as_str());
    let instrument_id = b.create_string(book.instrument_id.as_str());
    let identity_offset = market_fb::OrderBookIdentity::create(
        &mut b,
        &market_fb::OrderBookIdentityArgs {
            source_id: Some(source_id),
            market_id: Some(market_id),
            instrument_id: Some(instrument_id),
        },
    );
    let depth_value = match book.depth_policy {
        crate::domain::observation::order_book::DepthPolicy::Full => "full".to_owned(),
        crate::domain::observation::order_book::DepthPolicy::TopN(n) => format!("top_n:{n}"),
    };
    let depth = b.create_string(&depth_value);
    let checksum = book.checksum.as_deref().map(|value| b.create_string(value));
    let bids = encode_orderbook_levels(&mut b, &book.bids);
    let asks = encode_orderbook_levels(&mut b, &book.asks);
    let snapshot = market_fb::OrderBookSnapshotValue::create(
        &mut b,
        &market_fb::OrderBookSnapshotValueArgs {
            identity: Some(identity_offset),
            sequence: book.sequence.get(),
            source_observed_at_unix_nanos: book.event_time_unix_nanos.get(),
            received_at_unix_nanos: 0,
            checksum,
            depth_policy: Some(depth),
            bids: Some(bids),
            asks: Some(asks),
        },
    );
    let source_event_id = event_id(&mut b, generation);
    let latest = market_fb::LatestOrderBook::create(
        &mut b,
        &market_fb::LatestOrderBookArgs {
            value: Some(snapshot),
            synchronized: book.synchronized,
            source_event_id: Some(source_event_id),
        },
    );
    let root = market_fb::OrderBookLatestView::create(
        &mut b,
        &market_fb::OrderBookLatestViewArgs {
            metadata: Some(metadata),
            book: Some(latest),
        },
    );
    market_fb::finish_order_book_latest_view_buffer(&mut b, root);
    Ok(b.finished_data().to_vec())
}

fn encode_orderbook_levels<'a, A: flatbuffers::Allocator + 'a>(
    b: &mut FlatBufferBuilder<'a, A>,
    levels: &[crate::PriceLevel],
) -> flatbuffers::WIPOffset<
    flatbuffers::Vector<'a, flatbuffers::ForwardsUOffset<market_fb::OrderBookLevel<'a>>>,
> {
    let offsets = levels
        .iter()
        .map(|level| {
            let price = Decimal64::new(level.price.mantissa(), level.price.scale());
            let quantity = Decimal64::new(level.quantity.mantissa(), level.quantity.scale());
            market_fb::OrderBookLevel::create(
                b,
                &market_fb::OrderBookLevelArgs {
                    price: Some(&price),
                    quantity: Some(&quantity),
                    order_count: 0,
                },
            )
        })
        .collect::<Vec<_>>();
    b.create_vector(&offsets)
}

fn encode_freshness(
    actor_id: &str,
    identity: &InstanceIdentity,
    generation: u64,
    key: &MarketViewKey,
    value: &MarketFreshness,
) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let context = EncodeContext::view(
        actor_id,
        actor_id,
        identity.clone(),
        generation,
        key.resource_id(),
    );
    let metadata = view_metadata(
        &mut builder,
        &context,
        key,
        value.last_received_time_unix_nanos.get(),
    );
    let source_id = builder.create_string(&value.source_id);
    let scope = observation_scope(&mut builder, &value.scope);
    let data_kind = builder.create_string(value.data_kind.as_str());
    let entry = market_fb::FreshnessEntry::create(
        &mut builder,
        &market_fb::FreshnessEntryArgs {
            source_id: Some(source_id),
            scope: Some(scope),
            data_kind: Some(data_kind),
            last_event_time_unix_nanos: value.last_event_time_unix_nanos.get(),
            last_received_time_unix_nanos: value.last_received_time_unix_nanos.get(),
            age_nanos: 0,
            event_sequence: value.event_sequence.get(),
            status: match value.status {
                crate::domain::freshness::DataFreshnessStatus::Unknown => {
                    market_fb::FreshnessStatus::UNKNOWN
                }
                crate::domain::freshness::DataFreshnessStatus::Current => {
                    market_fb::FreshnessStatus::CURRENT
                }
                crate::domain::freshness::DataFreshnessStatus::Stale => {
                    market_fb::FreshnessStatus::STALE
                }
            },
        },
    );
    let root = market_fb::MarketFreshnessLatestView::create(
        &mut builder,
        &market_fb::MarketFreshnessLatestViewArgs {
            metadata: Some(metadata),
            entry: Some(entry),
        },
    );
    market_fb::finish_market_freshness_latest_view_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}
