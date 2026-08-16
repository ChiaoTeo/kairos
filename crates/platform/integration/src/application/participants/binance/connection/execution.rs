//! Execution capability handles projected from a Binance principal context.

use super::*;

pub struct BinanceSpotOrderEntry {
    pub(super) inner: BinanceSpotOrderConnection,
}

pub struct BinanceSpotOrderQuery {
    pub(super) inner: BinanceOrderQueryConnection,
}

pub struct BinanceSpotOrderEvents {
    pub(super) inner: BinanceSpotAsyncOrderEventSource,
}

pub struct BinanceFuturesOrderEntry {
    pub(super) inner: BinanceFuturesOrderConnection,
}

pub struct BinanceFuturesOrderQuery {
    pub(super) inner: BinanceOrderQueryConnection,
}

pub struct BinanceFuturesOrderEvents {
    pub(super) inner: BinanceFuturesAsyncOrderEventSource,
}

pub struct BinanceMarginOrderEntry {
    pub(super) inner:
        crate::services::participants::binance::margin_order::BinanceMarginOrderConnection,
}

pub struct BinanceMarginOrderQuery {
    pub(super) inner:
        crate::services::participants::binance::margin_query::BinanceMarginOrderQueryConnection,
}

pub struct BinanceMarginOrderEvents {
    pub(super) inner: crate::services::participants::binance::async_margin_order_events::BinanceAsyncMarginOrderEventSource,
}

pub struct BinanceOptionsOrderEntry {
    pub(super) inner:
        crate::services::participants::binance::options::order::BinanceOptionsOrderConnection,
}

pub struct BinanceOptionsOrderQuery {
    pub(super) inner: BinanceOrderQueryConnection,
}

pub struct BinanceOptionsOrderEvents {
    pub(super) inner: crate::services::participants::binance::options::async_order_events::BinanceOptionsAsyncOrderEventSource,
}

impl AsyncOrderEntryConnection for BinanceOptionsOrderEntry {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        self.inner.submit_order_async(request).await
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        self.inner
            .cancel_order_async(request, remote_order_id, at_unix_nanos)
            .await
    }
}

impl AsyncOrderQueryConnection for BinanceOptionsOrderQuery {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.inner.open_orders_async(query).await
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.inner.order_history_async(query).await
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        self.inner.order_detail_async(query).await
    }
}

impl AsyncOrderEventSource for BinanceOptionsOrderEvents {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.connect_channel().await
    }
    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.disconnect_channel().await
    }
    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.reconnect_channel().await
    }
    fn channel_health(&self) -> crate::domain::ConnectionHealth {
        self.inner.channel_health()
    }
    async fn next_order_event(
        &mut self,
    ) -> Result<
        crate::application::ExternalEventEnvelope<crate::application::ExternalExecutionEvent>,
        IntegrationError,
    > {
        self.inner.next_order_event().await
    }
}

impl AsyncOrderEntryConnection for BinanceMarginOrderEntry {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        self.inner.submit_order_async(request).await
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        _at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        self.inner
            .cancel_order_async(request, remote_order_id)
            .await
    }
}

impl AsyncOrderQueryConnection for BinanceMarginOrderQuery {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.inner.open_orders(query).await
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.inner.order_history(query).await
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        self.inner.order_detail(query).await
    }
}

impl AsyncOrderEventSource for BinanceMarginOrderEvents {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.connect_channel().await
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.disconnect_channel().await
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.reconnect_channel().await
    }

    fn channel_health(&self) -> crate::domain::ConnectionHealth {
        self.inner.channel_health()
    }

    async fn next_order_event(
        &mut self,
    ) -> Result<
        crate::application::ExternalEventEnvelope<crate::application::ExternalExecutionEvent>,
        IntegrationError,
    > {
        self.inner.next_order_event().await
    }
}

impl AsyncOrderEntryConnection for BinanceFuturesOrderEntry {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        self.inner.submit_order_async(request).await
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        self.inner
            .cancel_order_async(request, remote_order_id, at_unix_nanos)
            .await
    }
}

impl AsyncOrderQueryConnection for BinanceFuturesOrderQuery {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.inner.open_orders_async(query).await
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.inner.order_history_async(query).await
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        self.inner.order_detail_async(query).await
    }
}

impl AsyncOrderEventSource for BinanceFuturesOrderEvents {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.connect_channel().await
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.disconnect_channel().await
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.reconnect_channel().await
    }

    fn channel_health(&self) -> crate::domain::ConnectionHealth {
        self.inner.channel_health()
    }

    async fn next_order_event(
        &mut self,
    ) -> Result<
        crate::application::ExternalEventEnvelope<crate::application::ExternalExecutionEvent>,
        IntegrationError,
    > {
        self.inner.next_order_event().await
    }
}

impl AsyncOrderEntryConnection for BinanceSpotOrderEntry {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        self.inner.submit_order_async(request).await
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        self.inner
            .cancel_order_async(request, remote_order_id, at_unix_nanos)
            .await
    }
}

impl AsyncOrderQueryConnection for BinanceSpotOrderQuery {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.inner.open_orders_async(query).await
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.inner.order_history_async(query).await
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        self.inner.order_detail_async(query).await
    }
}

impl AsyncOrderEventSource for BinanceSpotOrderEvents {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.connect_channel().await
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.disconnect_channel().await
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.reconnect_channel().await
    }

    fn channel_health(&self) -> crate::domain::ConnectionHealth {
        self.inner.channel_health()
    }

    async fn next_order_event(
        &mut self,
    ) -> Result<
        crate::application::ExternalEventEnvelope<crate::application::ExternalExecutionEvent>,
        IntegrationError,
    > {
        self.inner.next_order_event().await
    }
}
