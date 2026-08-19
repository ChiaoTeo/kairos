use kairos_primitives::ParticipantSymbol;

use crate::participants::ibkr::{IbkrMarketDataConfig, descriptor};
use crate::services::participants::ibkr::IbkrOptions;
use crate::services::participants::ibkr::execution::SessionService;
use crate::services::participants::ibkr::market::MarketQueryService;
use crate::{
    ConnectionDescriptor, ConnectionHealth, ConnectionHealthQuery, ConnectionLifecycle,
    ConnectionLifecycleCommand, ConnectionMaintenance, ConnectionState, IntegrationError,
    MaintenanceOutcome, MarketQuote, MarketQuoteQuery,
};

pub struct IbkrMarketDataConnection {
    state: ConnectionState,
    session: std::sync::Arc<SessionService>,
    query: MarketQueryService,
}

impl IbkrMarketDataConnection {
    pub fn new(
        connection_key: crate::ConnectionKey,
        config: IbkrMarketDataConfig,
    ) -> Result<Self, IntegrationError> {
        if config.exchange.trim().is_empty() || config.currency.trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "IBKR exchange and currency are required".into(),
            ));
        }
        let options = IbkrOptions::new(config.host, config.port, config.client_id)
            .map_err(IntegrationError::InvalidRequest)?;
        let descriptor = descriptor(
            connection_key,
            config.environment,
            config.client_id,
            "market-data",
        )?;
        let session = SessionService::new(options, String::new());
        Ok(Self {
            state: ConnectionState::new(descriptor),
            query: MarketQueryService {
                session: session.clone(),
                exchange: config.exchange,
                currency: config.currency,
            },
            session,
        })
    }
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.state.identity
    }
}
impl ConnectionHealthQuery for IbkrMarketDataConnection {
    fn connection_health(&mut self) -> ConnectionHealth {
        self.state.health()
    }
}
impl ConnectionLifecycleCommand for IbkrMarketDataConnection {
    async fn connect(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Starting;
        self.session
            .connect()
            .await
            .inspect(|()| self.state.mark_ready(true))
            .inspect_err(|error| self.state.mark_failed(error.to_string()))
    }
    async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Stopping;
        self.session.disconnect().await;
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
impl ConnectionMaintenance for IbkrMarketDataConnection {
    fn next_maintenance_at(&self) -> Option<tokio::time::Instant> {
        None
    }

    fn poll_maintenance(
        &mut self,
        _cx: &mut std::task::Context<'_>,
        _now: tokio::time::Instant,
    ) -> std::task::Poll<Result<MaintenanceOutcome, IntegrationError>> {
        std::task::Poll::Ready(Ok(MaintenanceOutcome::Healthy))
    }
}
impl MarketQuoteQuery for IbkrMarketDataConnection {
    async fn fetch_quotes(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketQuote>, IntegrationError> {
        self.query.quotes(symbols).await
    }
}
