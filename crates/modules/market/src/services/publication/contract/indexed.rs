use std::collections::BTreeMap;

use flatbuffers::FlatBufferBuilder;
use kairos_market_contract::{MarketViewKey, MarketViewKind};
use kairos_protocol::generated::kairos::common::v_2::Decimal64;
use kairos_protocol::generated::kairos::market::v_2 as market_fb;

use crate::domain::events::{MarketChange, MarketViewUpdate};
use crate::domain::freshness::MarketFreshness;

pub(crate) struct EncodedMarketView {
    pub(crate) key: MarketViewKey,
    pub(crate) bytes: Vec<u8>,
}

pub(crate) struct EncodedMarketViewBatch {
    pub(crate) mutations: Vec<kairos_conflux::IndexedMutation>,
    pub(crate) applied_state_sequence: u64,
    pub(crate) input_update_count: usize,
    pub(crate) encoded_update_count: usize,
    pub(crate) encoded_order_book_count: usize,
}

impl EncodedMarketView {
    pub(crate) fn into_mutations(self) -> Result<Vec<kairos_conflux::IndexedMutation>, String> {
        let database = kairos_market_contract::market_database(&self.key.kind).to_owned();
        let key = kairos_market_contract::market_indexed_key(&self.key)
            .map_err(|error| error.to_string())?;
        Ok(vec![kairos_conflux::IndexedMutation::Put {
            database,
            key,
            value: self.bytes,
        }])
    }
}

macro_rules! finish_current {
    ($builder:ident, $key:expr, $root:ident, $args:ident, $finish:ident, $value:expr, $source:expr, $synchronized:expr) => {{
        let scope_key = $builder.create_string(&$key.scope_key);
        let provider = $builder.create_string($key.provider.as_str());
        let qualifier = $key
            .qualifier
            .as_deref()
            .map(|value| $builder.create_string(value));
        let identity = market_fb::MarketCurrentIdentity::create(
            &mut $builder,
            &market_fb::MarketCurrentIdentityArgs {
                scope_key: Some(scope_key),
                provider: Some(provider),
                qualifier,
                source_event_id: $source,
                synchronized: $synchronized,
            },
        );
        let root = market_fb::$root::create(
            &mut $builder,
            &market_fb::$args {
                identity: Some(identity),
                value: Some($value),
            },
        );
        market_fb::$finish(&mut $builder, root);
        Ok($builder.finished_data().to_vec())
    }};
}

/// Coalesce one drained Market publication batch before encoding current
/// values. Aeron notifications retain every admitted event; the authoritative
/// current view retains only the newest state for each canonical key.
pub(crate) fn encode_latest_change_views(
    changes: &[MarketChange],
    order_books: &BTreeMap<String, crate::OrderBook>,
) -> Result<Option<EncodedMarketViewBatch>, String> {
    let mut latest = BTreeMap::<MarketViewKey, &MarketChange>::new();
    let mut input_update_count = 0;
    for change in changes {
        let Some(view) = change.view.as_ref() else {
            continue;
        };
        let Some(key) = market_view_key(view)? else {
            continue;
        };
        input_update_count += 1;
        latest.insert(key, change);
    }
    if latest.is_empty() {
        return Ok(None);
    }

    let applied_state_sequence = latest
        .values()
        .map(|change| change.sequence.get())
        .max()
        .expect("non-empty latest current-view updates have a sequence");
    let encoded_update_count = latest.len();
    let encoded_order_book_count = latest
        .values()
        .filter(|change| matches!(change.view.as_ref(), Some(MarketViewUpdate::OrderBook(_))))
        .count();
    let mut mutations = Vec::with_capacity(encoded_update_count);
    for (key, change) in latest {
        let view = change
            .view
            .as_ref()
            .expect("coalesced current-view update retains its view");
        mutations
            .extend(encode_view(change.sequence.get(), view, key, order_books)?.into_mutations()?);
    }
    Ok(Some(EncodedMarketViewBatch {
        mutations,
        applied_state_sequence,
        input_update_count,
        encoded_update_count,
        encoded_order_book_count,
    }))
}

