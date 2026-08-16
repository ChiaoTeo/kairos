//! Interactive Brokers TWS/IB Gateway connections.
//!
//! The `ibapi` client is contained in this integration adapter.  Only
//! normalized connection facts cross into the rest of the workspace.

pub(crate) mod async_account;
pub(crate) mod async_execution;

use std::collections::BTreeMap;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::application::capabilities::account_facts::{
    external_instrument_ref, ExternalAccountSegment, ExternalAccountSnapshot,
    ExternalAccountStatus, ExternalBalance, ExternalDecimal, ExternalOpenOrder, ExternalPosition,
};
use crate::application::{
    AccountEventReceive, AccountEventStreamConnection, AccountReadConnection, IntegrationError,
};
use ibapi::accounts::types::AccountId;
use ibapi::accounts::AccountUpdate;
use ibapi::client::blocking::Client;
use ibapi::orders::OrderStatusKind;
use ibapi::orders::Orders;
pub(super) fn normalize_ibkr_order_status(
    status: OrderStatusKind,
    filled: Option<f64>,
    remaining: Option<f64>,
) -> kairos_primitives::OrderStatus {
    if filled.is_some_and(|value| value > 0.0) && remaining.is_some_and(|value| value > 0.0) {
        return kairos_primitives::OrderStatus::PartiallyFilled;
    }
    match status {
        OrderStatusKind::ApiPending
        | OrderStatusKind::PendingSubmit
        | OrderStatusKind::PreSubmitted => kairos_primitives::OrderStatus::Acknowledged,
        OrderStatusKind::PendingCancel | OrderStatusKind::Submitted => {
            kairos_primitives::OrderStatus::Accepted
        }
        OrderStatusKind::ApiCancelled | OrderStatusKind::Cancelled => {
            kairos_primitives::OrderStatus::Canceled
        }
        OrderStatusKind::Filled => kairos_primitives::OrderStatus::Filled,
        OrderStatusKind::Inactive => kairos_primitives::OrderStatus::Unknown,
    }
}
pub struct IbkrAccountConnection {
    options: IbkrOptions,
}
pub struct IbkrAccountStreamConnection {
    state: crate::domain::ConnectionState,
    options: IbkrOptions,
    account_id: String,
    segment_key: String,
    next_snapshot_poll: Instant,
}
#[derive(Clone, Debug)]
pub struct IbkrOptions {
    pub host: String,
    pub port: u16,
    pub client_id: i32,
}

impl IbkrOptions {
    pub fn new(host: impl Into<String>, port: u16, client_id: i32) -> Result<Self, String> {
        let host = host.into();
        if host.trim().is_empty() || port == 0 {
            return Err("IBKR host and port are required".into());
        }
        Ok(Self {
            host,
            port,
            client_id,
        })
    }
    fn address(&self) -> String {
        format!("{}:{}", self.host, self.port)
    }
    fn connect(&self) -> Result<Client, String> {
        Client::connect(&self.address(), self.client_id).map_err(|e| e.to_string())
    }
}

fn connection_state(
    binding_id: &str,
    domain: &str,
) -> Result<crate::domain::ConnectionState, String> {
    Ok(crate::domain::ConnectionState::new(
        crate::domain::ConnectionDescriptor::new(
            binding_id,
            crate::domain::ParticipantRef::new(crate::domain::ParticipantKind::Broker, "ibkr")?,
            domain,
        )?,
    ))
}

impl IbkrAccountConnection {
    pub fn new(options: IbkrOptions) -> Result<Self, String> {
        Ok(Self { options })
    }
}

impl IbkrAccountStreamConnection {
    pub fn new(
        options: IbkrOptions,
        account_id: impl Into<String>,
        segment_key: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            state: connection_state("account.ibkr.equity.stream", "account")?,
            options,
            account_id: account_id.into(),
            segment_key: segment_key.into(),
            next_snapshot_poll: Instant::now(),
        })
    }
}

impl AccountReadConnection for IbkrAccountConnection {
    fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, IntegrationError> {
        fetch_snapshot(&self.options, segment).map_err(IntegrationError::InvalidPayload)
    }
}

impl AccountEventStreamConnection for IbkrAccountStreamConnection {
    fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.state.mark_ready(true);
        Ok(())
    }

    fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.state.mark_stopped();
        Ok(())
    }

    fn channel_health(&self) -> crate::domain::ConnectionHealth {
        self.state.health()
    }

    fn recv_account_event(
        &mut self,
        timeout: Duration,
    ) -> Result<AccountEventReceive, IntegrationError> {
        self.connect_channel()?;
        let now = Instant::now();
        if now < self.next_snapshot_poll {
            std::thread::park_timeout(timeout.min(self.next_snapshot_poll - now));
            if Instant::now() < self.next_snapshot_poll {
                return Ok(AccountEventReceive::Idle);
            }
        }
        self.next_snapshot_poll = Instant::now() + Duration::from_secs(1);
        let segment = ExternalAccountSegment {
            identity:
                crate::application::capabilities::account_facts::ExternalAccountIdentity::new(
                    "ibkr",
                    self.account_id.clone(),
                )
                .map_err(IntegrationError::InvalidPayload)?,
            segment_key: kairos_primitives::SegmentKey::new(self.segment_key.clone())
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
            environment: "live".into(),
            account_model: None,
        };
        fetch_snapshot(&self.options, &segment)
            .map(crate::application::ExternalAccountEvent::Snapshot)
            .map(AccountEventReceive::Event)
            .map_err(IntegrationError::InvalidPayload)
    }
}

