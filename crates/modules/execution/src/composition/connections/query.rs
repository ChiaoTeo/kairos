use super::*;

/// Business-owned provider selection for Integration's async query
/// capability. Provider-specific details remain on the concrete connection.
pub enum ExecutionAsyncOrderQuery {
    BinanceSpot(kairos_integration::participants::binance::spot::BinanceSpotRestConnection),
    BinanceUsdM(kairos_integration::participants::binance::usdm::BinanceUsdMRestConnection),
    BinanceCoinM(kairos_integration::participants::binance::coinm::BinanceCoinMRestConnection),
    BinanceMargin(kairos_integration::participants::binance::margin::BinanceMarginRestConnection),
    BinanceOptions(
        kairos_integration::participants::binance::options::BinanceOptionsRestConnection,
    ),
    BinanceStocks(
        kairos_integration::participants::binance::advanced::stocks::BinanceStocksRestConnection,
    ),
    Ibkr(kairos_integration::participants::ibkr::IbkrOrderConnection),
    OkxTrading(kairos_integration::participants::okx::private::OkxPrivateRestConnection),
}

impl OrderQuery for ExecutionAsyncOrderQuery {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => OrderQuery::open_orders(connection, query).await,
            Self::BinanceUsdM(connection) => OrderQuery::open_orders(connection, query).await,
            Self::BinanceCoinM(connection) => OrderQuery::open_orders(connection, query).await,
            Self::BinanceMargin(connection) => OrderQuery::open_orders(connection, query).await,
            Self::BinanceOptions(connection) => OrderQuery::open_orders(connection, query).await,
            Self::BinanceStocks(connection) => OrderQuery::open_orders(connection, query).await,
            Self::Ibkr(connection) => OrderQuery::open_orders(connection, query).await,
            Self::OkxTrading(connection) => OrderQuery::open_orders(connection, query).await,
        }
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => OrderQuery::order_history(connection, query).await,
            Self::BinanceUsdM(connection) => OrderQuery::order_history(connection, query).await,
            Self::BinanceCoinM(connection) => OrderQuery::order_history(connection, query).await,
            Self::BinanceMargin(connection) => OrderQuery::order_history(connection, query).await,
            Self::BinanceOptions(connection) => OrderQuery::order_history(connection, query).await,
            Self::BinanceStocks(connection) => OrderQuery::order_history(connection, query).await,
            Self::Ibkr(connection) => OrderQuery::order_history(connection, query).await,
            Self::OkxTrading(connection) => OrderQuery::order_history(connection, query).await,
        }
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => OrderQuery::order_detail(connection, query).await,
            Self::BinanceUsdM(connection) => OrderQuery::order_detail(connection, query).await,
            Self::BinanceCoinM(connection) => OrderQuery::order_detail(connection, query).await,
            Self::BinanceMargin(connection) => OrderQuery::order_detail(connection, query).await,
            Self::BinanceOptions(connection) => OrderQuery::order_detail(connection, query).await,
            Self::BinanceStocks(connection) => OrderQuery::order_detail(connection, query).await,
            Self::Ibkr(connection) => OrderQuery::order_detail(connection, query).await,
            Self::OkxTrading(connection) => OrderQuery::order_detail(connection, query).await,
        }
    }
}
