//! Account projection over the native Binance Margin private order channel.

use crate::application::capabilities::account_facts::ExternalAccountEventEnvelope;
use crate::application::{
    AsyncAccountEventSource, AsyncOrderEventSource, ExternalEventEnvelope, IntegrationError,
};
use crate::domain::ConnectionHealth;

use super::async_margin_order_events::BinanceAsyncMarginOrderEventSource;

pub(crate) struct BinanceAsyncMarginAccountEventSource {
    segment_key: kairos_primitives::SegmentKey,
    inner: BinanceAsyncMarginOrderEventSource,
}

impl BinanceAsyncMarginAccountEventSource {
    pub(crate) fn new(
        binding_id: impl Into<String>,
        segment_key: impl Into<String>,
        client: super::spot::account::BinanceSpotAccountClient,
        websocket_endpoint: impl Into<String>,
        isolated_symbol: Option<String>,
        event_queue_capacity: usize,
    ) -> Result<Self, IntegrationError> {
        let segment_key = kairos_primitives::SegmentKey::new(segment_key.into())
            .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?;
        Ok(Self {
            segment_key,
            inner: BinanceAsyncMarginOrderEventSource::new(
                binding_id,
                client,
                websocket_endpoint,
                isolated_symbol,
                event_queue_capacity,
            )?,
        })
    }
}

impl AsyncAccountEventSource for BinanceAsyncMarginAccountEventSource {
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
        let payload = super::account_events::from_execution_event(
            "binance-spot",
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
