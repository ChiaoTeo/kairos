//! Native async IBKR Account projections sharing the execution hard session.

use std::collections::BTreeMap;
use std::sync::Arc;

use futures_util::StreamExt;
use ibapi::accounts::types::AccountId;
use ibapi::accounts::AccountUpdate;
use ibapi::subscriptions::{Subscription, SubscriptionItem, SubscriptionItemStreamExt};
use tokio::sync::watch;

use crate::application::capabilities::account_facts::{
    external_instrument_ref, ExternalAccountEvent, ExternalAccountEventEnvelope,
    ExternalAccountSegment, ExternalAccountSnapshot, ExternalAccountStatus, ExternalBalance,
    ExternalDecimal, ExternalOpenOrder, ExternalPosition,
};
use crate::application::{
    AsyncAccountEventSource, AsyncAccountReadConnection, ExternalEventEnvelope, IntegrationError,
};
use crate::domain::{ConnectionHealth, ConnectionLifecycle, ParticipantKind, ParticipantRef};

use super::async_execution::{collect_orders, IbkrAsyncSession};
use super::normalize_ibkr_order_status;

const QUERY_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(10);

pub(crate) struct IbkrAsyncAccountRead {
    pub(crate) session: Arc<IbkrAsyncSession>,
}

pub(crate) struct IbkrAsyncAccountEvents {
    session: Arc<IbkrAsyncSession>,
    binding_id: String,
    account_id: String,
    segment_key: kairos_domain_types::SegmentKey,
    subscription: Option<Subscription<AccountUpdate>>,
    notices: watch::Receiver<Option<ibapi::Notice>>,
    balance_values: BTreeMap<String, (Option<ExternalDecimal>, Option<ExternalDecimal>)>,
    lifecycle: ConnectionLifecycle,
    channel_epoch: u64,
    last_error: Option<String>,
}

impl IbkrAsyncAccountEvents {
    pub(crate) fn new(
        session: Arc<IbkrAsyncSession>,
        binding_id: impl Into<String>,
        account_id: impl Into<String>,
        segment_key: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        let segment_key = kairos_domain_types::SegmentKey::new(segment_key.into())
            .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?;
        let notices = session.notice_receiver();
        Ok(Self {
            session,
            binding_id: binding_id.into(),
            account_id: account_id.into(),
            segment_key,
            subscription: None,
            notices,
            balance_values: BTreeMap::new(),
            lifecycle: ConnectionLifecycle::Created,
            channel_epoch: 0,
            last_error: None,
        })
    }
}

impl AsyncAccountReadConnection for IbkrAsyncAccountRead {
    async fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, IntegrationError> {
        let client = self.session.client().await?;
        let account = AccountId(segment.identity.account_id.to_string());
        tokio::time::timeout(QUERY_TIMEOUT, async {
            let subscription = client.account_updates(&account).await.map_err(transport)?;
            let mut stream = subscription.filter_data();
            let mut balances = BTreeMap::new();
            let mut positions = Vec::new();
            let mut equity = None;
            let mut net_profit = None;
            while let Some(item) = stream.next().await {
                match item.map_err(transport)? {
                    AccountUpdate::AccountValue(value) => {
                        if value.account.as_deref().is_some_and(|id| id != account.0) {
                            continue;
                        }
                        let number = match ExternalDecimal::parse(&value.value) {
                            Ok(value) => value,
                            Err(_) => continue,
                        };
                        match value.key.as_str() {
                            "TotalCashValue" => {
                                balances.entry(value.currency).or_insert((None, None)).0 =
                                    Some(number)
                            }
                            "AvailableFunds" => {
                                balances.entry(value.currency).or_insert((None, None)).1 =
                                    Some(number)
                            }
                            "NetLiquidation" => equity = Some(number),
                            "RealizedPnL" | "RealizedPnL-S" => net_profit = Some(number),
                            _ => {}
                        }
                    }
                    AccountUpdate::PortfolioValue(value) => {
                        if value.account.as_deref().is_some_and(|id| id != account.0) {
                            continue;
                        }
                        if value.position != 0.0 {
                            positions.push(position(value)?);
                        }
                    }
                    AccountUpdate::End => break,
                    AccountUpdate::UpdateTime(_) => {}
                }
            }
            let open_orders = collect_orders(client.open_orders().await.map_err(transport)?)
                .await?
                .into_iter()
                .filter(|value| value.order.account.is_empty() || value.order.account == account.0)
                .filter_map(open_order)
                .collect();
            Ok(ExternalAccountSnapshot {
                segment_key: segment.segment_key.clone(),
                balances: normalized_balances(balances)?,
                collateral: Vec::new(),
                positions,
                open_orders,
                status: ExternalAccountStatus::Ready,
                observed_at_unix_nanos: now_nanos(),
                equity,
                initial_equity: None,
                net_profit,
                account_model: segment
                    .account_model
                    .as_deref()
                    .and_then(crate::application::ExternalAccountModel::parse),
                margin_mode: None,
                position_mode: None,
                partial: false,
            })
        })
        .await
        .map_err(|_| IntegrationError::Unavailable("IBKR account snapshot timed out".into()))?
    }
}

