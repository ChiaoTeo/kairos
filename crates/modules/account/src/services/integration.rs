//! Account-owned mapping of normalized Integration facts.
//!
//! Integration already defines the connection capabilities. Account keeps
//! concrete dependency holders here instead of mirroring those capabilities
//! with another public protocol hierarchy.

use std::collections::BTreeMap;
use std::path::Path;
use std::sync::{Arc, Mutex};

use crate::domain::{
    AccountEvent, AccountModel, AccountObservedFill, AccountOrderObservation, AccountSegment,
    AccountSnapshot, AccountStatus, AssetId, Balance, FillId, InstrumentId, MarginMode, Money,
    OpenOrder, Position, PositionMode, SegmentKey, SignedQuantity,
};

#[derive(Clone, Default)]
pub(crate) struct AccountInstrumentResolver {
    client: Option<Arc<kairos_reference_contract::ReferenceClient>>,
    cache: Arc<Mutex<BTreeMap<String, (InstrumentId, Option<kairos_primitives::MarketId>)>>>,
    cache_generation: Arc<Mutex<Option<u64>>>,
    #[cfg(test)]
    fixture_markets: Arc<Vec<kairos_reference_contract::ReferenceMarket>>,
    #[cfg(test)]
    fixture_instruments: Arc<Vec<kairos_reference_contract::Instrument>>,
}

impl AccountInstrumentResolver {
    pub(crate) fn from_reference_database(
        database: impl AsRef<Path>,
        actor_id: &str,
    ) -> Result<Self, String> {
        Ok(Self {
            client: Some(Arc::new(
                kairos_reference_contract::ReferenceClient::connect(
                    kairos_reference_contract::ReferenceEndpoint {
                        database: database.as_ref().to_path_buf(),
                        actor_id: actor_id.to_owned(),
                        aeron_dir: None,
                        aeron_channel: kairos_transport::DEFAULT_CHANNEL.into(),
                        event_stream_id: kairos_transport::stream_ids::REFERENCE_CHANGES,
                    },
                ),
            )),
            ..Default::default()
        })
    }

    fn resolve(
        &self,
        provider: &kairos_integration::ParticipantInstrumentRef,
    ) -> Result<(InstrumentId, Option<kairos_primitives::MarketId>), String> {
        let key = format!(
            "{}|{}|{}",
            provider.participant.id.to_ascii_lowercase(),
            provider
                .instrument_type
                .as_ref()
                .map(|value| value.as_str())
                .unwrap_or_default()
                .to_ascii_lowercase(),
            provider.source_symbol.as_str().to_ascii_uppercase(),
        );
        if let Some(client) = &self.client {
            let generation = client
                .watermark()
                .map_err(|error| error.to_string())?
                .generation;
            let mut cached_generation = self
                .cache_generation
                .lock()
                .map_err(|_| "Reference identity cache generation lock poisoned".to_string())?;
            if *cached_generation != Some(generation) {
                self.cache
                    .lock()
                    .map_err(|_| "Reference identity cache lock poisoned".to_string())?
                    .clear();
                *cached_generation = Some(generation);
            }
            if let Some(value) = self
                .cache
                .lock()
                .map_err(|_| "Reference identity cache lock poisoned".to_string())?
                .get(&key)
                .cloned()
            {
                return Ok(value);
            }
        }

        let resolved = self.resolve_uncached(provider)?;
        if self.client.is_some() {
            self.cache
                .lock()
                .map_err(|_| "Reference identity cache lock poisoned".to_string())?
                .insert(key, resolved.clone());
        }
        Ok(resolved)
    }

