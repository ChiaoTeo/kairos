//! Reusable IBKR TWS/IB Gateway session mechanisms.

pub(crate) mod account;
pub(crate) mod execution;
pub(crate) mod market;
pub(crate) mod market_stream;

use ibapi::orders::OrderStatusKind;

pub(crate) fn normalize_ibkr_order_status(
    status: OrderStatusKind,
    filled: Option<f64>,
    remaining: Option<f64>,
) -> kairos_primitives::integration::OrderStatus {
    if filled.is_some_and(|value| value > 0.0) && remaining.is_some_and(|value| value > 0.0) {
        return kairos_primitives::integration::OrderStatus::PartiallyFilled;
    }
    match status {
        OrderStatusKind::ApiPending
        | OrderStatusKind::PendingSubmit
        | OrderStatusKind::PreSubmitted => {
            kairos_primitives::integration::OrderStatus::Acknowledged
        },
        OrderStatusKind::PendingCancel | OrderStatusKind::Submitted => {
            kairos_primitives::integration::OrderStatus::Accepted
        },
        OrderStatusKind::ApiCancelled | OrderStatusKind::Cancelled => {
            kairos_primitives::integration::OrderStatus::Canceled
        },
        OrderStatusKind::Filled => kairos_primitives::integration::OrderStatus::Filled,
        OrderStatusKind::Inactive => kairos_primitives::integration::OrderStatus::Unknown,
    }
}

#[derive(Clone, Debug)]
pub(crate) struct IbkrOptions {
    pub(crate) host: String,
    pub(crate) port: u16,
    pub(crate) client_id: i32,
}

impl IbkrOptions {
    pub(crate) fn new(host: impl Into<String>, port: u16, client_id: i32) -> Result<Self, String> {
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
}