fn market_view_key(view: &MarketViewUpdate) -> Result<Option<MarketViewKey>, String> {
    let key = match view {
        MarketViewUpdate::Observation(crate::MarketObservation::Quote(value)) => {
            MarketViewKey::new(
                value.scope.key(),
                value.provider.clone(),
                MarketViewKind::Quote,
                None::<String>,
            )
        },
        MarketViewUpdate::Observation(crate::MarketObservation::Rate(value)) => MarketViewKey::new(
            value.scope.key(),
            value.provider.clone(),
            MarketViewKind::Rate,
            Some(value.rate_id.clone()),
        ),
        MarketViewUpdate::Observation(crate::MarketObservation::Ticker24h(value)) => {
            MarketViewKey::new(
                value.scope.key(),
                value.provider.clone(),
                MarketViewKind::Ticker24h,
                None::<String>,
            )
        },
        MarketViewUpdate::Observation(crate::MarketObservation::MarkPrice(value)) => {
            MarketViewKey::new(
                value.scope.key(),
                value.provider.clone(),
                MarketViewKind::MarkPrice,
                None::<String>,
            )
        },
        MarketViewUpdate::Observation(crate::MarketObservation::FundingRate(value)) => {
            MarketViewKey::new(
                value.scope.key(),
                value.provider.clone(),
                MarketViewKind::FundingRate,
                None::<String>,
            )
        },
        MarketViewUpdate::Observation(crate::MarketObservation::OpenInterest(value)) => {
            MarketViewKey::new(
                value.scope.key(),
                value.provider.clone(),
                MarketViewKind::OpenInterest,
                None::<String>,
            )
        },
        MarketViewUpdate::Observation(crate::MarketObservation::IndexPrice(value)) => {
            MarketViewKey::new(
                value.scope.key(),
                value.provider.clone(),
                MarketViewKind::IndexPrice,
                None::<String>,
            )
        },
        MarketViewUpdate::Observation(crate::MarketObservation::Bar(value)) => {
            market_bar_view_key(value)
        },
        MarketViewUpdate::Observation(crate::MarketObservation::TradeBar(value)) => {
            market_bar_view_key(&value.bar)
        },
        MarketViewUpdate::Observation(crate::MarketObservation::QuoteBar(value)) => {
            market_bar_view_key(&value.bar)
        },
        MarketViewUpdate::Observation(crate::MarketObservation::OptionGreeks(value)) => {
            MarketViewKey::new(
                value.scope.key(),
                value.provider.clone(),
                MarketViewKind::Greeks,
                None::<String>,
            )
        },
        MarketViewUpdate::OrderBook(value) => MarketViewKey::new(
            value.market_id.to_string(),
            value.provider.clone(),
            MarketViewKind::OrderBook,
            Some(value.instrument_id.to_string()),
        ),
        MarketViewUpdate::Freshness(value) => MarketViewKey::new(
            value.scope.key(),
            value.provider.clone(),
            MarketViewKind::Freshness,
            Some(value.data_kind.as_str()),
        ),
        _ => return Ok(None),
    };
    key.map(Some).map_err(|error| error.to_string())
}

fn market_bar_view_key(
    value: &crate::Bar,
) -> kairos_market_contract::ContractResult<MarketViewKey> {
    MarketViewKey::new(
        value.scope.key(),
        value.provider.clone(),
        MarketViewKind::Bar,
        Some(value.timeframe.clone()),
    )
}