    fn resolve_uncached(
        &self,
        provider: &kairos_integration::ParticipantInstrumentRef,
    ) -> Result<(InstrumentId, Option<kairos_primitives::MarketId>), String> {
        let symbol = provider.source_symbol.as_str();
        let (markets, instruments) = self.identity_snapshot()?;
        if provider.participant.id.eq_ignore_ascii_case("ibkr") {
            let matches = instruments
                .iter()
                .filter(|value| {
                    value.symbol.eq_ignore_ascii_case(symbol)
                        && value.instrument_type == kairos_primitives::InstrumentKind::Equity
                        && matches!(value.status.as_str(), "active" | "trading")
                })
                .collect::<Vec<_>>();
            let [instrument] = matches.as_slice() else {
                return Err(identity_resolution_error(provider, matches.len()));
            };
            return Ok((
                InstrumentId::new(instrument.instrument_id.clone())
                    .map_err(|error| error.to_string())?,
                None,
            ));
        }

        let domain = provider
            .instrument_type
            .as_ref()
            .map(|value| value.as_str())
            .unwrap_or_default();
        let matches = markets
            .iter()
            .filter(|value| {
                provider_id_from_exchange(&value.exchange_id)
                    .is_some_and(|source| source.eq_ignore_ascii_case(&provider.participant.id))
                    && value
                        .venue_symbol
                        .as_deref()
                        .is_some_and(|value| value.eq_ignore_ascii_case(symbol))
                    && matches!(value.status.as_str(), "active" | "trading")
                    && provider_domain_matches_market(domain, &value.instrument_kind)
            })
            .collect::<Vec<_>>();
        let [market] = matches.as_slice() else {
            return Err(identity_resolution_error(provider, matches.len()));
        };
        Ok((
            InstrumentId::new(market.instrument_id.clone()).map_err(|error| error.to_string())?,
            Some(kairos_primitives::MarketId::new(market.market_id.clone())?),
        ))
    }

    fn identity_snapshot(
        &self,
    ) -> Result<
        (
            Vec<kairos_reference_contract::ReferenceMarket>,
            Vec<kairos_reference_contract::Instrument>,
        ),
        String,
    > {
        if let Some(client) = &self.client {
            let snapshot = client
                .account_snapshot()
                .map_err(|error| error.to_string())?;
            return Ok((
                snapshot.markets.into_iter().map(reference_market).collect(),
                snapshot.instruments,
            ));
        }
        #[cfg(test)]
        {
            return Ok((
                self.fixture_markets.as_ref().clone(),
                self.fixture_instruments.as_ref().clone(),
            ));
        }
        #[cfg(not(test))]
        {
            Err("Reference identity client is not configured".into())
        }
    }

    #[cfg(test)]
    fn fixture(
        markets: Vec<kairos_reference_contract::ReferenceMarket>,
        instruments: Vec<kairos_reference_contract::Instrument>,
    ) -> Self {
        Self {
            fixture_markets: Arc::new(markets),
            fixture_instruments: Arc::new(instruments),
            ..Default::default()
        }
    }
}

fn provider_domain_matches_market(domain: &str, market_type: &str) -> bool {
    let domain = domain.to_ascii_lowercase();
    let market_type = market_type.to_ascii_lowercase();
    if domain.contains("spot") || domain.contains("margin") {
        return market_type == "spot";
    }
    if domain.contains("option") {
        return matches!(market_type.as_str(), "option" | "options");
    }
    if domain.contains("future") || domain.contains("swap") {
        return matches!(
            market_type.as_str(),
            "future" | "futures" | "perpetual" | "swap"
        );
    }
    true
}

fn identity_resolution_error(
    provider: &kairos_integration::ParticipantInstrumentRef,
    matches: usize,
) -> String {
    format!(
        "Reference identity resolution expected one match for {}/{}/{}, found {matches}",
        provider.participant.id,
        provider
            .instrument_type
            .as_ref()
            .map(|value| value.as_str())
            .unwrap_or("unspecified"),
        provider.source_symbol
    )
}

fn provider_id_from_exchange(exchange_id: &str) -> Option<&str> {
    ["binance", "okx", "hyperliquid", "ibkr"]
        .into_iter()
        .find(|provider| {
            exchange_id.eq_ignore_ascii_case(provider)
                || exchange_id
                    .strip_prefix(provider)
                    .is_some_and(|suffix| suffix.starts_with('-') || suffix.starts_with(':'))
                || exchange_id
                    .strip_prefix("exchange:")
                    .is_some_and(|value| value.eq_ignore_ascii_case(provider))
        })
}

