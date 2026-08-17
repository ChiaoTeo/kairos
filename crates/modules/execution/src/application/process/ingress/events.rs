//! Mapping from Integration external event envelopes into Execution-owned facts.

use super::super::ExecutionProcess;
use crate::services::actor::RemoteOrderEvent;
use kairos_integration::application::{ExternalEventEnvelope, ExternalExecutionEvent};
use std::sync::atomic::Ordering;

impl<E, Q, S> ExecutionProcess<E, Q, S> {
    pub(in crate::application::process) fn apply_exchange_event(
        &mut self,
        event: RemoteOrderEvent,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        self.metrics
            .exchange_events_applied
            .fetch_add(1, Ordering::Relaxed);
        if self
            .application
            .accept_remote_event_identity(&event.event_id)
        {
            if let Err(error) = self.application.apply_remote_execution_event(event.event) {
                tracing::warn!(event = "exchange_event_rejected", component = "execution", error = %error, "exchange event was not applied");
            }
        }
        Ok(())
    }
}

pub(in crate::application::process) fn remote_order_event_from_envelope(
    envelope: ExternalEventEnvelope<ExternalExecutionEvent>,
) -> RemoteOrderEvent {
    let event_id = envelope.provider_event_id.clone().unwrap_or_else(|| {
        format!(
            "{}:{:?}:{}",
            envelope.payload.order_id,
            envelope.payload.status,
            envelope.payload.occurred_at_unix_nanos.get()
        )
    });
    let event = envelope.payload;
    RemoteOrderEvent {
        event_id,
        connection_id: envelope.binding_id,
        event: crate::application::RemoteOrderUpdate {
            order_id: event.order_id,
            symbol: event.symbol,
            status: crate::application::remote_status(&format!("{:?}", event.status)),
            fill_quantity: event
                .fill_quantity
                .and_then(|value| format_decimal(value).parse().ok()),
            fill_price: event
                .fill_price
                .and_then(|value| format_decimal(value).parse().ok()),
            execution_id: event.execution_id,
            fee_currency: event.fee_currency,
            fee_amount: event
                .fee_amount
                .and_then(|value| format_decimal(value).parse().ok()),
            occurred_at_unix_nanos: event.occurred_at_unix_nanos,
            reason: event.reason,
        },
    }
}

fn format_decimal(value: kairos_integration::application::DecimalValue) -> String {
    if value.scale == 0 {
        return value.mantissa.to_string();
    }
    let negative = value.mantissa < 0;
    let digits = value.mantissa.unsigned_abs().to_string();
    let scale = usize::from(value.scale);
    let body = if digits.len() <= scale {
        format!("0.{}{}", "0".repeat(scale - digits.len()), digits)
    } else {
        let split = digits.len() - scale;
        format!("{}.{}", &digits[..split], &digits[split..])
    };
    if negative {
        format!("-{body}")
    } else {
        body
    }
}
