//! Account-owned mapping of normalized Integration facts.
//!
//! Integration already defines the connection capabilities. Account keeps
//! concrete dependency holders here instead of mirroring those capabilities
//! with another public protocol hierarchy.

use std::collections::BTreeMap;
use std::path::Path;
use std::sync::{Arc, Mutex};

use kairos_conflux::{
    BinanceRestConfig, BinanceUserWebSocketConfig, ConnectionKey, EarnPosition, EarnPositionState,
    EarnProductFamily, ExternalAccountEvent, ExternalAccountIdentity, ExternalAccountModel,
    ExternalAccountSegment, ExternalAccountSnapshot, ExternalAccountStatus, ExternalBalance,
    ExternalDecimal, ExternalMarginMode, ExternalOpenOrder, ExternalOrderStatus, ExternalPosition,
    ExternalPositionMode, IbkrAccountQueryConfig, IbkrAccountStreamConfig, OkxPrivateRestConfig,
    OkxPrivateWebSocketConfig, ParticipantInstrumentRef,
};

use crate::domain::{
    AccountEvent, AccountModel, AccountObservedFill, AccountOrderObservation, AccountSegment,
    AccountSnapshot, AccountStatus, AssetId, Balance, EarnAccruedReward, EarnHolding,
    EarnHoldingLiquidity, EarnHoldingState, EarnHoldingsSnapshot, FillId, InstrumentId, MarginMode,
    Money, OpenOrder, Position, PositionMode, SegmentKey, SignedQuantity,
};

#[derive(Clone, Default)]
pub(crate) struct AccountInstrumentResolver {
    client: Option<Arc<kairos_reference_contract::ReferenceClient>>,
    cache: Arc<
        Mutex<BTreeMap<String, (InstrumentId, Option<kairos_primitives::reference::MarketId>)>>,
    >,
    cache_generation: Arc<Mutex<Option<kairos_primitives::time::Generation>>>,
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
                        events: kairos_transport::AeronEndpoint::from_parts(
                            None,
                            kairos_transport::DEFAULT_CHANNEL,
                            kairos_transport::stream_ids::REFERENCE_CHANGES,
                        )
                        .map_err(|error| error.to_string())?,
                    },
                ),
            )),
            ..Default::default()
        })
    }

    fn resolve(
        &self,
        provider: &ParticipantInstrumentRef,
    ) -> Result<(InstrumentId, Option<kairos_primitives::reference::MarketId>), String> {
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
        provider: &ParticipantInstrumentRef,
    ) -> Result<(InstrumentId, Option<kairos_primitives::reference::MarketId>), String> {
        let symbol = provider.source_symbol.as_str();
        let (markets, instruments) = self.identity_snapshot()?;
        if provider.participant.id.eq_ignore_ascii_case("ibkr") {
            let matches = instruments
                .iter()
                .filter(|value| {
                    value.symbol.eq_ignore_ascii_case(symbol)
                        && value.instrument_type
                            == kairos_primitives::reference::InstrumentKind::Equity
                        && matches!(value.status.as_str(), "active" | "trading")
                })
                .collect::<Vec<_>>();
            let [instrument] = matches.as_slice() else {
                return Err(identity_resolution_error(provider, matches.len()));
            };
            return Ok((instrument.instrument_id.clone(), None));
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
                    && matches!(
                        value.status,
                        kairos_primitives::reference::ReferenceStatus::Active
                            | kairos_primitives::reference::ReferenceStatus::Trading
                    )
                    && provider_domain_matches_market(domain, value.instrument_kind)
            })
            .collect::<Vec<_>>();
        let [market] = matches.as_slice() else {
            return Err(identity_resolution_error(provider, matches.len()));
        };
        Ok((market.instrument_id.clone(), Some(market.market_id.clone())))
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
            return Ok((snapshot.markets, snapshot.instruments));
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