fn reference_market(
    value: kairos_reference_contract::Market,
) -> kairos_reference_contract::ReferenceMarket {
    kairos_reference_contract::ReferenceMarket {
        market_id: value.market_id,
        instrument_id: value.instrument_id,
        listing_id: value.listing_id,
        exchange_id: value.exchange_id,
        instrument_kind: value.instrument_kind.to_string(),
        asset_type: value.asset_type.map(|value| value.to_string()),
        venue_symbol: value.venue_symbol,
        base_asset_id: value.base_asset_id,
        quote_asset_id: value.quote_asset_id,
        underlying_instrument_id: value.underlying_instrument_id,
        status: value.status,
        price_tick: value.price_tick,
        quantity_tick: value.quantity_tick,
        minimum_quantity: value.minimum_quantity,
        minimum_notional: value.minimum_notional,
        price_precision: value.price_precision,
        quantity_precision: value.quantity_precision,
        contract_size: value.contract_size,
        effective_from_unix_nanos: value.effective_from_unix_nanos,
        effective_to_unix_nanos: value.effective_to_unix_nanos,
    }
}

/// Account-owned heterogeneous holder for concrete Integration event sources.
/// It is a dispatch container, not another implementation of Integration's
/// provider capability trait.
pub(crate) enum AccountAsyncEventSource {
    BinanceSpot {
        segment_key: SegmentKey,
        source: kairos_integration::participants::binance::spot::BinanceSpotUserWebSocketConnection,
    },
    BinanceUsdM {
        segment_key: SegmentKey,
        source: kairos_integration::participants::binance::usdm::BinanceUsdMUserWebSocketConnection,
    },
    BinanceCoinM {
        segment_key: SegmentKey,
        source: kairos_integration::participants::binance::coinm::BinanceCoinMUserWebSocketConnection,
    },
    BinanceOptions {
        segment_key: SegmentKey,
        source: kairos_integration::participants::binance::options::BinanceOptionsUserWebSocketConnection,
    },
    BinanceMargin {
        segment_key: SegmentKey,
        source: kairos_integration::participants::binance::margin::BinanceMarginUserWebSocketConnection,
    },
    Ibkr {
        segment_key: SegmentKey,
        source: kairos_integration::participants::ibkr::IbkrAccountStreamConnection,
    },
    OkxTrading {
        segment_key: SegmentKey,
        source: kairos_integration::participants::okx::private::OkxPrivateWebSocketConnection,
    },
}

/// Concrete async account-read capabilities selected by Account composition.
/// This enum is deliberately private: it keeps heterogeneous provider handles
/// without publishing a second Account-owned provider protocol.
pub(crate) enum AccountAsyncSnapshotConnection {
    BinanceSpot(kairos_integration::participants::binance::spot::BinanceSpotRestConnection),
    BinanceFunding(
        kairos_integration::participants::binance::funding::BinanceFundingRestConnection,
    ),
    BinanceMargin(kairos_integration::participants::binance::margin::BinanceMarginRestConnection),
    BinanceUsdM(kairos_integration::participants::binance::usdm::BinanceUsdMRestConnection),
    BinanceCoinM(kairos_integration::participants::binance::coinm::BinanceCoinMRestConnection),
    BinanceOptions(
        kairos_integration::participants::binance::options::BinanceOptionsRestConnection,
    ),
    Ibkr(kairos_integration::participants::ibkr::IbkrAccountQueryConnection),
    OkxTrading(kairos_integration::participants::okx::private::OkxPrivateRestConnection),
}

