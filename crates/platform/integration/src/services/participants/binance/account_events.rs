//! Shared projection from Binance private order facts into Account facts.

use crate::application::capabilities::account_facts::{
    external_instrument_ref, ExternalAccountEvent, ExternalFillEvent, ExternalOrderEvent,
    ExternalOrderStatus,
};
use crate::application::{ExternalExecutionEvent, IntegrationError};

pub(super) fn from_execution_event(
    product: &str,
    segment_key: &kairos_primitives::SegmentKey,
    value: ExternalExecutionEvent,
) -> Result<ExternalAccountEvent, IntegrationError> {
    let status = match value.status {
        kairos_primitives::OrderStatus::Acknowledged | kairos_primitives::OrderStatus::Accepted => {
            ExternalOrderStatus::Acknowledged
        }
        kairos_primitives::OrderStatus::PartiallyFilled => ExternalOrderStatus::PartiallyFilled,
        kairos_primitives::OrderStatus::Filled => ExternalOrderStatus::Filled,
        kairos_primitives::OrderStatus::Canceled => ExternalOrderStatus::Canceled,
        kairos_primitives::OrderStatus::Rejected => ExternalOrderStatus::Rejected,
        kairos_primitives::OrderStatus::Expired => ExternalOrderStatus::Expired,
        _ => ExternalOrderStatus::Unknown,
    };
    let order = ExternalAccountEvent::Order(ExternalOrderEvent {
        order_id: value.order_id.clone(),
        status,
        remote_order_id: None,
        filled_quantity: value.filled_quantity.map(external_decimal),
        occurred_at_unix_nanos: value.occurred_at_unix_nanos,
        reason: value.reason.clone(),
    });
    let Some(fill_id) = value.execution_id else {
        return Ok(order);
    };
    let quantity = value.fill_quantity.ok_or_else(|| {
        IntegrationError::InvalidPayload("Binance fill quantity is missing".into())
    })?;
    let price = value
        .fill_price
        .ok_or_else(|| IntegrationError::InvalidPayload("Binance fill price is missing".into()))?;
    let provider_instrument = external_instrument_ref(
        crate::domain::ParticipantKind::Exchange,
        "binance",
        product,
        value.symbol.as_str(),
    )
    .map_err(IntegrationError::InvalidPayload)?;
    let fill = ExternalAccountEvent::Fill(ExternalFillEvent {
        fill_id,
        order_id: value.order_id,
        segment_key: segment_key.clone(),
        provider_instrument,
        side: match value.side {
            Some(kairos_primitives::OrderSide::Sell) => "SELL",
            _ => "BUY",
        }
        .into(),
        quantity: external_decimal(quantity),
        price: external_decimal(price),
        fee_asset: value.fee_currency,
        fee_amount: value.fee_amount.map(external_decimal),
        occurred_at_unix_nanos: value.occurred_at_unix_nanos,
    });
    Ok(ExternalAccountEvent::Batch(vec![order, fill]))
}

fn external_decimal(
    value: crate::application::capabilities::DecimalValue,
) -> crate::application::capabilities::account_facts::ExternalDecimal {
    crate::application::capabilities::account_facts::ExternalDecimal::new(
        value.mantissa,
        value.scale,
    )
}