impl AsyncAccountEventSource for IbkrAsyncAccountEvents {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        if self.lifecycle == ConnectionLifecycle::Ready && self.subscription.is_some() {
            return Ok(());
        }
        self.lifecycle = ConnectionLifecycle::Starting;
        let client = self.session.client().await?;
        let subscription = tokio::time::timeout(
            QUERY_TIMEOUT,
            client.account_updates(&AccountId(self.account_id.clone())),
        )
        .await
        .map_err(|_| IntegrationError::Unavailable("IBKR account subscription timed out".into()))?
        .map_err(transport)?;
        self.subscription = Some(subscription);
        self.balance_values.clear();
        self.channel_epoch = self.channel_epoch.saturating_add(1);
        self.lifecycle = ConnectionLifecycle::Ready;
        self.last_error = None;
        Ok(())
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.lifecycle = ConnectionLifecycle::Stopping;
        if let Some(subscription) = self.subscription.take() {
            subscription.cancel().await;
        }
        self.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    fn channel_health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.lifecycle,
            healthy: self.lifecycle == ConnectionLifecycle::Ready && self.subscription.is_some(),
            authenticated: self.lifecycle == ConnectionLifecycle::Ready
                && self.subscription.is_some(),
            last_error: self.last_error.clone(),
        }
    }

    async fn next_account_event(
        &mut self,
    ) -> Result<ExternalAccountEventEnvelope, IntegrationError> {
        self.connect_channel().await?;
        loop {
            let item = {
                let subscription = self
                    .subscription
                    .as_mut()
                    .ok_or(IntegrationError::NotReady)?;
                tokio::select! {
                    changed = self.notices.changed() => {
                        if changed.is_err() {
                            return Err(IntegrationError::ResyncRequired(
                                "IBKR global notice stream ended".into(),
                            ));
                        }
                        let notice = self.notices.borrow_and_update().clone();
                        if let Some(notice) = notice.as_ref() {
                            if let Some(error) = super::async_execution::notice_error(notice) {
                                return Err(error);
                            }
                        }
                        continue;
                    }
                    item = subscription.next() => item
                        .ok_or_else(|| IntegrationError::ResyncRequired(
                            "IBKR account stream ended".into(),
                        ))?
                        .map_err(transport)?,
                }
            };
            let SubscriptionItem::Data(update) = item else {
                if let SubscriptionItem::Notice(notice) = item {
                    tracing::info!(
                        event = "ibkr_notice",
                        component = "integration",
                        channel = "account-events",
                        code = notice.code,
                        message = %notice.message,
                        "IBKR account subscription notice observed"
                    );
                    if let Some(error) = super::async_execution::notice_error(&notice) {
                        return Err(error);
                    }
                }
                continue;
            };
            let Some(payload) = partial_event(&self.segment_key, update, &mut self.balance_values)?
            else {
                continue;
            };
            let observed = now_nanos();
            return Ok(ExternalEventEnvelope {
                participant: ParticipantRef::new(ParticipantKind::Broker, "ibkr")
                    .expect("static IBKR participant"),
                binding_id: self.binding_id.clone(),
                channel_id: format!("{}.account-updates", self.binding_id),
                channel_epoch: self.channel_epoch,
                provider_event_id: None,
                provider_sequence: None,
                observed_at_unix_nanos: observed,
                received_at_unix_nanos: observed,
                payload,
            });
        }
    }
}