fn encode_view(
    sequence: u64,
    view: &MarketViewUpdate,
    key: MarketViewKey,
    order_books: &BTreeMap<String, crate::OrderBook>,
) -> Result<EncodedMarketView, String> {
    let encoded = match view {
        MarketViewUpdate::Observation(crate::MarketObservation::Quote(value)) => {
            let bytes = encode_quote(sequence, &key, value)?;
            EncodedMarketView { key, bytes }
        },
        MarketViewUpdate::Observation(crate::MarketObservation::Rate(value)) => {
            let bytes = encode_rate_view(sequence, &key, value)?;
            EncodedMarketView { key, bytes }
        },
        MarketViewUpdate::Observation(crate::MarketObservation::Ticker24h(value)) => {
            let bytes = encode_ticker_view(sequence, &key, value)?;
            EncodedMarketView { key, bytes }
        },
        MarketViewUpdate::Observation(crate::MarketObservation::MarkPrice(value)) => {
            let bytes = encode_mark_price_view(sequence, &key, value)?;
            EncodedMarketView { key, bytes }
        },
        MarketViewUpdate::Observation(crate::MarketObservation::FundingRate(value)) => {
            let bytes = encode_funding_view(sequence, &key, value)?;
            EncodedMarketView { key, bytes }
        },
        MarketViewUpdate::Observation(crate::MarketObservation::OpenInterest(value)) => {
            let bytes = encode_open_interest_view(sequence, &key, value)?;
            EncodedMarketView { key, bytes }
        },
        MarketViewUpdate::Observation(crate::MarketObservation::IndexPrice(value)) => {
            let bytes = encode_index_price_view(sequence, &key, value)?;
            EncodedMarketView { key, bytes }
        },
        MarketViewUpdate::Observation(crate::MarketObservation::Bar(value)) => {
            encode_bar_view_for_key(sequence, value, "unspecified", key)?
        },
        MarketViewUpdate::Observation(crate::MarketObservation::TradeBar(value)) => {
            encode_bar_view_for_key(sequence, &value.bar, "trades", key)?
        },
        MarketViewUpdate::Observation(crate::MarketObservation::QuoteBar(value)) => {
            encode_bar_view_for_key(sequence, &value.bar, "quotes", key)?
        },
        MarketViewUpdate::Observation(crate::MarketObservation::OptionGreeks(value)) => {
            let bytes = encode_greeks_view(sequence, &key, value)?;
            EncodedMarketView { key, bytes }
        },
        MarketViewUpdate::OrderBook(identity) => {
            let value = order_books
                .get(&format!("{}:{}", identity.provider, identity.market_id))
                .ok_or_else(|| "Market current order book is unavailable at flush".to_owned())?;
            if value.instrument_id != identity.instrument_id {
                return Err("Market current order-book identity changed before flush".into());
            }
            let bytes = encode_orderbook_view(sequence, &key, value)?;
            EncodedMarketView { key, bytes }
        },
        MarketViewUpdate::Freshness(value) => {
            let bytes = encode_freshness(sequence, &key, value)?;
            EncodedMarketView { key, bytes }
        },
        _ => return Err("Market current-view update has no encodable value".into()),
    };
    Ok(encoded)
}

fn encode_bar_view_for_key(
    sequence: u64,
    value: &crate::Bar,
    kind: &str,
    key: MarketViewKey,
) -> Result<EncodedMarketView, String> {
    let bytes = encode_bar_view(sequence, &key, value, kind)?;
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
    generation: u64,
    key: &MarketViewKey,
    value: &crate::Quote,
) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let scope = observation_scope(&mut builder, &value.scope);
    let instrument_id = builder.create_string(value.instrument_id.as_str());
    let provider = builder.create_string(&value.provider);
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
            provider: Some(provider),
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
    finish_current!(
        builder,
        key,
        MarketQuoteCurrent,
        MarketQuoteCurrentArgs,
        finish_market_quote_current_buffer,
        quote,
        Some(source_event_id),
        false
    )
}

fn event_id<'a, A: flatbuffers::Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    sequence: u64,
) -> flatbuffers::WIPOffset<&'a str> {
    builder.create_string(&format!("market:{sequence}"))
}

