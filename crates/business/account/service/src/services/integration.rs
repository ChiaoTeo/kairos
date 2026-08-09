//! Account-owned mapping of normalized Integration facts.
//!
//! Integration already defines the connection capabilities. Account keeps
//! concrete dependency holders here instead of mirroring those capabilities
//! with another public protocol hierarchy.

use std::collections::BTreeMap;

use crate::application::{AccountMarketProfile, AccountMarketProfileRequest};
use crate::domain::{
    AccountEvent, AccountFill, AccountModel, AccountOrderObservation, AccountSegment,
    AccountSnapshot, AccountStatus, AssetId, Balance, Decimal, FillId, InstrumentId, MarginMode,
    OpenOrder, Position, PositionMode, SegmentKey,
};
use kairos_integration::application::{
    AccountMarketProfileConnection, AccountReadConnection, BufferedIntegrationAccountStream,
    ExternalMarketProfileRequest,
};
use kairos_integration::domain::{
    ExternalAccountEvent, ExternalAccountModel, ExternalAccountSegment, ExternalAccountStatus,
    ExternalBalance, ExternalDecimal, ExternalMarginMode, ExternalOrderStatus,
    ExternalPositionMode,
};

pub(crate) enum AccountSnapshotGateway {
    Memory(BTreeMap<String, AccountSnapshot>),
    Integration(BTreeMap<String, Box<dyn AccountReadConnection + Send>>),
}

impl AccountSnapshotGateway {
    pub(crate) fn memory(snapshots: BTreeMap<String, AccountSnapshot>) -> Self {
        Self::Memory(snapshots)
    }

    pub(crate) fn integration(
        connections: BTreeMap<String, Box<dyn AccountReadConnection + Send>>,
    ) -> Self {
        Self::Integration(connections)
    }

