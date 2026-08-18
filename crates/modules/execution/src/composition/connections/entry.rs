use super::*;

/// Business-owned heterogeneous collection of concrete Integration order
/// entry capabilities. This enum only selects providers; it does not redefine
/// or narrow the Integration contract.
pub enum ExecutionAsyncOrderEntry {
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

impl OrderCommand for ExecutionAsyncOrderEntry {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => OrderCommand::submit_order(connection, request).await,
            Self::BinanceUsdM(connection) => OrderCommand::submit_order(connection, request).await,
            Self::BinanceCoinM(connection) => OrderCommand::submit_order(connection, request).await,
            Self::BinanceMargin(connection) => {
                OrderCommand::submit_order(connection, request).await
            }
            Self::BinanceOptions(connection) => {
                OrderCommand::submit_order(connection, request).await
            }
            Self::BinanceStocks(connection) => {
                OrderCommand::submit_order(connection, request).await
            }
            Self::Ibkr(connection) => OrderCommand::submit_order(connection, request).await,
            Self::OkxTrading(connection) => OrderCommand::submit_order(connection, request).await,
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
                OrderCommand::cancel_order(connection, request, remote_order_id, at_unix_nanos)
                    .await
            }
            Self::BinanceUsdM(connection) => {
                OrderCommand::cancel_order(connection, request, remote_order_id, at_unix_nanos)
                    .await
            }
            Self::BinanceCoinM(connection) => {
                OrderCommand::cancel_order(connection, request, remote_order_id, at_unix_nanos)
                    .await
            }
            Self::BinanceMargin(connection) => {
                OrderCommand::cancel_order(connection, request, remote_order_id, at_unix_nanos)
                    .await
            }
            Self::BinanceOptions(connection) => {
                OrderCommand::cancel_order(connection, request, remote_order_id, at_unix_nanos)
                    .await
            }
            Self::BinanceStocks(connection) => {
                OrderCommand::cancel_order(connection, request, remote_order_id, at_unix_nanos)
                    .await
            }
            Self::Ibkr(connection) => {
                OrderCommand::cancel_order(connection, request, remote_order_id, at_unix_nanos)
                    .await
            }
            Self::OkxTrading(connection) => {
                OrderCommand::cancel_order(connection, request, remote_order_id, at_unix_nanos)
                    .await
            }
        }
    }
}

pub fn compose_order_entry(
    options: &ExecutionConnectionOptions,
) -> Result<Box<dyn BlockingOrderCommand>, String> {
    let provider = options.participant_id.trim().to_ascii_lowercase();
    if matches!(provider.as_str(), "simulated" | "paper") {
        return Ok(Box::new(SimulatedOrderEntry::default()));
    }
    Err(format!(
        "{provider} live execution is async-only; compose its concrete OrderCommand connection"
    ))
}

impl BlockingOrderCommand for SimulatedOrderEntry {
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