fn encode_rate_view(
    generation: u64,
    key: &MarketViewKey,
    value: &crate::Rate,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let rate_id = b.create_string(&value.rate_id);
    let scope = observation_scope(&mut b, &value.scope);
    let instrument_id = b.create_string(value.instrument_id.as_str());
    let provider = b.create_string(&value.provider);
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
            provider: Some(provider),
            basis: Some(basis),
            value: Some(&v),
            mark_price: mark.as_ref(),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    let source_event_id = event_id(&mut b, generation);
    finish_current!(
        b,
        key,
        MarketRateCurrent,
        MarketRateCurrentArgs,
        finish_market_rate_current_buffer,
        value_offset,
        Some(source_event_id),
        false
    )
}

fn encode_ticker_view(
    generation: u64,
    key: &MarketViewKey,
    value: &crate::Ticker24h,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let scope = observation_scope(&mut b, &value.scope);
    let i = b.create_string(value.instrument_id.as_str());
    let s = b.create_string(&value.provider);
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
            provider: Some(s),
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
    finish_current!(
        b,
        key,
        MarketTicker24hCurrent,
        MarketTicker24hCurrentArgs,
        finish_market_ticker_24h_current_buffer,
        v,
        Some(source_event_id),
        false
    )
}

fn encode_mark_price_view(
    generation: u64,
    key: &MarketViewKey,
    value: &crate::MarkPrice,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let scope = observation_scope(&mut b, &value.scope);
    let i = b.create_string(value.instrument_id.as_str());
    let s = b.create_string(&value.provider);
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
            provider: Some(s),
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
    finish_current!(
        b,
        key,
        MarketMarkPriceCurrent,
        MarketMarkPriceCurrentArgs,
        finish_market_mark_price_current_buffer,
        v,
        Some(source_event_id),
        false
    )
}

