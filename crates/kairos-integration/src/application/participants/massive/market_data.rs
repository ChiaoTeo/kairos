use crate::application::capabilities::{ConnectionHealth, MarketStreamCapabilities};
use crate::application::{
    AsyncHistoricalMarketDataConnection, AsyncMarketEventSource, HistoricalMarketDataConnection,
    HistoricalMarketRequest, IntegrationError, MarketEvent, MarketStreamConnection,
    MarketSubscription, SubscriptionId,
};

pub struct MassiveLiveMarket {
    pub(super) inner: crate::services::participants::massive::market_data::MassiveMarketStream,
}

pub struct MassiveAsyncLiveMarket {
    pub(super) inner: crate::services::participants::massive::market_data::MassiveAsyncMarketStream,
}

pub struct MassiveHistoricalMarket {
    pub(super) inner:
        crate::services::participants::massive::market_data::MassiveHistoricalMarketData,
}

pub struct MassiveAsyncHistoricalMarket {
    pub(super) inner:
        crate::services::participants::massive::market_data::MassiveAsyncHistoricalMarketData,
}

impl AsyncMarketEventSource for MassiveAsyncLiveMarket {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.connect_channel().await
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.disconnect_channel().await
    }

    fn channel_health(&self) -> ConnectionHealth {
        self.inner.channel_health()
    }

    async fn subscribe(
        &mut self,
        request: MarketSubscription,
    ) -> Result<SubscriptionId, IntegrationError> {
        self.inner.subscribe(request).await
    }

    async fn unsubscribe(&mut self, subscription: SubscriptionId) -> Result<(), IntegrationError> {
        self.inner.unsubscribe(subscription).await
    }

    async fn next_market_event(&mut self) -> Result<MarketEvent, IntegrationError> {
        self.inner.next_market_event().await
    }
}

impl AsyncHistoricalMarketDataConnection for MassiveAsyncHistoricalMarket {
    fn capabilities(&self) -> MarketStreamCapabilities {
        self.inner.capabilities()
    }

    async fn fetch(
        &mut self,
        request: &HistoricalMarketRequest,
    ) -> Result<Vec<MarketEvent>, IntegrationError> {
        self.inner.fetch(request).await
    }
}

impl MarketStreamConnection for MassiveLiveMarket {
    fn descriptor(&self) -> &crate::domain::ConnectionDescriptor {
        self.inner.descriptor()
    }

    fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        reject_blocking_runtime()?;
        self.inner.connect_channel()
    }

    fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        reject_blocking_runtime()?;
        self.inner.disconnect_channel()
    }

    fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        reject_blocking_runtime()?;
        self.inner.reconnect_channel()
    }

    fn channel_health(&self) -> crate::domain::ConnectionHealth {
        self.inner.channel_health()
    }

    fn capabilities(&self) -> MarketStreamCapabilities {
        self.inner.capabilities()
    }

    fn subscribe(
        &mut self,
        request: MarketSubscription,
    ) -> Result<SubscriptionId, IntegrationError> {
        reject_blocking_runtime()?;
        self.inner.subscribe(request)
    }

    fn unsubscribe(&mut self, subscription: SubscriptionId) -> Result<(), IntegrationError> {
        reject_blocking_runtime()?;
        self.inner.unsubscribe(subscription)
    }

    fn next_event(&mut self) -> Result<Option<MarketEvent>, IntegrationError> {
        reject_blocking_runtime()?;
        self.inner.next_event()
    }
}

impl HistoricalMarketDataConnection for MassiveHistoricalMarket {
    fn capabilities(&self) -> MarketStreamCapabilities {
        self.inner.capabilities()
    }

    fn fetch(
        &mut self,
        request: &HistoricalMarketRequest,
    ) -> Result<Vec<MarketEvent>, IntegrationError> {
        reject_blocking_runtime()?;
        self.inner.fetch(request)
    }
}

pub(super) fn reject_blocking_runtime() -> Result<(), IntegrationError> {
    if tokio::runtime::Handle::try_current().is_ok() {
        Err(IntegrationError::InvalidRequest(
            "blocking Massive API cannot run on a Tokio runtime worker".into(),
        ))
    } else {
        Ok(())
    }
}