impl AccountAsyncSnapshotConnection {
    pub(crate) fn into_conflux(
        self,
        key: String,
        system: &mut kairos_conflux::ConfluxSystem,
    ) -> Result<(), String> {
        match self {
            Self::BinanceSpot(connection) => {
                system
                    .binance_spot_rest_connections
                    .ensure_with(key, 1, || connection)
            }
            Self::BinanceFunding(connection) => system
                .binance_funding_rest_connections
                .ensure_with(key, 1, || connection),
            Self::BinanceMargin(connection) => {
                system
                    .binance_margin_rest_connections
                    .ensure_with(key, 1, || connection)
            }
            Self::BinanceUsdM(connection) => {
                system
                    .binance_usdm_rest_connections
                    .ensure_with(key, 1, || connection)
            }
            Self::BinanceCoinM(connection) => {
                system
                    .binance_coinm_rest_connections
                    .ensure_with(key, 1, || connection)
            }
            Self::BinanceOptions(connection) => system
                .binance_options_rest_connections
                .ensure_with(key, 1, || connection),
            Self::Ibkr(connection) => {
                system
                    .ibkr_account_query_connections
                    .ensure_with(key, 1, || connection)
            }
            Self::OkxTrading(connection) => {
                system
                    .okx_private_rest_connections
                    .ensure_with(key, 1, || connection)
            }
        }
        .map(|_| ())
        .map_err(|error| error.to_string())
    }
}

impl AccountAsyncEventSource {
    pub(crate) fn into_conflux(
        self,
        system: &mut kairos_conflux::ConfluxSystem,
    ) -> Result<(), String> {
        let key = self.segment_key().to_string();
        match self {
            Self::BinanceSpot { source, .. } => system
                .binance_spot_user_websocket_connections
                .ensure_with(key, 1, || source),
            Self::BinanceUsdM { source, .. } => system
                .binance_usdm_user_websocket_connections
                .ensure_with(key, 1, || source),
            Self::BinanceCoinM { source, .. } => system
                .binance_coinm_user_websocket_connections
                .ensure_with(key, 1, || source),
            Self::BinanceOptions { source, .. } => system
                .binance_options_user_websocket_connections
                .ensure_with(key, 1, || source),
            Self::BinanceMargin { source, .. } => system
                .binance_margin_user_websocket_connections
                .ensure_with(key, 1, || source),
            Self::Ibkr { source, .. } => {
                system
                    .ibkr_account_stream_connections
                    .ensure_with(key, 1, || source)
            }
            Self::OkxTrading { source, .. } => system
                .okx_private_websocket_connections
                .ensure_with(key, 1, || source),
        }
        .map(|_| ())
        .map_err(|error| error.to_string())
    }

    pub(crate) fn segment_key(&self) -> &SegmentKey {
        match self {
            Self::BinanceSpot { segment_key, .. }
            | Self::BinanceUsdM { segment_key, .. }
            | Self::BinanceCoinM { segment_key, .. }
            | Self::BinanceOptions { segment_key, .. }
            | Self::BinanceMargin { segment_key, .. }
            | Self::Ibkr { segment_key, .. }
            | Self::OkxTrading { segment_key, .. } => segment_key,
        }
    }
}
use kairos_integration::{
    ExternalAccountEvent, ExternalAccountModel, ExternalAccountSegment, ExternalAccountStatus,
    ExternalBalance, ExternalDecimal, ExternalMarginMode, ExternalOrderStatus,
    ExternalPositionMode,
};

pub(crate) enum AccountSnapshotGateway {
    Memory(BTreeMap<String, AccountSnapshot>),
}

impl AccountSnapshotGateway {
    pub(crate) fn memory(snapshots: BTreeMap<String, AccountSnapshot>) -> Self {
        Self::Memory(snapshots)
    }

    pub(crate) fn split(self) -> BTreeMap<String, Self> {
        match self {
            Self::Memory(snapshots) => snapshots
                .into_iter()
                .map(|(key, snapshot)| {
                    (key.clone(), Self::Memory(BTreeMap::from([(key, snapshot)])))
                })
                .collect(),
        }
    }

    pub(crate) fn fetch(&mut self, segment: &AccountSegment) -> Result<AccountSnapshot, String> {
        match self {
            Self::Memory(snapshots) => snapshots
                .get(segment.segment_key.as_str())
                .cloned()
                .ok_or_else(|| format!("missing snapshot for segment: {}", segment.segment_key)),
        }
    }
}