fn provider_domain_matches_market(
    domain: &str,
    market_type: kairos_primitives::reference::InstrumentKind,
) -> bool {
    let domain = domain.to_ascii_lowercase();
    if domain.contains("spot") || domain.contains("margin") {
        return market_type == kairos_primitives::reference::InstrumentKind::Spot;
    }
    if domain.contains("option") {
        return market_type == kairos_primitives::reference::InstrumentKind::Option;
    }
    if domain.contains("future") || domain.contains("swap") {
        return matches!(
            market_type,
            kairos_primitives::reference::InstrumentKind::Future
                | kairos_primitives::reference::InstrumentKind::Perpetual
        );
    }
    true
}

fn identity_resolution_error(provider: &ParticipantInstrumentRef, matches: usize) -> String {
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

/// Account-owned heterogeneous holder for concrete Integration event sources.
/// It is a dispatch container, not another implementation of Integration's
/// provider capability trait.
pub(crate) enum AccountAsyncEventSource {
    BinanceSpot {
        segment_key: SegmentKey,
        parameters: BinanceUserWebSocketConfig,
    },
    BinanceUsdM {
        segment_key: SegmentKey,
        parameters: BinanceUserWebSocketConfig,
    },
    BinanceCoinM {
        segment_key: SegmentKey,
        parameters: BinanceUserWebSocketConfig,
    },
    BinanceOptions {
        segment_key: SegmentKey,
        parameters: BinanceUserWebSocketConfig,
    },
    BinanceMargin {
        segment_key: SegmentKey,
        parameters: BinanceUserWebSocketConfig,
    },
    Ibkr {
        segment_key: SegmentKey,
        parameters: IbkrAccountStreamConfig,
    },
    OkxTrading {
        segment_key: SegmentKey,
        parameters: OkxPrivateWebSocketConfig,
    },
}

/// Concrete async account-read capabilities selected by Account composition.
/// This enum is deliberately private: it keeps heterogeneous provider handles
/// without publishing a second Account-owned provider protocol.
pub(crate) enum AccountAsyncSnapshotConnection {
    BinanceSpot(BinanceRestConfig),
    BinanceFunding(BinanceRestConfig),
    BinanceMargin(BinanceRestConfig),
    BinanceUsdM(BinanceRestConfig),
    BinanceCoinM(BinanceRestConfig),
    BinanceOptions(BinanceRestConfig),
    Ibkr(IbkrAccountQueryConfig),
    OkxTrading(OkxPrivateRestConfig),
}

impl AccountAsyncSnapshotConnection {
    pub(crate) fn into_conflux(
        self,
        key: String,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> Result<(), String> {
        let key = ConnectionKey::new(key).map_err(|error| error.to_string())?;
        match self {
            Self::BinanceSpot(parameters) => connections.binance_spot_rest.create(key, parameters),
            Self::BinanceFunding(parameters) => {
                connections
                    .binance_funding_rest
                    .create(key.clone(), parameters.clone())
                    .map_err(|error| error.to_string())?;
                connections.binance_earn_rest.create(key, parameters)
            },
            Self::BinanceMargin(parameters) => {
                connections.binance_margin_rest.create(key, parameters)
            },
            Self::BinanceUsdM(parameters) => connections.binance_usdm_rest.create(key, parameters),
            Self::BinanceCoinM(parameters) => {
                connections.binance_coinm_rest.create(key, parameters)
            },
            Self::BinanceOptions(parameters) => {
                connections.binance_options_rest.create(key, parameters)
            },
            Self::Ibkr(parameters) => connections.ibkr_account_query.create(key, parameters),
            Self::OkxTrading(parameters) => connections.okx_private_rest.create(key, parameters),
        }
        .map_err(|error| error.to_string())
    }
}

impl AccountAsyncEventSource {
    pub(crate) fn into_conflux(
        self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> Result<(), String> {
        let key = ConnectionKey::new(self.segment_key().to_string())
            .map_err(|error| error.to_string())?;
        match self {
            Self::BinanceSpot { parameters, .. } => connections
                .binance_spot_user_websocket
                .create(key, parameters),
            Self::BinanceUsdM { parameters, .. } => connections
                .binance_usdm_user_websocket
                .create(key, parameters),
            Self::BinanceCoinM { parameters, .. } => connections
                .binance_coinm_user_websocket
                .create(key, parameters),
            Self::BinanceOptions { parameters, .. } => connections
                .binance_options_user_websocket
                .create(key, parameters),
            Self::BinanceMargin { parameters, .. } => connections
                .binance_margin_user_websocket
                .create(key, parameters),
            Self::Ibkr { parameters, .. } => {
                connections.ibkr_account_stream.create(key, parameters)
            },
            Self::OkxTrading { parameters, .. } => {
                connections.okx_private_websocket.create(key, parameters)
            },
        }
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
        identity: ExternalAccountIdentity {
            broker: segment.identity.broker.to_string(),
            account_id: segment.identity.account_id.clone(),
        },
        segment_key: kairos_primitives::account::SegmentKey::new(segment.segment_key.to_string())
            .expect("validated account segment key"),
        environment: segment.environment.clone(),
        account_model: segment.account_model.clone(),
    }
}

fn signed_quantity(value: ExternalDecimal) -> Result<SignedQuantity, String> {
    SignedQuantity::new(value.mantissa, value.scale).map_err(Into::into)
}

fn quantity(value: ExternalDecimal) -> Result<kairos_primitives::decimal::Quantity, String> {
    kairos_primitives::decimal::Quantity::new(value.mantissa, value.scale)
        .map_err(|error| error.to_string())
}

fn price(value: ExternalDecimal) -> Result<kairos_primitives::decimal::Price, String> {
    kairos_primitives::decimal::Price::new(value.mantissa, value.scale)
        .map_err(|error| error.to_string())
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
    value: ExternalPosition,
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
    value: ExternalAccountSnapshot,
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

pub(crate) fn map_earn_positions(
    segment_key: SegmentKey,
    positions: Vec<EarnPosition>,
    observed_at_unix_nanos: kairos_primitives::time::UnixNanos,
    complete: bool,
) -> EarnHoldingsSnapshot {
    EarnHoldingsSnapshot {
        segment_key,
        holdings: positions
            .into_iter()
            .map(|value| EarnHolding {
                participant_position_id: value.participant_position_id,
                product_id: value.product_id,
                asset: value.asset,
                principal: value.principal,
                redeemable: value.redeemable_amount,
                accrued_rewards: value
                    .accrued_rewards
                    .into_iter()
                    .map(|reward| EarnAccruedReward {
                        asset: reward.asset,
                        amount: reward.amount,
                    })
                    .collect(),
                liquidity: match value.family {
                    EarnProductFamily::Flexible => EarnHoldingLiquidity::Immediate,
                    EarnProductFamily::Locked => value.matures_at_unix_nanos.map_or(
                        EarnHoldingLiquidity::Unknown,
                        |matures_at_unix_nanos| EarnHoldingLiquidity::FixedTerm {
                            matures_at_unix_nanos,
                        },
                    ),
                    EarnProductFamily::Staking
                    | EarnProductFamily::YieldBearingAsset
                    | EarnProductFamily::Other(_) => EarnHoldingLiquidity::Unknown,
                },
                state: match value.state {
                    EarnPositionState::Active => EarnHoldingState::Active,
                    EarnPositionState::Redeeming => EarnHoldingState::Redeeming,
                    EarnPositionState::Redeemed => EarnHoldingState::Redeemed,
                    EarnPositionState::Unknown(value) => EarnHoldingState::Unknown(value),
                },
                observed_at_unix_nanos: value
                    .observed_at_unix_nanos
                    .unwrap_or(observed_at_unix_nanos),
            })
            .collect(),
        observed_at_unix_nanos,
        complete,
    }
}

fn map_open_order(
    value: ExternalOpenOrder,
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
        },
        ExternalAccountEvent::Order(value) => {
            let (status, active) = map_order_status(value.status);
            AccountEvent::OrderObserved(AccountOrderObservation {
                order_id: value.order_id,
                status: match status {
                    "acknowledged" => kairos_primitives::integration::OrderStatus::Acknowledged,
                    "partially_filled" => {
                        kairos_primitives::integration::OrderStatus::PartiallyFilled
                    },
                    "filled" => kairos_primitives::integration::OrderStatus::Filled,
                    "canceled" => kairos_primitives::integration::OrderStatus::Canceled,
                    "rejected" => kairos_primitives::integration::OrderStatus::Rejected,
                    "expired" => kairos_primitives::integration::OrderStatus::Expired,
                    _ => kairos_primitives::integration::OrderStatus::Unknown,
                },
                active,
                remote_order_id: value.remote_order_id,
                filled_quantity: value.filled_quantity.map(quantity).transpose()?,
                observed_at_unix_nanos: value.occurred_at_unix_nanos,
            })
        },
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
        },
    })
}

