//! Account-owned mapping of normalized Integration facts.
//!
//! Integration already defines the connection capabilities. Account keeps
//! concrete dependency holders here instead of mirroring those capabilities
//! with another public protocol hierarchy.

use std::collections::BTreeMap;
use std::sync::Arc;

use crate::application::{AccountMarketProfile, AccountMarketProfileRequest};
use crate::domain::{
    AccountEvent, AccountModel, AccountObservedFill, AccountOrderObservation, AccountSegment,
    AccountSnapshot, AccountStatus, AssetId, Balance, FillId, InstrumentId, MarginMode, Money,
    OpenOrder, Position, PositionMode, SegmentKey, SignedQuantity,
};
use kairos_integration::application::{
    AsyncAccountEventSource, AsyncAccountMarketProfileConnection, AsyncAccountReadConnection,
    ConnectionDescriptor, ExternalMarketProfile, ExternalMarketProfileRequest, IntegrationError,
};
use kairos_integration::blocking::{
    AccountMarketProfileConnection, AccountReadConnection, BufferedIntegrationAccountStream,
};

/// Account-owned heterogeneous holder for concrete Integration event sources.
/// It is a dispatch container, not another implementation of Integration's
/// provider capability trait.
pub(crate) enum AccountAsyncEventSource {
    BinanceSpot(kairos_integration::participants::binance::BinanceSpotAccountEvents),
    OkxTrading(kairos_integration::participants::okx::OkxTradingAccountEvents),
}

impl AccountAsyncEventSource {
    pub(crate) async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        match self {
            Self::BinanceSpot(source) => source.disconnect_channel().await,
            Self::OkxTrading(source) => source.disconnect_channel().await,
        }
    }

    pub(crate) async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        match self {
            Self::BinanceSpot(source) => source.reconnect_channel().await,
            Self::OkxTrading(source) => source.reconnect_channel().await,
        }
    }

    pub(crate) async fn next_account_event(
        &mut self,
    ) -> Result<ExternalAccountEvent, IntegrationError> {
        match self {
            Self::BinanceSpot(source) => source.next_account_event().await,
            Self::OkxTrading(source) => source.next_account_event().await,
        }
    }
}
use kairos_integration::application::{
    ExternalAccountEvent, ExternalAccountModel, ExternalAccountSegment, ExternalAccountStatus,
    ExternalBalance, ExternalDecimal, ExternalMarginMode, ExternalOrderStatus,
    ExternalPositionMode,
};

pub(crate) enum AccountSnapshotGateway {
    Memory(BTreeMap<String, AccountSnapshot>),
    Integration(BTreeMap<String, Box<dyn AccountReadConnection + Send>>),
}

struct AsyncAccountReadRequest {
    segment: ExternalAccountSegment,
    reply: std::sync::mpsc::SyncSender<
        Result<
            kairos_integration::application::capabilities::account_facts::ExternalAccountSnapshot,
            IntegrationError,
        >,
    >,
}

/// Transitional adapter between Account's synchronous refresh worker and an
/// Integration async capability. The network Future stays on Account's Tokio
/// runtime; only the dedicated refresh thread waits synchronously.
pub(crate) struct AsyncAccountReadProxy {
    sender: tokio::sync::mpsc::Sender<AsyncAccountReadRequest>,
}

pub(crate) fn async_account_read_channel<C>(
    descriptor: ConnectionDescriptor,
    connection: C,
) -> Result<(AsyncAccountReadProxy, tokio::task::JoinHandle<()>), String>
where
    C: AsyncAccountReadConnection + 'static,
{
    descriptor.validate()?;
    let (sender, mut receiver) = tokio::sync::mpsc::channel::<AsyncAccountReadRequest>(2);
    let worker = tokio::spawn(async move {
        let mut connection = connection;
        while let Some(request) = receiver.recv().await {
            let result = connection.fetch_account(&request.segment).await;
            let _ = request.reply.send(result);
        }
    });
    Ok((AsyncAccountReadProxy { sender }, worker))
}