fn encode_funding_view(
    generation: u64,
    key: &MarketViewKey,
    value: &crate::FundingRate,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let scope = observation_scope(&mut b, &value.scope);
    let i = b.create_string(value.instrument_id.as_str());
    let s = b.create_string(&value.provider);
    let fr = Decimal64::new(value.funding_rate.mantissa(), value.funding_rate.scale());
    let v = market_fb::FundingRate::create(
        &mut b,
        &market_fb::FundingRateArgs {
            scope: Some(scope),
            instrument_id: Some(i),
            provider: Some(s),
            funding_rate: Some(&fr),
            funding_period_seconds: value.funding_period_seconds.unwrap_or_default(),
            next_funding_time_unix_nanos: value.next_funding_time_unix_nanos.map_or(0, |x| x.get()),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    let source_event_id = event_id(&mut b, generation);
    finish_current!(
        b,
        key,
        MarketFundingRateCurrent,
        MarketFundingRateCurrentArgs,
        finish_market_funding_rate_current_buffer,
        v,
        Some(source_event_id),
        false
    )
}

fn encode_open_interest_view(
    generation: u64,
    key: &MarketViewKey,
    value: &crate::OpenInterest,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let scope = observation_scope(&mut b, &value.scope);
    let i = b.create_string(value.instrument_id.as_str());
    let s = b.create_string(&value.provider);
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
            provider: Some(s),
            contracts: Some(&c),
            quote_value: q.as_ref(),
            change_24h: ch.as_ref(),
            change_pct_24h: cp.as_ref(),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    let source_event_id = event_id(&mut b, generation);
    finish_current!(
        b,
        key,
        MarketOpenInterestCurrent,
        MarketOpenInterestCurrentArgs,
        finish_market_open_interest_current_buffer,
        v,
        Some(source_event_id),
        false
    )
}

fn encode_bar_view(
    generation: u64,
    key: &MarketViewKey,
    value: &crate::Bar,
    kind: &str,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let scope = observation_scope(&mut b, &value.scope);
    let i = b.create_string(value.instrument_id.as_str());
    let s = b.create_string(&value.provider);
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
            provider: Some(s),
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
    finish_current!(
        b,
        key,
        MarketBarCurrent,
        MarketBarCurrentArgs,
        finish_market_bar_current_buffer,
        bar,
        Some(source_event_id),
        false
    )
}

fn encode_greeks_view(
    generation: u64,
    key: &MarketViewKey,
    value: &crate::OptionGreeks,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let scope = observation_scope(&mut b, &value.scope);
    let i = b.create_string(value.instrument_id.as_str());
    let s = b.create_string(&value.provider);
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
            provider: Some(s),
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
    finish_current!(
        b,
        key,
        MarketGreeksCurrent,
        MarketGreeksCurrentArgs,
        finish_market_greeks_current_buffer,
        greeks,
        Some(source_event_id),
        false
    )
}

fn encode_index_price_view(
    generation: u64,
    key: &MarketViewKey,
    value: &crate::IndexPrice,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let scope = observation_scope(&mut b, &value.scope);
    let i = b.create_string(value.instrument_id.as_str());
    let s = b.create_string(&value.provider);
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
            provider: Some(s),
            spot_index_price: spot.as_ref(),
            contract_index_price: contract.as_ref(),
            index_price: index.as_ref(),
            funding_rate: funding.as_ref(),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    let source_event_id = event_id(&mut b, generation);
    finish_current!(
        b,
        key,
        MarketIndexPriceCurrent,
        MarketIndexPriceCurrentArgs,
        finish_market_index_price_current_buffer,
        v,
        Some(source_event_id),
        false
    )
}

fn encode_orderbook_view(
    generation: u64,
    key: &MarketViewKey,
    book: &crate::OrderBook,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let provider = b.create_string(&book.provider);
    let market_id = b.create_string(book.market_id.as_str());
    let instrument_id = b.create_string(book.instrument_id.as_str());
    let identity_offset = market_fb::OrderBookIdentity::create(
        &mut b,
        &market_fb::OrderBookIdentityArgs {
            provider: Some(provider),
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
    finish_current!(
        b,
        key,
        MarketOrderBookCurrent,
        MarketOrderBookCurrentArgs,
        finish_market_order_book_current_buffer,
        snapshot,
        Some(source_event_id),
        book.synchronized
    )
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
    _generation: u64,
    key: &MarketViewKey,
    value: &MarketFreshness,
) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let provider = builder.create_string(&value.provider);
    let scope = observation_scope(&mut builder, &value.scope);
    let data_kind = builder.create_string(value.data_kind.as_str());
    let entry = market_fb::FreshnessEntry::create(
        &mut builder,
        &market_fb::FreshnessEntryArgs {
            provider: Some(provider),
            scope: Some(scope),
            data_kind: Some(data_kind),
            last_event_time_unix_nanos: value.last_event_time_unix_nanos.get(),
            last_received_time_unix_nanos: value.last_received_time_unix_nanos.get(),
            age_nanos: 0,
            event_sequence: value.event_sequence.get(),
            status: match value.status {
                crate::domain::freshness::DataFreshnessStatus::Unknown => {
                    market_fb::FreshnessStatus::UNKNOWN
                },
                crate::domain::freshness::DataFreshnessStatus::Current => {
                    market_fb::FreshnessStatus::CURRENT
                },
                crate::domain::freshness::DataFreshnessStatus::Stale => {
                    market_fb::FreshnessStatus::STALE
                },
            },
        },
    );
    finish_current!(
        builder,
        key,
        MarketFreshnessCurrent,
        MarketFreshnessCurrentArgs,
        finish_market_freshness_current_buffer,
        entry,
        None,
        false
    )
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use kairos_conflux::IndexedMutation;
    use kairos_primitives::market::Provider;
    use kairos_primitives::reference::InstrumentId;
    use kairos_primitives::time::{Sequence, UnixNanos};
    use kairos_protocol::generated::kairos::market::v_2 as market_fb;

    use super::encode_latest_change_views;
    use crate::domain::events::{MarketChange, MarketViewUpdate};
    use crate::{MarketEvent, MarketObservation, ObservationScope, OrderBook, PriceLevel, Quote};

    fn quote_change(sequence: u64, market_id: &str, bid_price: &str) -> MarketChange {
        let quote = Quote {
            scope: ObservationScope::market(market_id).unwrap(),
            instrument_id: InstrumentId::new(format!("instrument:{market_id}")).unwrap(),
            bid_price: Some(bid_price.parse().unwrap()),
            bid_quantity: Some("1".parse().unwrap()),
            ask_price: None,
            ask_quantity: None,
            bid_venue_code: None,
            ask_venue_code: None,
            tape: None,
            observed_at_unix_nanos: UnixNanos::new(sequence),
            provider: Provider::new("test").unwrap(),
        };
        MarketChange {
            sequence: Sequence::new(sequence),
            event: Some(MarketEvent::Observation(MarketObservation::Quote(
                quote.clone(),
            ))),
            view: Some(MarketViewUpdate::Observation(MarketObservation::Quote(
                quote,
            ))),
        }
    }

    fn put_value(mutation: &IndexedMutation) -> &[u8] {
        match mutation {
            IndexedMutation::Put { value, .. } => value,
            other => panic!("expected current-view put, got {other:?}"),
        }
    }

    #[test]
    fn coalesces_same_key_before_encoding_and_keeps_latest_value() {
        let batch = encode_latest_change_views(
            &[
                quote_change(1, "market:btc", "100"),
                quote_change(2, "market:btc", "101"),
            ],
            &BTreeMap::new(),
        )
        .unwrap()
        .unwrap();

        assert_eq!(batch.input_update_count, 2);
        assert_eq!(batch.encoded_update_count, 1);
        assert_eq!(batch.encoded_order_book_count, 0);
        assert_eq!(batch.applied_state_sequence, 2);
        assert_eq!(batch.mutations.len(), 1);

        let root = market_fb::root_as_market_quote_current(put_value(&batch.mutations[0])).unwrap();
        assert_eq!(root.identity().source_event_id(), Some("market:2"));
        assert_eq!(root.value().bid_price().unwrap().mantissa(), 101);
    }

    #[test]
    fn keeps_distinct_keys_in_one_batch_and_uses_highest_sequence_watermark() {
        let batch = encode_latest_change_views(
            &[
                quote_change(9, "market:btc", "100"),
                quote_change(4, "market:eth", "10"),
            ],
            &BTreeMap::new(),
        )
        .unwrap()
        .unwrap();

        assert_eq!(batch.input_update_count, 2);
        assert_eq!(batch.encoded_update_count, 2);
        assert_eq!(batch.applied_state_sequence, 9);
        assert_eq!(batch.mutations.len(), 2);
    }

    #[test]
    fn event_only_change_does_not_create_a_current_view_mutation() {
        let mut change = quote_change(1, "market:btc", "100");
        change.view = None;

        assert!(
            encode_latest_change_views(&[change], &BTreeMap::new())
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn order_book_markers_encode_only_the_final_actor_book() {
        let provider = Provider::new("test").unwrap();
        let book = OrderBook::snapshot_with_provider(
            provider.clone(),
            "market:btc",
            "instrument:btc",
            12_u64,
            12_u64,
            vec![PriceLevel {
                price: "102".parse().unwrap(),
                quantity: "3".parse().unwrap(),
            }],
            vec![],
        )
        .unwrap();
        let marker = MarketViewUpdate::OrderBook((&book).into());
        let changes = [
            MarketChange {
                sequence: Sequence::new(10),
                event: None,
                view: Some(marker.clone()),
            },
            MarketChange {
                sequence: Sequence::new(12),
                event: None,
                view: Some(marker),
            },
        ];
        let books = BTreeMap::from([(book.key(), book)]);

        let batch = encode_latest_change_views(&changes, &books)
            .unwrap()
            .unwrap();

        assert_eq!(batch.input_update_count, 2);
        assert_eq!(batch.encoded_update_count, 1);
        assert_eq!(batch.encoded_order_book_count, 1);
        let root =
            market_fb::root_as_market_order_book_current(put_value(&batch.mutations[0])).unwrap();
        assert_eq!(root.identity().source_event_id(), Some("market:12"));
        assert_eq!(root.value().sequence(), 12);
        assert_eq!(root.value().bids().get(0).price().mantissa(), 102);
    }
}