#[cfg(test)]
mod identity_tests {
    use kairos_conflux::{
        ParticipantInstrumentRef, ParticipantInstrumentTypeRef, ParticipantKind, ParticipantRef,
    };

    use super::{AccountInstrumentResolver, map_earn_positions};

    #[test]
    fn resolves_exchange_symbol_only_through_reference_market() {
        let resolver = AccountInstrumentResolver::fixture(
            vec![kairos_reference_contract::ReferenceMarket {
                market_id: kairos_primitives::reference::MarketId::new(
                    "market:binance:spot:BTCUSDT",
                )
                .unwrap(),
                instrument_id: kairos_primitives::reference::InstrumentId::new(
                    "instrument:spot:BTC",
                )
                .unwrap(),
                listing_id: Some(
                    kairos_primitives::reference::ListingId::new("listing:binance:spot:BTCUSDT")
                        .unwrap(),
                ),
                exchange_id: kairos_primitives::reference::Exchange::new("exchange:binance")
                    .unwrap(),
                instrument_kind: kairos_primitives::reference::InstrumentKind::Spot,
                venue_symbol: Some(kairos_primitives::reference::Symbol::new("BTCUSDT").unwrap()),
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
                effective_from_unix_nanos: 0.into(),
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
            market
                .as_ref()
                .map(kairos_primitives::reference::MarketId::as_str),
            Some("market:binance:spot:BTCUSDT")
        );
    }

    #[test]
    fn resolves_ibkr_equity_to_reference_instrument_without_fabricating_market() {
        let resolver = AccountInstrumentResolver::fixture(
            Vec::new(),
            vec![kairos_reference_contract::Instrument {
                instrument_id: kairos_primitives::reference::InstrumentId::new(
                    "instrument:equity:US:AAPL:common",
                )
                .unwrap(),
                symbol: kairos_primitives::reference::Symbol::new("AAPL").unwrap(),
                instrument_type: kairos_primitives::reference::InstrumentKind::Equity,
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

    #[test]
    fn maps_earn_positions_into_a_separate_account_fact_set() {
        let observed_at = kairos_primitives::time::UnixNanos::new(100);
        let snapshot = map_earn_positions(
            kairos_primitives::account::SegmentKey::new("funding").unwrap(),
            vec![kairos_conflux::EarnPosition {
                participant_position_id: Some("position-1".into()),
                product_id: "USDT001".into(),
                asset: kairos_primitives::reference::Currency::new("USDT").unwrap(),
                family: kairos_conflux::EarnProductFamily::Flexible,
                principal: kairos_primitives::decimal::Quantity::new(100, 0).unwrap(),
                accrued_rewards: Vec::new(),
                redeemable_amount: Some(kairos_primitives::decimal::Quantity::new(80, 0).unwrap()),
                subscribed_at_unix_nanos: None,
                matures_at_unix_nanos: None,
                state: kairos_conflux::EarnPositionState::Redeeming,
                observed_at_unix_nanos: Some(observed_at),
            }],
            observed_at,
            true,
        );
        assert_eq!(snapshot.holdings.len(), 1);
        assert_eq!(snapshot.holdings[0].product_id, "USDT001");
        assert_eq!(
            snapshot.holdings[0].state,
            crate::domain::EarnHoldingState::Redeeming
        );
    }
}