impl AccountReadConnection for AsyncAccountReadProxy {
    fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<
        kairos_integration::application::capabilities::account_facts::ExternalAccountSnapshot,
        IntegrationError,
    > {
        if tokio::runtime::Handle::try_current().is_ok() {
            return Err(IntegrationError::InvalidRequest(
                "Account async-read proxy must be called by its dedicated refresh worker".into(),
            ));
        }
        let (reply, receiver) = std::sync::mpsc::sync_channel(1);
        self.sender
            .blocking_send(AsyncAccountReadRequest {
                segment: segment.clone(),
                reply,
            })
            .map_err(|_| {
                IntegrationError::Unavailable("Account async-read worker is stopped".into())
            })?;
        receiver.recv().map_err(|_| {
            IntegrationError::Unavailable("Account async-read worker did not respond".into())
        })?
    }
}

struct AsyncAccountMarketProfileRequest {
    request: ExternalMarketProfileRequest,
    reply: std::sync::mpsc::SyncSender<Result<ExternalMarketProfile, IntegrationError>>,
}

pub(crate) struct AsyncAccountMarketProfileProxy {
    sender: tokio::sync::mpsc::Sender<AsyncAccountMarketProfileRequest>,
}

pub(crate) fn async_account_market_profile_channel<C>(
    descriptor: ConnectionDescriptor,
    connection: C,
) -> Result<(AsyncAccountMarketProfileProxy, tokio::task::JoinHandle<()>), String>
where
    C: AsyncAccountMarketProfileConnection + 'static,
{
    descriptor.validate()?;
    let (sender, mut receiver) = tokio::sync::mpsc::channel::<AsyncAccountMarketProfileRequest>(2);
    let worker = tokio::spawn(async move {
        let mut connection = connection;
        while let Some(request) = receiver.recv().await {
            let result = connection.fetch_market_profile(&request.request).await;
            let _ = request.reply.send(result);
        }
    });
    Ok((AsyncAccountMarketProfileProxy { sender }, worker))
}

