use secrecy::ExposeSecret;

use crate::participants::hyperliquid::HyperliquidExchangeRestConfig;
use crate::services::participants::hyperliquid::exchange::ExchangeService;
use crate::{
    CommandResult, ConnectionDescriptor, ConnectionHealth, ConnectionHealthQuery,
    ConnectionLifecycle, ConnectionLifecycleCommand, ConnectionState, IntegrationError,
    OrderCommand, OrderEntryEvent, OrderEntryRequest, ParticipantKind, ParticipantRef,
};

pub struct HyperliquidExchangeRestConnection {
    state: ConnectionState,
    service: ExchangeService,
}

impl HyperliquidExchangeRestConnection {
    pub fn new(config: HyperliquidExchangeRestConfig) -> Result<Self, IntegrationError> {
        let mut descriptor = ConnectionDescriptor::new(
            config.binding_id,
            ParticipantRef::new(ParticipantKind::Exchange, "hyperliquid")
                .map_err(IntegrationError::InvalidRequest)?,
            "exchange.rest",
        )
        .map_err(IntegrationError::InvalidRequest)?;
        descriptor.environment = config.environment;
        descriptor
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let service = ExchangeService::new(&config.endpoint, config.private_key.expose_secret())?;
        Ok(Self {
            state: ConnectionState::new(descriptor),
            service,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.state.identity
    }
}

impl ConnectionHealthQuery for HyperliquidExchangeRestConnection {
    fn connection_health(&mut self) -> ConnectionHealth {
        let mut health = self.state.health();
        health.authenticated = self.service.is_connected();
        health
    }
}

impl ConnectionLifecycleCommand for HyperliquidExchangeRestConnection {
    async fn connect(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Starting;
        self.service
            .connect()
            .await
            .inspect(|()| self.state.mark_ready(true))
            .inspect_err(|error| self.state.mark_failed(error.to_string()))
    }

    async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Stopping;
        self.service.disconnect();
        self.state.mark_stopped();
        Ok(())
    }

    async fn reconnect(&mut self) -> Result<(), IntegrationError> {
        self.disconnect().await?;
        self.connect().await?;
        self.state.reconnect_count = self.state.reconnect_count.saturating_add(1);
        Ok(())
    }
}

impl OrderCommand for HyperliquidExchangeRestConnection {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        self.service.submit(request).await
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        self.service
            .cancel(request, remote_order_id, at_unix_nanos)
            .await
    }
}