fn partial_event(
    segment_key: &kairos_domain_types::SegmentKey,
    update: AccountUpdate,
    balance_values: &mut BTreeMap<String, (Option<ExternalDecimal>, Option<ExternalDecimal>)>,
) -> Result<Option<ExternalAccountEvent>, IntegrationError> {
    let mut snapshot = ExternalAccountSnapshot {
        segment_key: segment_key.clone(),
        balances: Vec::new(),
        collateral: Vec::new(),
        positions: Vec::new(),
        open_orders: Vec::new(),
        status: ExternalAccountStatus::Ready,
        observed_at_unix_nanos: now_nanos(),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: None,
        margin_mode: None,
        position_mode: None,
        partial: true,
    };
    match update {
        AccountUpdate::AccountValue(value) => {
            let number =
                ExternalDecimal::parse(&value.value).map_err(IntegrationError::InvalidPayload)?;
            match value.key.as_str() {
                "TotalCashValue" | "AvailableFunds" => {
                    let currency = value.currency;
                    let values = balance_values.entry(currency.clone()).or_default();
                    if value.key == "TotalCashValue" {
                        values.0 = Some(number);
                    } else {
                        values.1 = Some(number);
                    }
                    let Some(total) = values.0 else {
                        return Ok(None);
                    };
                    snapshot.balances.push(ExternalBalance {
                        asset_id: kairos_domain_types::AssetId::new(format!(
                            "asset:fiat:{currency}"
                        ))
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                        asset_code: kairos_domain_types::Currency::new(currency)
                            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                        total,
                        available: values.1,
                        locked: None,
                        borrowed: None,
                        interest: None,
                    });
                }
                "NetLiquidation" => snapshot.equity = Some(number),
                "RealizedPnL" | "RealizedPnL-S" => snapshot.net_profit = Some(number),
                _ => return Ok(None),
            }
        }
        AccountUpdate::PortfolioValue(value) if value.position != 0.0 => {
            snapshot.positions.push(position(value)?);
        }
        AccountUpdate::PortfolioValue(_) | AccountUpdate::UpdateTime(_) | AccountUpdate::End => {
            return Ok(None)
        }
    }
    Ok(Some(ExternalAccountEvent::Snapshot(snapshot)))
}

fn position(
    value: ibapi::accounts::AccountPortfolioValue,
) -> Result<ExternalPosition, IntegrationError> {
    let provider_instrument = external_instrument_ref(
        ParticipantKind::Broker,
        "ibkr",
        "equity",
        &value.contract.symbol.to_string(),
    )
    .map_err(IntegrationError::InvalidPayload)?;
    Ok(ExternalPosition {
        provider_instrument,
        quantity: decimal_f64(value.position),
        average_price: Some(decimal_f64(value.average_cost)),
        mark_price: Some(decimal_f64(value.market_price)),
        unrealized_pnl: Some(decimal_f64(value.unrealized_pnl)),
        realized_pnl: Some(decimal_f64(value.realized_pnl)),
        updated_at_unix_nanos: now_nanos(),
    })
}

