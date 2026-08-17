use super::*;

/// Business-owned heterogeneous collection of concrete Integration order
/// entry capabilities. This enum only selects providers; it does not redefine
/// or narrow the Integration contract.
pub enum ExecutionAsyncOrderEntry {
    BinanceSpot(kairos_integration::participants::binance::BinanceSpotOrderEntry),
    BinanceFutures(kairos_integration::participants::binance::BinanceFuturesOrderEntry),
    BinanceMargin(kairos_integration::participants::binance::BinanceMarginOrderEntry),
    BinanceOptions(kairos_integration::participants::binance::BinanceOptionsOrderEntry),
    Ibkr(kairos_integration::participants::ibkr::IbkrOrderEntry),
    OkxTrading(kairos_integration::participants::okx::OkxTradingOrderEntry),
}

/// Execution-owned route collection over concrete Integration capabilities.
/// The wrapper is public only so the binary can transfer it into the process;
/// its route table and provider values remain private.
pub struct ExecutionAsyncOrderEntryRoutes {
    pub(in crate::composition) inner: RoutedAsyncOrderEntry<ExecutionAsyncOrderEntry>,
    pub(in crate::composition) writer_fences: Vec<ExecutionWriterFence>,
}

impl AsyncOrderEntryConnection for ExecutionAsyncOrderEntryRoutes {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        self.validate_writer(request)?;
        self.inner.submit_order(request).await
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        self.validate_writer(request)?;
        self.inner
            .cancel_order(request, remote_order_id, at_unix_nanos)
            .await
    }
}

impl AsyncOrderEntryConnection for ExecutionAsyncOrderEntry {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => {
                AsyncOrderEntryConnection::submit_order(connection, request).await
            }
            Self::BinanceFutures(connection) => {
                AsyncOrderEntryConnection::submit_order(connection, request).await
            }
            Self::BinanceMargin(connection) => {
                AsyncOrderEntryConnection::submit_order(connection, request).await
            }
            Self::BinanceOptions(connection) => {
                AsyncOrderEntryConnection::submit_order(connection, request).await
            }
            Self::Ibkr(connection) => {
                AsyncOrderEntryConnection::submit_order(connection, request).await
            }
            Self::OkxTrading(connection) => {
                AsyncOrderEntryConnection::submit_order(connection, request).await
            }
        }
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => {
                AsyncOrderEntryConnection::cancel_order(
                    connection,
                    request,
                    remote_order_id,
                    at_unix_nanos,
                )
                .await
            }
            Self::BinanceFutures(connection) => {
                AsyncOrderEntryConnection::cancel_order(
                    connection,
                    request,
                    remote_order_id,
                    at_unix_nanos,
                )
                .await
            }
            Self::BinanceMargin(connection) => {
                AsyncOrderEntryConnection::cancel_order(
                    connection,
                    request,
                    remote_order_id,
                    at_unix_nanos,
                )
                .await
            }
            Self::BinanceOptions(connection) => {
                AsyncOrderEntryConnection::cancel_order(
                    connection,
                    request,
                    remote_order_id,
                    at_unix_nanos,
                )
                .await
            }
            Self::Ibkr(connection) => {
                AsyncOrderEntryConnection::cancel_order(
                    connection,
                    request,
                    remote_order_id,
                    at_unix_nanos,
                )
                .await
            }
            Self::OkxTrading(connection) => {
                AsyncOrderEntryConnection::cancel_order(
                    connection,
                    request,
                    remote_order_id,
                    at_unix_nanos,
                )
                .await
            }
        }
    }
}

pub fn compose_order_entry(
    options: &ExecutionConnectionOptions,
) -> Result<Box<dyn OrderEntryConnection>, String> {
    let provider = options.participant_id.trim().to_ascii_lowercase();
    if matches!(provider.as_str(), "simulated" | "paper") {
        return Ok(Box::new(SimulatedOrderEntry::default()));
    }
    let product_name = options.product.trim().to_ascii_lowercase();
    if provider == "binance" && product_name == "spot" {
        return Ok(Box::new(
            binance_spot_private_connection(options)?
                .blocking_spot_order_entry()
                .map_err(|error| error.to_string())?,
        ));
    }
    if provider == "okx" || provider == "okex" {
        let (instrument_type, trading_mode) =
            okx_trading_shape(&product_name, options.trading_mode.as_deref())?;
        return Ok(Box::new(
            okx_private_connection(options)?
                .blocking_trading_order_entry(instrument_type, trading_mode)
                .map_err(|error| error.to_string())?,
        ));
    }
    match provider.as_str() {
        "binance" => match product_name.as_str() {
            "equity" | "stocks" => Err(
                "Binance does not publish a supported Equity/Stocks execution API; use a broker route such as IBKR"
                    .into(),
            ),
            "cross-margin" => Err(
                "Binance Cross Margin is async-only; use production/direct async composition"
                    .into(),
            ),
            "isolated-margin" => Err(
                "Binance Isolated Margin is async-only; use production/direct async composition"
                    .into(),
            ),
            "options" => {
                Err("Binance Options is async-only; use production/direct async composition".into())
            }
            "usd-m-futures" => Err(
                "Binance USD-M Futures is async-only; use production/direct async composition"
                    .into(),
            ),
            "coin-m-futures" => Err(
                "Binance COIN-M Futures is async-only; use production/direct async composition"
                    .into(),
            ),
            _ => {
                return Err(format!(
                    "unsupported Binance execution product: {product_name}"
                ))
            }
        },
        _ => Err(format!("unsupported execution provider: {provider}")),
    }
}

impl OrderEntryConnection for SimulatedOrderEntry {
    fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        Ok(CommandOutcome::Confirmed(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Accepted,
            remote_order_id: kairos_primitives::RemoteOrderId::new(format!(
                "simulated:{}",
                request.order_id
            ))
            .ok(),
            filled_quantity: Some(DecimalValue::new(0, request.quantity.scale)),
            occurred_at_unix_nanos: now_nanos().into(),
            reason: String::new(),
        }))
    }
    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        Ok(CommandOutcome::Confirmed(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Canceled,
            remote_order_id: kairos_primitives::RemoteOrderId::new(remote_order_id).ok(),
            filled_quantity: None,
            occurred_at_unix_nanos: at_unix_nanos.into(),
            reason: String::new(),
        }))
    }
}

fn now_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}
