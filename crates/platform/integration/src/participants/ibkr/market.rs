use crate::participants::ibkr::{descriptor, IbkrMarketDataConfig};
use crate::services::participants::ibkr::{
    execution::SessionService, market::MarketQueryService, IbkrOptions,
};
use crate::{
    ConnectionDescriptor, ConnectionHealth, ConnectionHealthQuery, ConnectionLifecycle,
    ConnectionLifecycleCommand, ConnectionState, IntegrationError, MarketQuote, MarketQuoteQuery,
};
use kairos_primitives::ParticipantSymbol;

pub struct IbkrMarketDataConnection {
    state: ConnectionState,
    session: std::sync::Arc<SessionService>,
    query: MarketQueryService,
}

impl IbkrMarketDataConnection {
    pub fn new(config: IbkrMarketDataConfig) -> Result<Self, IntegrationError> {
        if config.exchange.trim().is_empty() || config.currency.trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "IBKR exchange and currency are required".into(),
            ));
        }
        let options = IbkrOptions::new(config.host, config.port, config.client_id)
            .map_err(IntegrationError::InvalidRequest)?;
        let descriptor = descriptor(
            config.binding_id,
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
impl MarketQuoteQuery for IbkrMarketDataConnection {
    async fn fetch_quotes(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketQuote>, IntegrationError> {
        self.query.quotes(symbols).await
    }
}