fn open_order(value: ibapi::orders::OrderData) -> Option<ExternalOpenOrder> {
    let provider_instrument = external_instrument_ref(
        ParticipantKind::Broker,
        "ibkr",
        "equity",
        &value.contract.symbol.to_string(),
    )
    .ok()?;
    Some(ExternalOpenOrder {
        order_id: kairos_domain_types::OrderId::new(value.order_id.to_string()).ok()?,
        remote_order_id: kairos_domain_types::RemoteOrderId::new(value.order_id.to_string()).ok(),
        provider_instrument,
        side: if format!("{:?}", value.order.action).eq_ignore_ascii_case("sell") {
            kairos_domain_types::OrderSide::Sell
        } else {
            kairos_domain_types::OrderSide::Buy
        },
        quantity: decimal_f64(value.order.total_quantity),
        filled_quantity: ExternalDecimal::default(),
        status: normalize_ibkr_order_status(value.order_state.status, None, None),
    })
}

fn normalized_balances(
    values: BTreeMap<String, (Option<ExternalDecimal>, Option<ExternalDecimal>)>,
) -> Result<Vec<ExternalBalance>, IntegrationError> {
    values
        .into_iter()
        .map(|(currency, (total, available))| {
            Ok(ExternalBalance {
                asset_id: kairos_domain_types::AssetId::new(format!("asset:fiat:{currency}"))
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                asset_code: kairos_domain_types::Currency::new(currency)
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                total: total.or(available).unwrap_or_default(),
                available,
                locked: None,
                borrowed: None,
                interest: None,
            })
        })
        .collect()
}

fn decimal_f64(value: f64) -> ExternalDecimal {
    ExternalDecimal::parse(&format!("{value:.8}")).unwrap_or_default()
}

fn now_nanos() -> kairos_domain_types::UnixNanos {
    kairos_domain_types::UnixNanos::new(
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
            .min(u64::MAX as u128) as u64,
    )
}

fn transport(error: impl ToString) -> IntegrationError {
    IntegrationError::Transport(error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use ibapi::accounts::AccountValue;

    #[test]
    fn available_funds_waits_for_total_and_preserves_total_cash_value() {
        let segment_key = kairos_domain_types::SegmentKey::new("equity").unwrap();
        let mut values = BTreeMap::new();

        let available = partial_event(
            &segment_key,
            AccountUpdate::AccountValue(AccountValue {
                key: "AvailableFunds".into(),
                value: "80".into(),
                currency: "USD".into(),
                account: Some("DU123".into()),
            }),
            &mut values,
        )
        .unwrap();
        assert!(available.is_none());

        let event = partial_event(
            &segment_key,
            AccountUpdate::AccountValue(AccountValue {
                key: "TotalCashValue".into(),
                value: "100".into(),
                currency: "USD".into(),
                account: Some("DU123".into()),
            }),
            &mut values,
        )
        .unwrap()
        .expect("balance update after total is known");
        let ExternalAccountEvent::Snapshot(snapshot) = event else {
            panic!("expected partial snapshot");
        };
        assert!(snapshot.partial);
        assert_eq!(snapshot.balances.len(), 1);
        assert_eq!(snapshot.balances[0].total, ExternalDecimal::new(100, 0));
        assert_eq!(
            snapshot.balances[0].available.expect("available funds"),
            ExternalDecimal::new(80, 0)
        );
    }

    #[test]
    fn net_liquidation_is_emitted_as_partial_equity() {
        let segment_key = kairos_domain_types::SegmentKey::new("equity").unwrap();
        let event = partial_event(
            &segment_key,
            AccountUpdate::AccountValue(AccountValue {
                key: "NetLiquidation".into(),
                value: "1234.5".into(),
                currency: "USD".into(),
                account: Some("DU123".into()),
            }),
            &mut BTreeMap::new(),
        )
        .unwrap()
        .expect("equity update");
        let ExternalAccountEvent::Snapshot(snapshot) = event else {
            panic!("expected partial snapshot");
        };
        assert_eq!(
            snapshot.equity.expect("equity"),
            ExternalDecimal::new(12345, 1)
        );
    }
}