pub(crate) fn external_segment(segment: &AccountSegment) -> ExternalAccountSegment {
    ExternalAccountSegment {
        identity: kairos_integration::ExternalAccountIdentity {
            broker: segment.identity.broker.to_string(),
            account_id: segment.identity.account_id.clone(),
        },
        segment_key: kairos_primitives::SegmentKey::new(segment.segment_key.to_string())
            .expect("validated account segment key"),
        environment: segment.environment.clone(),
        account_model: segment.account_model.clone(),
    }
}

fn signed_quantity(value: ExternalDecimal) -> Result<SignedQuantity, String> {
    SignedQuantity::new(value.mantissa, value.scale).map_err(Into::into)
}

fn quantity(value: ExternalDecimal) -> Result<kairos_primitives::Quantity, String> {
    kairos_primitives::Quantity::new(value.mantissa, value.scale).map_err(|error| error.to_string())
}

fn price(value: ExternalDecimal) -> Result<kairos_primitives::Price, String> {
    kairos_primitives::Price::new(value.mantissa, value.scale).map_err(|error| error.to_string())
}

fn money(value: ExternalDecimal) -> Result<Money, String> {
    Money::new(value.mantissa, value.scale).map_err(Into::into)
}

fn map_balance(value: ExternalBalance) -> Result<Balance, String> {
    Ok(Balance {
        asset_id: AssetId::new(value.asset_id.to_string()).expect("validated asset id"),
        asset_code: value.asset_code,
        total: signed_quantity(value.total)?,
        available: value.available.map(signed_quantity).transpose()?,
        locked: value.locked.map(signed_quantity).transpose()?,
        borrowed: value.borrowed.map(signed_quantity).transpose()?,
        interest: value.interest.map(signed_quantity).transpose()?,
    })
}

fn map_position(
    value: kairos_integration::ExternalPosition,
    resolver: &AccountInstrumentResolver,
) -> Result<Position, String> {
    let (instrument_id, market_id) = resolver.resolve(&value.participant_instrument)?;
    Ok(Position {
        instrument_id,
        market_id,
        position_side: value.position_side,
        quantity: signed_quantity(value.quantity)?,
        average_price: value.average_price.map(price).transpose()?,
        mark_price: value.mark_price.map(price).transpose()?,
        unrealized_pnl: value.unrealized_pnl.map(money).transpose()?,
        realized_pnl: value.realized_pnl.map(money).transpose()?,
        updated_at_unix_nanos: value.updated_at_unix_nanos,
    })
}

pub(crate) fn map_snapshot(
    value: kairos_integration::ExternalAccountSnapshot,
    resolver: &AccountInstrumentResolver,
) -> Result<AccountSnapshot, String> {
    Ok(AccountSnapshot {
        segment_key: SegmentKey::new(value.segment_key.to_string())
            .expect("validated account segment key"),
        balances: value
            .balances
            .into_iter()
            .map(map_balance)
            .collect::<Result<_, _>>()?,
        collateral: value
            .collateral
            .into_iter()
            .map(map_balance)
            .collect::<Result<_, _>>()?,
        positions: value
            .positions
            .into_iter()
            .map(|value| map_position(value, resolver))
            .collect::<Result<_, _>>()?,
        open_orders: value
            .open_orders
            .into_iter()
            .map(|value| map_open_order(value, resolver))
            .collect::<Result<_, _>>()?,
        status: map_status(value.status),
        observed_at_unix_nanos: value.observed_at_unix_nanos,
        equity: value.equity.map(money).transpose()?,
        initial_equity: value.initial_equity.map(money).transpose()?,
        net_profit: value.net_profit.map(money).transpose()?,
        account_model: value.account_model.map(map_model),
        margin_mode: value.margin_mode.map(map_margin),
        position_mode: value.position_mode.map(map_position_mode),
        kind: if value.partial {
            crate::domain::SnapshotKind::Delta
        } else {
            crate::domain::SnapshotKind::Full
        },
    })
}

