use kairos_primitives::integration::ParticipantSymbol;

use crate::participants::ibkr::{IbkrMarketDataConfig, descriptor};
use crate::services::participants::ibkr::IbkrOptions;
use crate::services::participants::ibkr::execution::SessionService;
use crate::services::participants::ibkr::market::MarketQueryService;
use crate::services::participants::ibkr::market_stream::MarketStreamService;
use crate::{
    ConnectionDescriptor, ConnectionHealth, ConnectionHealthQuery, ConnectionLifecycle,
    ConnectionLifecycleCommand, ConnectionMaintenance, ConnectionState, IntegrationError,
    MaintenanceOutcome, MarketDataStream, MarketDelivery, MarketEvent, MarketQuote,
    MarketQuoteQuery, MarketSubscription, MarketSubscriptionCommand, MarketSubscriptionOutcome,
    MarketSubscriptionRequest,
};

pub struct IbkrMarketDataConnection {
    state: ConnectionState,
    session: std::sync::Arc<SessionService>,
    query: MarketQueryService,
    stream: MarketStreamService,
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
        let stream = MarketStreamService::new(
            session.clone(),
            config.exchange.clone(),
            config.currency.clone(),
            config.market_data_line_limit,
        )?;
        Ok(Self {
            state: ConnectionState::new(descriptor),
            query: MarketQueryService {
                session: session.clone(),
                exchange: config.exchange,
                currency: config.currency,
            },
            stream,
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
        let result = async {
            self.session.connect().await?;
            self.stream.restore().await
        }
        .await;
        match result {
            Ok(()) => {
                self.state.mark_ready(true);
                Ok(())
            },
            Err(error) => {
                self.stream.suspend().await;
                self.session.disconnect().await;
                self.state.mark_failed(error.to_string());
                Err(error)
            },
        }
    }
    async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Stopping;
        self.stream.suspend().await;
        self.session.disconnect().await;
        self.state.mark_stopped();
        Ok(())
    }
    async fn reconnect(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Starting;
        self.stream.suspend().await;
        self.session.disconnect().await;
        let result = async {
            self.session.connect().await?;
            self.stream.restore().await
        }
        .await;
        match result {
            Ok(()) => {
                self.state.mark_ready(true);
                self.state.reconnect_count = self.state.reconnect_count.saturating_add(1);
                Ok(())
            },
            Err(error) => {
                self.stream.suspend().await;
                self.session.disconnect().await;
                self.state.mark_failed(error.to_string());
                Err(error)
            },
        }
    }
}
impl MarketSubscriptionCommand for IbkrMarketDataConnection {
    async fn subscribe(
        &mut self,
        request: MarketSubscriptionRequest,
    ) -> Result<MarketSubscriptionOutcome<MarketSubscription>, IntegrationError> {
        if self.state.lifecycle != ConnectionLifecycle::Ready {
            return Err(IntegrationError::NotReady);
        }
        let id = self.stream.subscribe(&request).await?;
        Ok(MarketSubscriptionOutcome::Confirmed(MarketSubscription {
            id,
            feeds: request.feeds,
            delivery: MarketDelivery::Push,
        }))
    }

    async fn unsubscribe(
        &mut self,
        subscription: crate::MarketSubscriptionId,
    ) -> Result<MarketSubscriptionOutcome<()>, IntegrationError> {
        self.stream.unsubscribe(subscription).await?;
        Ok(MarketSubscriptionOutcome::Confirmed(()))
    }
}

impl MarketDataStream for IbkrMarketDataConnection {
    fn poll_next(
        &mut self,
        cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<Result<MarketEvent, IntegrationError>> {
        self.stream.poll_next(cx)
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
