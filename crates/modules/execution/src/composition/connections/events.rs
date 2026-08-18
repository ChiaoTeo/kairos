use super::*;

/// Business-owned heterogeneous source collection. Integration keeps the
/// provider implementations concrete; Execution owns which sources form an
/// execution route.
pub enum ExecutionAsyncEventSource {
    BinanceSpot(
        kairos_integration::participants::binance::spot::BinanceSpotUserWebSocketConnection,
    ),
    BinanceUsdM(
        kairos_integration::participants::binance::usdm::BinanceUsdMUserWebSocketConnection,
    ),
    BinanceCoinM(
        kairos_integration::participants::binance::coinm::BinanceCoinMUserWebSocketConnection,
    ),
    BinanceMargin(
        kairos_integration::participants::binance::margin::BinanceMarginUserWebSocketConnection,
    ),
    BinanceOptions(
        kairos_integration::participants::binance::options::BinanceOptionsUserWebSocketConnection,
    ),
    BinanceStocks(
        kairos_integration::participants::binance::advanced::stocks::BinanceStocksUserWebSocketConnection,
    ),
    Ibkr(kairos_integration::participants::ibkr::IbkrExecutionStreamConnection),
    OkxTrading(kairos_integration::participants::okx::private::OkxPrivateWebSocketConnection),
}

impl ExecutionStream for ExecutionAsyncEventSource {
    async fn next(
        &mut self,
    ) -> Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError> {
        match self {
            Self::BinanceSpot(source) => ExecutionStream::next(source).await,
            Self::BinanceUsdM(source) => ExecutionStream::next(source).await,
            Self::BinanceCoinM(source) => ExecutionStream::next(source).await,
            Self::BinanceMargin(source) => ExecutionStream::next(source).await,
            Self::BinanceOptions(source) => ExecutionStream::next(source).await,
            Self::BinanceStocks(source) => ExecutionStream::next(source).await,
            Self::Ibkr(source) => ExecutionStream::next(source).await,
            Self::OkxTrading(source) => ExecutionStream::next(source).await,
        }
    }
}

macro_rules! delegate_source {
    ($self:ident, $method:ident) => {
        match $self {
            Self::BinanceSpot(source) => {
                kairos_integration::ConnectionLifecycleCommand::$method(source).await
            }
            Self::BinanceUsdM(source) => {
                kairos_integration::ConnectionLifecycleCommand::$method(source).await
            }
            Self::BinanceCoinM(source) => {
                kairos_integration::ConnectionLifecycleCommand::$method(source).await
            }
            Self::BinanceMargin(source) => {
                kairos_integration::ConnectionLifecycleCommand::$method(source).await
            }
            Self::BinanceOptions(source) => {
                kairos_integration::ConnectionLifecycleCommand::$method(source).await
            }
            Self::BinanceStocks(source) => {
                kairos_integration::ConnectionLifecycleCommand::$method(source).await
            }
            Self::Ibkr(source) => {
                kairos_integration::ConnectionLifecycleCommand::$method(source).await
            }
            Self::OkxTrading(source) => {
                kairos_integration::ConnectionLifecycleCommand::$method(source).await
            }
        }
    };
}

impl kairos_integration::ConnectionLifecycleCommand for ExecutionAsyncEventSource {
    async fn connect(&mut self) -> Result<(), IntegrationError> {
        delegate_source!(self, connect)
    }
    async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        delegate_source!(self, disconnect)
    }
    async fn reconnect(&mut self) -> Result<(), IntegrationError> {
        delegate_source!(self, reconnect)
    }
}

impl kairos_integration::ConnectionHealthQuery for ExecutionAsyncEventSource {
    fn connection_health(&mut self) -> ConnectionHealth {
        match self {
            Self::BinanceSpot(source) => {
                kairos_integration::ConnectionHealthQuery::connection_health(source)
            }
            Self::BinanceUsdM(source) => {
                kairos_integration::ConnectionHealthQuery::connection_health(source)
            }
            Self::BinanceCoinM(source) => {
                kairos_integration::ConnectionHealthQuery::connection_health(source)
            }
            Self::BinanceMargin(source) => {
                kairos_integration::ConnectionHealthQuery::connection_health(source)
            }
            Self::BinanceOptions(source) => {
                kairos_integration::ConnectionHealthQuery::connection_health(source)
            }
            Self::BinanceStocks(source) => {
                kairos_integration::ConnectionHealthQuery::connection_health(source)
            }
            Self::Ibkr(source) => {
                kairos_integration::ConnectionHealthQuery::connection_health(source)
            }
            Self::OkxTrading(source) => {
                kairos_integration::ConnectionHealthQuery::connection_health(source)
            }
        }
    }
}