    pub(crate) fn split(self) -> BTreeMap<String, Self> {
        match self {
            Self::Memory(snapshots) => snapshots
                .into_iter()
                .map(|(key, snapshot)| {
                    (key.clone(), Self::Memory(BTreeMap::from([(key, snapshot)])))
                })
                .collect(),
            Self::Integration(connections) => connections
                .into_iter()
                .map(|(key, connection)| {
                    (
                        key.clone(),
                        Self::Integration(BTreeMap::from([(key, connection)])),
                    )
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
            Self::Integration(connections) => {
                let connection = connections
                    .get_mut(segment.segment_key.as_str())
                    .ok_or_else(|| {
                        format!("account segment is not configured: {}", segment.segment_key)
                    })?;
                connection
                    .fetch_account(&external_segment(segment))
                    .map_err(|error| error.to_string())
                    .and_then(map_snapshot)
            }
        }
    }
}

pub(crate) struct AccountMarketProfileGateway {
    connections: BTreeMap<String, Box<dyn AccountMarketProfileConnection + Send>>,
}

impl AccountMarketProfileGateway {
    pub(crate) fn new(
        connections: BTreeMap<String, Box<dyn AccountMarketProfileConnection + Send>>,
    ) -> Self {
        Self { connections }
    }

    pub(crate) fn fetch(
        &mut self,
        request: &AccountMarketProfileRequest,
    ) -> Result<AccountMarketProfile, String> {
        let connection = self
            .connections
            .get_mut(&request.segment_key)
            .ok_or_else(|| format!("account segment is not configured: {}", request.segment_key))?;
        connection
            .fetch_market_profile(&ExternalMarketProfileRequest {
                account_id: request.account_id.clone(),
                segment_key: request.segment_key.clone(),
                market_id: request.market_id.clone(),
                source_symbol: request.source_symbol.clone(),
            })
            .map_err(|error| error.to_string())
            .and_then(map_profile)
    }
}

pub(crate) struct AccountEventStream {
    stream: BufferedIntegrationAccountStream,
}

impl AccountEventStream {
    pub(crate) fn new(stream: BufferedIntegrationAccountStream) -> Self {
        Self { stream }
    }

    pub(crate) fn next_event(&mut self) -> Result<Option<AccountEvent>, String> {
        self.stream.next_event()?.map(map_event).transpose()
    }

    pub(crate) fn pending_events(&self) -> usize {
        self.stream.pending_events()
    }
}

fn external_segment(segment: &AccountSegment) -> ExternalAccountSegment {
    ExternalAccountSegment {
        identity: kairos_integration::domain::account::ExternalAccountIdentity {
            broker: segment.identity.broker.clone(),
            account_id: segment.identity.account_id.to_string(),
        },
        segment_key: segment.segment_key.to_string(),
        environment: segment.environment.clone(),
        account_model: segment.account_model.clone(),
    }
}

fn decimal(value: ExternalDecimal) -> Decimal {
    Decimal::new(value.mantissa, value.scale)
}

fn map_balance(value: ExternalBalance) -> Result<Balance, String> {
    Ok(Balance {
        asset_id: AssetId::new(value.asset_id).map_err(|error| error.to_string())?,
        asset_code: value.asset_code,
        total: decimal(value.total),
        available: value.available.map(decimal),
        locked: value.locked.map(decimal),
        borrowed: value.borrowed.map(decimal),
        interest: value.interest.map(decimal),
    })
}

fn map_position(value: kairos_integration::domain::ExternalPosition) -> Result<Position, String> {
    Ok(Position {
        instrument_id: InstrumentId::new(value.instrument_id).map_err(|error| error.to_string())?,
        market_id: value.market_id,
        quantity: decimal(value.quantity),
        average_price: value.average_price.map(decimal),
        mark_price: value.mark_price.map(decimal),
        unrealized_pnl: value.unrealized_pnl.map(decimal),
        realized_pnl: value.realized_pnl.map(decimal),
        updated_at_unix_nanos: value.updated_at_unix_nanos,
    })
}

fn map_snapshot(
    value: kairos_integration::domain::ExternalAccountSnapshot,
) -> Result<AccountSnapshot, String> {
    Ok(AccountSnapshot {
        segment_key: SegmentKey::new(value.segment_key).map_err(|error| error.to_string())?,
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
            .map(map_position)
            .collect::<Result<_, _>>()?,
        open_orders: value.open_orders.into_iter().map(map_open_order).collect(),
        status: map_status(value.status),
        observed_at_unix_nanos: value.observed_at_unix_nanos,
        equity: value.equity.map(decimal),
        initial_equity: value.initial_equity.map(decimal),
        net_profit: value.net_profit.map(decimal),
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

fn map_open_order(value: kairos_integration::domain::ExternalOpenOrder) -> OpenOrder {
    OpenOrder {
        order_id: value.order_id,
        venue_order_id: value.venue_order_id,
        instrument_id: value.instrument_id,
        side: value.side,
        quantity: decimal(value.quantity),
        filled_quantity: decimal(value.filled_quantity),
        status: value.status,
    }
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

fn map_event(value: ExternalAccountEvent) -> Result<AccountEvent, String> {
    Ok(match value {
        ExternalAccountEvent::Batch(values) => AccountEvent::Batch(
            values
                .into_iter()
                .map(map_event)
                .collect::<Result<_, _>>()?,
        ),
        ExternalAccountEvent::Snapshot(value) => AccountEvent::Snapshot(map_snapshot(value)?),
        ExternalAccountEvent::Order(value) => {
            let (status, active) = map_order_status(value.status);
            AccountEvent::OrderObserved(AccountOrderObservation {
                order_id: value.order_id,
                status: status.into(),
                active,
                venue_order_id: value.venue_order_id,
                filled_quantity: value.filled_quantity.map(decimal),
                observed_at_unix_nanos: value.occurred_at_unix_nanos,
            })
        }
        ExternalAccountEvent::Fill(value) => AccountEvent::Fill(AccountFill {
            fill_id: FillId::new(value.fill_id).map_err(|error| error.to_string())?,
            order_id: Some(value.order_id),
            segment_key: SegmentKey::new(value.segment_key).map_err(|error| error.to_string())?,
            instrument_id: InstrumentId::new(value.instrument_id)
                .map_err(|error| error.to_string())?,
            quantity: decimal(value.quantity),
            price: decimal(value.price),
            side: if value.side.eq_ignore_ascii_case("sell") {
                crate::domain::FillSide::Sell
            } else {
                crate::domain::FillSide::Buy
            },
            settlement_asset: None,
            settlement_delta: None,
            fee_asset: value.fee_asset,
            fee_amount: value.fee_amount.map(decimal),
            occurred_at_unix_nanos: value.occurred_at_unix_nanos,
        }),
    })
}

fn map_profile(
    value: kairos_integration::application::ExternalMarketProfile,
) -> Result<AccountMarketProfile, String> {
    Ok(AccountMarketProfile {
        account_id: crate::domain::AccountId::new(value.account_id)
            .map_err(|error| error.to_string())?,
        segment_key: SegmentKey::new(value.segment_key).map_err(|error| error.to_string())?,
        market_id: value.market_id,
        account_model: value.account_model.map(map_model),
        margin_mode: value.margin_mode.as_deref().and_then(|mode| match mode {
            "cross" => Some(MarginMode::Cross),
            "isolated" => Some(MarginMode::Isolated),
            _ => None,
        }),
        position_mode: value.position_mode.as_deref().and_then(|mode| match mode {
            "one_way" => Some(PositionMode::OneWay),
            "hedge" => Some(PositionMode::Hedge),
            _ => None,
        }),
        maker_fee: value.maker_fee.map(decimal),
        taker_fee: value.taker_fee.map(decimal),
        fee_currency: value.fee_currency,
        fee_discount: value.fee_discount.map(decimal),
        fee_tier: value.fee_tier,
        source: value.source,
        observed_at_unix_nanos: value.observed_at_unix_nanos,
    })
}