impl AccountMarketProfileConnection for AsyncAccountMarketProfileProxy {
    fn fetch_market_profile(
        &mut self,
        request: &ExternalMarketProfileRequest,
    ) -> Result<ExternalMarketProfile, IntegrationError> {
        if tokio::runtime::Handle::try_current().is_ok() {
            return Err(IntegrationError::InvalidRequest(
                "Account async-market-profile proxy must be called by its dedicated refresh worker"
                    .into(),
            ));
        }
        let (reply, receiver) = std::sync::mpsc::sync_channel(1);
        self.sender
            .blocking_send(AsyncAccountMarketProfileRequest {
                request: request.clone(),
                reply,
            })
            .map_err(|_| {
                IntegrationError::Unavailable(
                    "Account async-market-profile worker is stopped".into(),
                )
            })?;
        receiver.recv().map_err(|_| {
            IntegrationError::Unavailable(
                "Account async-market-profile worker did not respond".into(),
            )
        })?
    }
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
            .get_mut(request.segment_key.as_str())
            .ok_or_else(|| format!("account segment is not configured: {}", request.segment_key))?;
        connection
            .fetch_market_profile(&ExternalMarketProfileRequest {
                account_id: request.account_id.clone(),
                segment_key: kairos_domain_types::SegmentKey::new(request.segment_key.to_string())
                    .map_err(|error| error.to_string())?,
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

    pub(crate) fn register_wakeup(&mut self, wakeup: Arc<tokio::sync::Notify>) {
        self.stream.register_wakeup(wakeup);
    }
}

fn external_segment(segment: &AccountSegment) -> ExternalAccountSegment {
    ExternalAccountSegment {
        identity:
            kairos_integration::application::capabilities::account_facts::ExternalAccountIdentity {
                broker: segment.identity.broker.clone(),
                account_id: segment.identity.account_id.clone(),
            },
        segment_key: kairos_domain_types::SegmentKey::new(segment.segment_key.to_string())
            .expect("validated account segment key"),
        environment: segment.environment.clone(),
        account_model: segment.account_model.clone(),
    }
}

fn signed_quantity(value: ExternalDecimal) -> SignedQuantity {
    SignedQuantity::new(value.mantissa, value.scale)
}

fn quantity(value: ExternalDecimal) -> Result<kairos_domain_types::Quantity, String> {
    kairos_domain_types::Quantity::new(value.mantissa, value.scale)
        .map_err(|error| error.to_string())
}

fn price(value: ExternalDecimal) -> Result<kairos_domain_types::Price, String> {
    kairos_domain_types::Price::new(value.mantissa, value.scale).map_err(|error| error.to_string())
}

fn money(value: ExternalDecimal) -> Money {
    Money::new(value.mantissa, value.scale)
}

fn rate(value: ExternalDecimal) -> kairos_domain_types::Rate {
    kairos_domain_types::Rate::new(value.mantissa, value.scale)
}

fn map_balance(value: ExternalBalance) -> Result<Balance, String> {
    Ok(Balance {
        asset_id: AssetId::new(value.asset_id.to_string()).expect("validated asset id"),
        asset_code: value.asset_code,
        total: signed_quantity(value.total),
        available: value.available.map(signed_quantity),
        locked: value.locked.map(signed_quantity),
        borrowed: value.borrowed.map(signed_quantity),
        interest: value.interest.map(signed_quantity),
    })
}

fn map_position(
    value: kairos_integration::application::ExternalPosition,
) -> Result<Position, String> {
    Ok(Position {
        instrument_id: InstrumentId::new(value.instrument_id.to_string())
            .map_err(|error| error.to_string())?,
        market_id: value.market_id,
        quantity: signed_quantity(value.quantity),
        average_price: value.average_price.map(price).transpose()?,
        mark_price: value.mark_price.map(price).transpose()?,
        unrealized_pnl: value.unrealized_pnl.map(money),
        realized_pnl: value.realized_pnl.map(money),
        updated_at_unix_nanos: value.updated_at_unix_nanos,
    })
}

fn map_snapshot(
    value: kairos_integration::application::ExternalAccountSnapshot,
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
            .map(map_position)
            .collect::<Result<_, _>>()?,
        open_orders: value
            .open_orders
            .into_iter()
            .map(map_open_order)
            .collect::<Result<_, _>>()?,
        status: map_status(value.status),
        observed_at_unix_nanos: value.observed_at_unix_nanos,
        equity: value.equity.map(money),
        initial_equity: value.initial_equity.map(money),
        net_profit: value.net_profit.map(money),
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
    value: kairos_integration::application::ExternalOpenOrder,
) -> Result<OpenOrder, String> {
    Ok(OpenOrder {
        order_id: value.order_id,
        remote_order_id: value.remote_order_id,
        instrument_id: InstrumentId::new(value.instrument_id.to_string())
            .expect("provider instrument id"),
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

pub(crate) fn map_event(value: ExternalAccountEvent) -> Result<AccountEvent, String> {
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
                status: match status {
                    "acknowledged" => kairos_domain_types::OrderStatus::Acknowledged,
                    "partially_filled" => kairos_domain_types::OrderStatus::PartiallyFilled,
                    "filled" => kairos_domain_types::OrderStatus::Filled,
                    "canceled" => kairos_domain_types::OrderStatus::Canceled,
                    "rejected" => kairos_domain_types::OrderStatus::Rejected,
                    "expired" => kairos_domain_types::OrderStatus::Expired,
                    _ => kairos_domain_types::OrderStatus::Unknown,
                },
                active,
                remote_order_id: value.remote_order_id,
                filled_quantity: value.filled_quantity.map(quantity).transpose()?,
                observed_at_unix_nanos: value.occurred_at_unix_nanos,
            })
        }
        ExternalAccountEvent::Fill(value) => AccountEvent::ObservedFill(AccountObservedFill {
            fill_id: FillId::new(value.fill_id.to_string()).expect("validated fill id"),
            order_id: Some(value.order_id),
            remote_order_id: None,
            segment_key: SegmentKey::new(value.segment_key.to_string())
                .expect("validated account segment key"),
            instrument_id: InstrumentId::new(value.instrument_id.to_string())
                .map_err(|error| error.to_string())?,
            quantity: quantity(value.quantity)?,
            price: price(value.price)?,
            side: if value.side.eq_ignore_ascii_case("sell") {
                crate::domain::FillSide::Sell
            } else {
                crate::domain::FillSide::Buy
            },
            occurred_at_unix_nanos: value.occurred_at_unix_nanos,
        }),
    })
}

