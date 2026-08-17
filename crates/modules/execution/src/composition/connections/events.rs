use super::*;

/// Business-owned heterogeneous source collection. Integration keeps the
/// provider implementations concrete; Execution owns which sources form an
/// execution route.
pub enum ExecutionAsyncEventSource {
    BinanceSpot(kairos_integration::participants::binance::BinanceSpotOrderEvents),
    BinanceFutures(kairos_integration::participants::binance::BinanceFuturesOrderEvents),
    BinanceMargin(kairos_integration::participants::binance::BinanceMarginOrderEvents),
    BinanceOptions(kairos_integration::participants::binance::BinanceOptionsOrderEvents),
    Ibkr(kairos_integration::participants::ibkr::IbkrOrderEvents),
    OkxTrading(kairos_integration::participants::okx::OkxTradingOrderEvents),
}

impl AsyncOrderEventSource for ExecutionAsyncEventSource {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        match self {
            Self::BinanceSpot(source) => source.connect_channel().await,
            Self::BinanceFutures(source) => source.connect_channel().await,
            Self::BinanceMargin(source) => source.connect_channel().await,
            Self::BinanceOptions(source) => source.connect_channel().await,
            Self::Ibkr(source) => source.connect_channel().await,
            Self::OkxTrading(source) => source.connect_channel().await,
        }
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        match self {
            Self::BinanceSpot(source) => source.disconnect_channel().await,
            Self::BinanceFutures(source) => source.disconnect_channel().await,
            Self::BinanceMargin(source) => source.disconnect_channel().await,
            Self::BinanceOptions(source) => source.disconnect_channel().await,
            Self::Ibkr(source) => source.disconnect_channel().await,
            Self::OkxTrading(source) => source.disconnect_channel().await,
        }
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        match self {
            Self::BinanceSpot(source) => source.reconnect_channel().await,
            Self::BinanceFutures(source) => source.reconnect_channel().await,
            Self::BinanceMargin(source) => source.reconnect_channel().await,
            Self::BinanceOptions(source) => source.reconnect_channel().await,
            Self::Ibkr(source) => source.reconnect_channel().await,
            Self::OkxTrading(source) => source.reconnect_channel().await,
        }
    }

    fn channel_health(&self) -> ConnectionHealth {
        match self {
            Self::BinanceSpot(source) => source.channel_health(),
            Self::BinanceFutures(source) => source.channel_health(),
            Self::BinanceMargin(source) => source.channel_health(),
            Self::BinanceOptions(source) => source.channel_health(),
            Self::Ibkr(source) => source.channel_health(),
            Self::OkxTrading(source) => source.channel_health(),
        }
    }

    async fn next_order_event(
        &mut self,
    ) -> Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError> {
        match self {
            Self::BinanceSpot(source) => source.next_order_event().await,
            Self::BinanceFutures(source) => source.next_order_event().await,
            Self::BinanceMargin(source) => source.next_order_event().await,
            Self::BinanceOptions(source) => source.next_order_event().await,
            Self::Ibkr(source) => source.next_order_event().await,
            Self::OkxTrading(source) => source.next_order_event().await,
        }
    }
}

pub fn compose_execution_stream(
    _options: &ExecutionConnectionOptions,
) -> Result<Option<Box<dyn OrderEventSource>>, String> {
    Ok(None)
}
