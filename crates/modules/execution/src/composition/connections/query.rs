use super::*;

/// Business-owned provider selection for Integration's async query
/// capability. Provider-specific details remain on the concrete connection.
pub enum ExecutionAsyncOrderQuery {
    BinanceSpot(kairos_integration::participants::binance::BinanceSpotOrderQuery),
    BinanceFutures(kairos_integration::participants::binance::BinanceFuturesOrderQuery),
    BinanceMargin(kairos_integration::participants::binance::BinanceMarginOrderQuery),
    BinanceOptions(kairos_integration::participants::binance::BinanceOptionsOrderQuery),
    Ibkr(kairos_integration::participants::ibkr::IbkrOrderQuery),
    OkxTrading(kairos_integration::participants::okx::OkxTradingOrderQuery),
}

pub struct ExecutionAsyncOrderQueryRoutes {
    pub(in crate::composition) inner: RoutedAsyncOrderQuery<ExecutionAsyncOrderQuery>,
}

impl AsyncOrderQueryConnection for ExecutionAsyncOrderQueryRoutes {
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

impl AsyncOrderQueryConnection for ExecutionAsyncOrderQuery {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => {
                AsyncOrderQueryConnection::open_orders(connection, query).await
            }
            Self::BinanceFutures(connection) => {
                AsyncOrderQueryConnection::open_orders(connection, query).await
            }
            Self::BinanceMargin(connection) => {
                AsyncOrderQueryConnection::open_orders(connection, query).await
            }
            Self::BinanceOptions(connection) => {
                AsyncOrderQueryConnection::open_orders(connection, query).await
            }
            Self::Ibkr(connection) => {
                AsyncOrderQueryConnection::open_orders(connection, query).await
            }
            Self::OkxTrading(connection) => {
                AsyncOrderQueryConnection::open_orders(connection, query).await
            }
        }
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => {
                AsyncOrderQueryConnection::order_history(connection, query).await
            }
            Self::BinanceFutures(connection) => {
                AsyncOrderQueryConnection::order_history(connection, query).await
            }
            Self::BinanceMargin(connection) => {
                AsyncOrderQueryConnection::order_history(connection, query).await
            }
            Self::BinanceOptions(connection) => {
                AsyncOrderQueryConnection::order_history(connection, query).await
            }
            Self::Ibkr(connection) => {
                AsyncOrderQueryConnection::order_history(connection, query).await
            }
            Self::OkxTrading(connection) => {
                AsyncOrderQueryConnection::order_history(connection, query).await
            }
        }
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => {
                AsyncOrderQueryConnection::order_detail(connection, query).await
            }
            Self::BinanceFutures(connection) => {
                AsyncOrderQueryConnection::order_detail(connection, query).await
            }
            Self::BinanceMargin(connection) => {
                AsyncOrderQueryConnection::order_detail(connection, query).await
            }
            Self::BinanceOptions(connection) => {
                AsyncOrderQueryConnection::order_detail(connection, query).await
            }
            Self::Ibkr(connection) => {
                AsyncOrderQueryConnection::order_detail(connection, query).await
            }
            Self::OkxTrading(connection) => {
                AsyncOrderQueryConnection::order_detail(connection, query).await
            }
        }
    }
}

pub fn compose_order_query(
    options: &ExecutionConnectionOptions,
) -> Result<Option<Box<dyn OrderQueryConnection>>, String> {
    let provider = options.participant_id.trim().to_ascii_lowercase();
    let product = options.product.trim().to_ascii_lowercase();
    if provider == "binance" && product == "spot" {
        return Ok(Some(Box::new(
            binance_spot_private_connection(options)?
                .blocking_spot_order_query()
                .map_err(|error| error.to_string())?,
        )));
    }
    if provider == "okx" || provider == "okex" {
        let (instrument_type, _) = okx_trading_shape(&product, options.trading_mode.as_deref())?;
        return Ok(Some(Box::new(
            okx_private_connection(options)?.blocking_trading_order_query(instrument_type),
        )));
    }
    let product_family = match product.as_str() {
        "equity" | "stocks" => return Ok(None),
        "spot" => BinanceConnectionDomain::Spot,
        "usd-m-futures" => BinanceConnectionDomain::UsdMFutures,
        "coin-m-futures" => BinanceConnectionDomain::CoinMFutures,
        "options" => BinanceConnectionDomain::Options,
        _ => return Ok(None),
    };
    if provider == "binance" {
        binance::blocking::order_query(
            product_family,
            options.api_key.expose_secret().to_owned(),
            options.secret.expose_secret().to_owned(),
            options.base_url.clone(),
        )
        .map(Some)
        .map_err(|error| error.to_string())
    } else {
        Ok(None)
    }
}