fn map_profile(
    value: kairos_integration::application::ExternalMarketProfile,
) -> Result<AccountMarketProfile, String> {
    Ok(AccountMarketProfile {
        account_id: value.account_id,
        segment_key: SegmentKey::new(value.segment_key.to_string())
            .map_err(|error| error.to_string())?,
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
        maker_fee: value.maker_fee.map(rate),
        taker_fee: value.taker_fee.map(rate),
        fee_currency: value.fee_currency,
        fee_discount: value.fee_discount.map(rate),
        fee_tier: value.fee_tier,
        source: value.source,
        observed_at_unix_nanos: value.observed_at_unix_nanos,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use kairos_integration::application::capabilities::account_facts::{
        ExternalAccountIdentity, ExternalAccountSnapshot, ExternalAccountStatus,
    };
    use kairos_integration::application::AsyncAccountReadConnection;
    use std::sync::atomic::{AtomicBool, Ordering};

    struct RuntimeCheckingRead(std::sync::Arc<AtomicBool>);

    impl AsyncAccountReadConnection for RuntimeCheckingRead {
        async fn fetch_account(
            &mut self,
            segment: &ExternalAccountSegment,
        ) -> Result<ExternalAccountSnapshot, IntegrationError> {
            self.0.store(
                tokio::runtime::Handle::try_current().is_ok(),
                Ordering::Release,
            );
            Ok(ExternalAccountSnapshot {
                segment_key: segment.segment_key.clone(),
                balances: Vec::new(),
                collateral: Vec::new(),
                positions: Vec::new(),
                open_orders: Vec::new(),
                status: ExternalAccountStatus::Ready,
                observed_at_unix_nanos: 1.into(),
                equity: None,
                initial_equity: None,
                net_profit: None,
                account_model: None,
                margin_mode: None,
                position_mode: None,
                partial: false,
            })
        }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn refresh_thread_proxy_polls_provider_future_on_account_runtime() {
        let used_runtime = std::sync::Arc::new(AtomicBool::new(false));
        let (mut proxy, worker) = async_account_read_channel(
            ConnectionDescriptor {
                binding_id: "account.binance.spot.test".into(),
                participant: kairos_integration::application::ParticipantRef::new(
                    kairos_integration::application::ParticipantKind::Exchange,
                    "binance",
                )
                .unwrap(),
                environment: "test".into(),
                principal_id: Some("main".into()),
                domain: kairos_integration::application::ConnectionDomainRef::new("spot").unwrap(),
            },
            RuntimeCheckingRead(std::sync::Arc::clone(&used_runtime)),
        )
        .unwrap();
        let segment = ExternalAccountSegment {
            identity: ExternalAccountIdentity::new("binance", "main").unwrap(),
            segment_key: kairos_domain_types::SegmentKey::new("spot").unwrap(),
            environment: "test".into(),
            account_model: Some("no_margin".into()),
        };
        let (result_sender, result_receiver) = tokio::sync::oneshot::channel();
        std::thread::spawn(move || {
            let _ = result_sender.send(proxy.fetch_account(&segment));
        });
        let result = result_receiver.await.unwrap().unwrap();
        assert_eq!(result.segment_key.as_str(), "spot");
        assert!(used_runtime.load(Ordering::Acquire));
        worker.await.unwrap();
    }
}