fn map_open_order(
    value: kairos_integration::ExternalOpenOrder,
    resolver: &AccountInstrumentResolver,
) -> Result<OpenOrder, String> {
    let (instrument_id, market_id) = resolver.resolve(&value.participant_instrument)?;
    Ok(OpenOrder {
        order_id: value.order_id,
        remote_order_id: value.remote_order_id,
        instrument_id,
        market_id,
        side: value.side,
        quantity: quantity(value.quantity)?,
        filled_quantity: quantity(value.filled_quantity)?,
        status: value.status,
    })
}

fn map_status(value: ExternalAccountStatus) -> AccountStatus {
    match value {
        ExternalAccountStatus::Unknown => AccountStatus::Unknown,
        ExternalAccountStatus::Ready => AccountStatus::Ready,
        ExternalAccountStatus::Reconciling => AccountStatus::Reconciling,
        ExternalAccountStatus::TypeMismatch => AccountStatus::TypeMismatch,
        ExternalAccountStatus::Suspended => AccountStatus::Suspended,
        ExternalAccountStatus::Unavailable => AccountStatus::Unavailable,
    }
}

fn map_model(value: ExternalAccountModel) -> AccountModel {
    match value {
        ExternalAccountModel::NoMargin => AccountModel::NoMargin,
        ExternalAccountModel::Margin => AccountModel::Margin,
        ExternalAccountModel::Contract => AccountModel::Contract,
        ExternalAccountModel::ContractUnified => AccountModel::ContractUnified,
        ExternalAccountModel::Unified => AccountModel::Unified,
        ExternalAccountModel::PortfolioMargin => AccountModel::PortfolioMargin,
    }
}

fn map_margin(value: ExternalMarginMode) -> MarginMode {
    match value {
        ExternalMarginMode::Cross => MarginMode::Cross,
        ExternalMarginMode::Isolated => MarginMode::Isolated,
    }
}

fn map_position_mode(value: ExternalPositionMode) -> PositionMode {
    match value {
        ExternalPositionMode::OneWay => PositionMode::OneWay,
        ExternalPositionMode::Hedge => PositionMode::Hedge,
    }
}

fn map_order_status(value: ExternalOrderStatus) -> (&'static str, bool) {
    match value {
        ExternalOrderStatus::Acknowledged => ("acknowledged", true),
        ExternalOrderStatus::PartiallyFilled => ("partially_filled", true),
        ExternalOrderStatus::Filled => ("filled", false),
        ExternalOrderStatus::Canceled => ("canceled", false),
        ExternalOrderStatus::Rejected => ("rejected", false),
        ExternalOrderStatus::Expired => ("expired", false),
        ExternalOrderStatus::Unknown => ("unknown", true),
    }
}

pub(crate) fn map_event(
    value: ExternalAccountEvent,
    resolver: &AccountInstrumentResolver,
) -> Result<AccountEvent, String> {
    Ok(match value {
        ExternalAccountEvent::Batch(values) => AccountEvent::Batch(
            values
                .into_iter()
                .map(|value| map_event(value, resolver))
                .collect::<Result<_, _>>()?,
        ),
        ExternalAccountEvent::Snapshot(value) => {
            AccountEvent::Snapshot(map_snapshot(value, resolver)?)
        }
        ExternalAccountEvent::Order(value) => {
            let (status, active) = map_order_status(value.status);
            AccountEvent::OrderObserved(AccountOrderObservation {
                order_id: value.order_id,
                status: match status {
                    "acknowledged" => kairos_primitives::OrderStatus::Acknowledged,
                    "partially_filled" => kairos_primitives::OrderStatus::PartiallyFilled,
                    "filled" => kairos_primitives::OrderStatus::Filled,
                    "canceled" => kairos_primitives::OrderStatus::Canceled,
                    "rejected" => kairos_primitives::OrderStatus::Rejected,
                    "expired" => kairos_primitives::OrderStatus::Expired,
                    _ => kairos_primitives::OrderStatus::Unknown,
                },
                active,
                remote_order_id: value.remote_order_id,
                filled_quantity: value.filled_quantity.map(quantity).transpose()?,
                observed_at_unix_nanos: value.occurred_at_unix_nanos,
            })
        }
        ExternalAccountEvent::Fill(value) => {
            let (instrument_id, _) = resolver.resolve(&value.participant_instrument)?;
            AccountEvent::ObservedFill(AccountObservedFill {
                fill_id: FillId::new(value.fill_id.to_string()).expect("validated fill id"),
                order_id: Some(value.order_id),
                remote_order_id: None,
                segment_key: SegmentKey::new(value.segment_key.to_string())
                    .expect("validated account segment key"),
                instrument_id,
                quantity: quantity(value.quantity)?,
                price: price(value.price)?,
                side: if value.side.eq_ignore_ascii_case("sell") {
                    crate::domain::OrderSide::Sell
                } else {
                    crate::domain::OrderSide::Buy
                },
                occurred_at_unix_nanos: value.occurred_at_unix_nanos,
            })
        }
    })
}