fn fetch_snapshot(
    options: &IbkrOptions,
    segment: &ExternalAccountSegment,
) -> Result<ExternalAccountSnapshot, String> {
    let client = options.connect()?;
    let account = AccountId(segment.identity.account_id.to_string());
    let updates = client
        .account_updates(&account)
        .map_err(|e| e.to_string())?;
    let mut balance_values: BTreeMap<String, (Option<ExternalDecimal>, Option<ExternalDecimal>)> =
        BTreeMap::new();
    let mut positions = Vec::new();
    let mut equity = None;
    let mut net_profit = None;
    for item in updates.iter_data() {
        match item.map_err(|e| e.to_string())? {
            AccountUpdate::AccountValue(value) => {
                if value
                    .account
                    .as_deref()
                    .is_some_and(|value| value != segment.identity.account_id.as_str())
                {
                    continue;
                }
                if let Ok(number) = decimal_text(&value.value) {
                    match value.key.as_str() {
                        "TotalCashValue" => {
                            balance_values.entry(value.currency).or_default().0 = Some(number);
                        }
                        "AvailableFunds" => {
                            balance_values.entry(value.currency).or_default().1 = Some(number);
                        }
                        "NetLiquidation" => equity = Some(number),
                        "RealizedPnL" | "RealizedPnL-S" => net_profit = Some(number),
                        _ => {}
                    }
                }
            }
            AccountUpdate::PortfolioValue(value) => {
                if value
                    .account
                    .as_deref()
                    .is_some_and(|account| account != segment.identity.account_id.as_str())
                {
                    continue;
                }
                if value.position != 0.0 {
                    let provider_instrument = external_instrument_ref(
                        crate::domain::ParticipantKind::Broker,
                        "ibkr",
                        "equity",
                        &value.contract.symbol.to_string(),
                    )?;
                    positions.push(ExternalPosition {
                        provider_instrument,
                        quantity: decimal_f64_value(value.position),
                        average_price: Some(decimal_f64_value(value.average_cost)),
                        mark_price: Some(decimal_f64_value(value.market_price)),
                        unrealized_pnl: Some(decimal_f64_value(value.unrealized_pnl)),
                        realized_pnl: Some(decimal_f64_value(value.realized_pnl)),
                        updated_at_unix_nanos: now_nanos().into(),
                    });
                }
            }
            AccountUpdate::End => {
                updates.cancel();
                break;
            }
            AccountUpdate::UpdateTime(_) => {}
        }
    }
    let balances = balance_values
        .into_iter()
        .map(
            |(currency, (total, available))| -> Result<ExternalBalance, String> {
                Ok(ExternalBalance {
                    asset_id: kairos_primitives::AssetId::new(format!(
                        "asset:equity:{currency}"
                    ))?,
                    asset_code: kairos_primitives::Currency::new(currency)?,
                    total: total.or(available).unwrap_or_default(),
                    available,
                    locked: None,
                    borrowed: None,
                    interest: None,
                })
            },
        )
        .collect::<Result<Vec<_>, _>>()?;
    let open_orders = client
        .open_orders()
        .map_err(|e| e.to_string())?
        .iter_data()
        .filter_map(|item| match item.ok()? {
            Orders::OrderData(value) if value.order.total_quantity > 0.0 => {
                let provider_instrument = external_instrument_ref(
                    crate::domain::ParticipantKind::Broker,
                    "ibkr",
                    "equity",
                    &value.contract.symbol.to_string(),
                )
                .ok()?;
                Some(ExternalOpenOrder {
                    order_id: kairos_primitives::OrderId::new(value.order_id.to_string()).ok()?,
                    remote_order_id: Some(
                        kairos_primitives::RemoteOrderId::new(value.order_id.to_string()).ok()?,
                    ),
                    provider_instrument,
                    side: if format!("{:?}", value.order.action).eq_ignore_ascii_case("sell") {
                        kairos_primitives::OrderSide::Sell
                    } else {
                        kairos_primitives::OrderSide::Buy
                    },
                    quantity: decimal_f64_value(value.order.total_quantity),
                    filled_quantity: ExternalDecimal::new(0, 0),
                    status: normalize_ibkr_order_status(value.order_state.status, None, None),
                })
            }
            _ => None,
        })
        .collect();
    Ok(ExternalAccountSnapshot {
        segment_key: segment.segment_key.clone(),
        balances,
        collateral: Vec::new(),
        positions,
        open_orders,
        status: ExternalAccountStatus::Ready,
        observed_at_unix_nanos: now_nanos().into(),
        equity,
        initial_equity: None,
        net_profit,
        account_model: segment
            .account_model
            .as_deref()
            .and_then(crate::application::capabilities::ExternalAccountModel::parse),
        margin_mode: None,
        position_mode: None,
        partial: false,
    })
}

fn decimal_f64_value(value: f64) -> ExternalDecimal {
    let text = format!("{value:.8}");
    decimal_text(&text).unwrap_or_default()
}
fn decimal_text(value: &str) -> Result<ExternalDecimal, String> {
    ExternalDecimal::parse(value)
}
fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}
