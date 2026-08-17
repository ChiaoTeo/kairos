//! Account projection over the native Binance Options private order channel.

use crate::application::capabilities::account_facts::ExternalAccountEventEnvelope;
use crate::application::{
    AsyncAccountEventSource, AsyncOrderEventSource, ExternalEventEnvelope, IntegrationError,
};
use crate::domain::ConnectionHealth;

use super::async_order_events::BinanceOptionsAsyncOrderEventSource;

pub(crate) struct BinanceOptionsAsyncAccountEventSource {
    segment_key: kairos_primitives::SegmentKey,
    inner: BinanceOptionsAsyncOrderEventSource,
}

impl BinanceOptionsAsyncAccountEventSource {
    pub(crate) fn new(
        binding_id: impl Into<String>,
        segment_key: impl Into<String>,
        client: super::account::BinanceOptionsAccountClient,
        websocket_endpoint: impl Into<String>,
        event_queue_capacity: usize,
    ) -> Result<Self, IntegrationError> {
        let segment_key = kairos_primitives::SegmentKey::new(segment_key.into())
            .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?;
        Ok(Self {
            segment_key,
            inner: BinanceOptionsAsyncOrderEventSource::new(
                binding_id,
                client,
                websocket_endpoint,
                event_queue_capacity,
            )?,
        })
    }
}

impl AsyncAccountEventSource for BinanceOptionsAsyncAccountEventSource {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.connect_channel().await
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.disconnect_channel().await
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.reconnect_channel().await
    }

    fn channel_health(&self) -> ConnectionHealth {
        self.inner.channel_health()
    }

    async fn next_account_event(
        &mut self,
    ) -> Result<ExternalAccountEventEnvelope, IntegrationError> {
        let event = self.inner.next_order_event().await?;
        let payload = super::super::account_events::from_execution_event(
            "binance-options",
            &self.segment_key,
            event.payload,
        )?;
        Ok(ExternalEventEnvelope {
            participant: event.participant,
            binding_id: event.binding_id,
            channel_id: event.channel_id.replace("order-events", "account-events"),
            channel_epoch: event.channel_epoch,
            provider_event_id: event.provider_event_id,
            provider_sequence: event.provider_sequence,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            received_at_unix_nanos: event.received_at_unix_nanos,
            payload,
        })
    }
}

#[cfg(test)]
mod tests {
    use kairos_primitives::{Currency, FillId, OrderId, OrderSide, OrderStatus, Symbol, UnixNanos};

    use crate::application::capabilities::account_facts::ExternalAccountEvent;
    use crate::application::capabilities::DecimalValue;
    use crate::application::ExternalExecutionEvent;

    use crate::services::participants::binance::account_events::from_execution_event;

    #[test]
    fn options_fill_projects_order_and_fill_account_facts() {
        let payload = from_execution_event(
            "binance-options",
            &kairos_primitives::SegmentKey::new("options").unwrap(),
            ExternalExecutionEvent {
                order_id: OrderId::new("order-1").unwrap(),
                symbol: Symbol::new("BTC-260327-100000-C").unwrap(),
                status: OrderStatus::PartiallyFilled,
                side: Some(OrderSide::Buy),
                order_type: None,
                quantity: Some(DecimalValue::parse("0.25").unwrap()),
                limit_price: None,
                filled_quantity: Some(DecimalValue::parse("0.1").unwrap()),
                remaining_quantity: None,
                fill_quantity: Some(DecimalValue::parse("0.1").unwrap()),
                fill_price: Some(DecimalValue::parse("100").unwrap()),
                execution_id: Some(FillId::new("binance-options:7").unwrap()),
                fee_currency: Some(Currency::new("USDT").unwrap()),
                fee_amount: Some(DecimalValue::parse("0.01").unwrap()),
                occurred_at_unix_nanos: UnixNanos::new(1_000),
                reason: String::new(),
            },
        )
        .unwrap();
        let ExternalAccountEvent::Batch(events) = payload else {
            panic!("expected order/fill batch");
        };
        assert_eq!(events.len(), 2);
        let ExternalAccountEvent::Fill(fill) = &events[1] else {
            panic!("expected fill fact");
        };
        assert_eq!(fill.segment_key, "options");
        assert_eq!(
            fill.provider_instrument.source_symbol.as_str(),
            "BTC-260327-100000-C"
        );
    }
}