#[cfg(test)]
mod identity_tests {
    use super::AccountInstrumentResolver;
    use kairos_integration::{
        ParticipantInstrumentRef, ParticipantInstrumentTypeRef, ParticipantKind, ParticipantRef,
    };

    #[test]
    fn resolves_exchange_symbol_only_through_reference_market() {
        let resolver = AccountInstrumentResolver::fixture(
            vec![kairos_reference_contract::ReferenceMarket {
                market_id: "market:binance:spot:BTCUSDT".into(),
                instrument_id: "instrument:spot:BTC".into(),
                listing_id: Some("listing:binance:spot:BTCUSDT".into()),
                exchange_id: "exchange:binance".into(),
                instrument_kind: "spot".into(),
                venue_symbol: Some("BTCUSDT".into()),
                status: "active".into(),
                asset_type: None,
                base_asset_id: None,
                quote_asset_id: None,
                underlying_instrument_id: None,
                price_tick: None,
                quantity_tick: None,
                minimum_quantity: None,
                minimum_notional: None,
                price_precision: 0,
                quantity_precision: 0,
                contract_size: None,
                effective_from_unix_nanos: 0,
                effective_to_unix_nanos: None,
            }],
            Vec::new(),
        );
        let provider = ParticipantInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            Some(ParticipantInstrumentTypeRef::new("binance-spot").unwrap()),
            "BTCUSDT",
        )
        .unwrap();

        let (instrument, market) = resolver.resolve(&provider).unwrap();
        assert_eq!(instrument.as_str(), "instrument:spot:BTC");
        assert_eq!(
            market.as_ref().map(kairos_primitives::MarketId::as_str),
            Some("market:binance:spot:BTCUSDT")
        );
    }

    #[test]
    fn resolves_ibkr_equity_to_reference_instrument_without_fabricating_market() {
        let resolver = AccountInstrumentResolver::fixture(
            Vec::new(),
            vec![kairos_reference_contract::Instrument {
                instrument_id: "instrument:equity:US:AAPL:common".into(),
                symbol: "AAPL".into(),
                instrument_type: kairos_primitives::InstrumentKind::Equity,
                status: "active".into(),
                ..Default::default()
            }],
        );
        let provider = ParticipantInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Broker, "ibkr").unwrap(),
            Some(ParticipantInstrumentTypeRef::new("equity").unwrap()),
            "AAPL",
        )
        .unwrap();

        let (instrument, market) = resolver.resolve(&provider).unwrap();
        assert_eq!(instrument.as_str(), "instrument:equity:US:AAPL:common");
        assert!(market.is_none());
    }

    #[test]
    fn refuses_missing_or_ambiguous_reference_identity() {
        let resolver = AccountInstrumentResolver::default();
        let provider = ParticipantInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            Some(ParticipantInstrumentTypeRef::new("binance-spot").unwrap()),
            "BTCUSDT",
        )
        .unwrap();

        assert!(resolver.resolve(&provider).is_err());
    }
}
