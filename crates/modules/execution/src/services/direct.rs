//! Short-lived provider connections used only by the standalone CLI facade.

use kairos_conflux::{
    BinanceCoinMRestConnection, BinanceMarginRestConnection, BinanceOptionsRestConnection,
    BinanceRestConfig, BinanceSpotRestConnection, BinanceStocksRestConnection,
    BinanceUsdMRestConnection, ExternalOrder, ExternalOrderQuery, IbkrOrderConfig,
    IbkrOrderConnection, IntegrationError, OkxPrivateRestConfig, OkxPrivateRestConnection,
    OkxRestConfig, OrderCommand, OrderEntryEvent, OrderEntryOptions, OrderEntryRequest, OrderQuery,
    ParticipantInstrumentRef, ParticipantInstrumentTypeRef, ParticipantKind, ParticipantRef,
    TimeInForce,
};
use kairos_primitives::account::{AccountId, SegmentKey};
use kairos_primitives::execution::{OrderId, OrderSide as PrimitiveOrderSide};
use kairos_primitives::reference::{InstrumentId, Symbol};

use crate::domain::{OrderSide, OrderType, SubmitOrder};

#[derive(Clone, Debug)]
pub(crate) struct DirectFill {
    pub(crate) fill_id: String,
    pub(crate) remote_order_id: String,
    pub(crate) symbol: String,
    pub(crate) side: String,
    pub(crate) price: String,
    pub(crate) quantity: String,
    pub(crate) realized_pnl: Option<String>,
    pub(crate) fee: Option<String>,
    pub(crate) fee_currency: Option<String>,
    pub(crate) executed_at_unix_nanos: u64,
}

#[derive(Clone, Debug)]
pub(crate) struct DirectOrder {
    pub(crate) order_id: String,
    pub(crate) remote_order_id: String,
    pub(crate) client_order_id: Option<String>,
    pub(crate) symbol: String,
    pub(crate) side: String,
    pub(crate) order_type: String,
    pub(crate) status: String,
    pub(crate) quantity: String,
    pub(crate) filled_quantity: String,
    pub(crate) average_fill_price: Option<String>,
    pub(crate) occurred_at_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug)]
pub(crate) enum DirectCommandOutcome {
    Confirmed {
        order_id: String,
        remote_order_id: Option<String>,
        order_status: String,
        filled_quantity: Option<String>,
        occurred_at_unix_nanos: u64,
        reason: String,
    },
    Rejected {
        code: Option<String>,
        message: String,
        participant_request_id: Option<String>,
    },
    Indeterminate {
        message: String,
        participant_request_id: Option<String>,
    },
}

/// Private provider gateway for one short-lived Execution CLI session.
///
/// It owns all Conflux/provider vocabulary. The application facade supplies
/// Execution commands and receives package-private normalized results.
pub(crate) struct DirectExecutionGateway {
    connection: DirectOrderConnection,
    provider: String,
    execution_channel: String,
    account_id: AccountId,
    segment_key: SegmentKey,
    trading_mode: Option<String>,
}

impl DirectExecutionGateway {
    pub(crate) fn new(
        connection: DirectOrderConnection,
        provider: String,
        execution_channel: String,
        account_id: AccountId,
        segment_key: SegmentKey,
        trading_mode: Option<String>,
    ) -> Self {
        Self {
            connection,
            provider,
            execution_channel,
            account_id,
            segment_key,
            trading_mode,
        }
    }

    pub(crate) async fn open_orders(
        &mut self,
        symbol: Option<&str>,
        limit: Option<u32>,
    ) -> Result<Vec<DirectOrder>, IntegrationError> {
        let query = self.query(symbol, None, limit)?;
        self.connection
            .open_orders(&query)
            .await
            .map(|values| values.iter().map(normalize_order).collect())
    }

    pub(crate) async fn history(
        &mut self,
        symbol: Option<&str>,
        limit: Option<u32>,
    ) -> Result<Vec<DirectOrder>, IntegrationError> {
        let query = self.query(symbol, None, limit)?;
        self.connection
            .history(&query)
            .await
            .map(|values| values.iter().map(normalize_order).collect())
    }

    pub(crate) async fn order(
        &mut self,
        order_id: &str,
        symbol: Option<&str>,
    ) -> Result<DirectOrder, IntegrationError> {
        self.find_order(order_id, symbol)
            .await
            .map(|value| normalize_order(&value))
    }

    pub(crate) async fn submit(
        &mut self,
        request: &SubmitOrder,
        symbol: Option<&str>,
    ) -> Result<DirectCommandOutcome, IntegrationError> {
        let request = self.provider_request(request, symbol)?;
        self.connection
            .submit(&request)
            .await
            .map(normalize_outcome)
    }

    pub(crate) async fn cancel(
        &mut self,
        order_id: &str,
        symbol: Option<&str>,
    ) -> Result<DirectCommandOutcome, IntegrationError> {
        let order = self.find_order(order_id, symbol).await?;
        let request = self.request_from_external(&order)?;
        self.connection
            .cancel(&request, order.remote_order_id.as_str(), now_unix_nanos())
            .await
            .map(normalize_outcome)
    }

    pub(crate) async fn fills(
        &mut self,
        symbol: Option<&str>,
        order_id: Option<&str>,
        limit: Option<u16>,
    ) -> Result<Vec<DirectFill>, IntegrationError> {
        self.connection.fills(symbol, order_id, limit).await
    }

    fn query(
        &self,
        symbol: Option<&str>,
        order_id: Option<&str>,
        limit: Option<u32>,
    ) -> Result<ExternalOrderQuery, IntegrationError> {
        Ok(ExternalOrderQuery {
            symbol: symbol
                .map(Symbol::new)
                .transpose()
                .map_err(invalid_request)?,
            instrument_type: Some(
                ParticipantInstrumentTypeRef::new(self.execution_channel.clone())
                    .map_err(invalid_request)?,
            ),
            order_id: order_id
                .map(OrderId::new)
                .transpose()
                .map_err(invalid_request)?,
            limit,
            since_unix_nanos: None,
        })
    }

    async fn find_order(
        &mut self,
        id: &str,
        symbol: Option<&str>,
    ) -> Result<ExternalOrder, IntegrationError> {
        let query = self.query(symbol, None, Some(100))?;
        let matches = |order: &ExternalOrder| {
            order.order_id.as_str() == id
                || order.remote_order_id.as_str() == id
                || order
                    .client_order_id
                    .as_ref()
                    .is_some_and(|value| value.as_str() == id)
        };
        if let Some(order) = self
            .connection
            .open_orders(&query)
            .await?
            .into_iter()
            .find(matches)
        {
            return Ok(order);
        }
        if symbol.is_some() {
            if let Some(order) = self
                .connection
                .history(&query)
                .await?
                .into_iter()
                .find(matches)
            {
                return Ok(order);
            }
        }
        let query = self.query(symbol, Some(id), Some(1))?;
        self.connection.order(&query).await?.ok_or_else(|| {
            IntegrationError::InvalidRequest(format!("provider order not found: {id}"))
        })
    }

    fn provider_request(
        &self,
        request: &SubmitOrder,
        symbol: Option<&str>,
    ) -> Result<OrderEntryRequest, IntegrationError> {
        let source_symbol = symbol.unwrap_or(request.instrument_id.as_str());
        let mut options = provider_options(&request.options)?;
        if options.wallet_type.is_none() {
            options.wallet_type = self.trading_mode.clone();
        }
        Ok(OrderEntryRequest {
            order_id: request.order_id.clone(),
            intent_id: request.intent_id.clone(),
            submitted_at_unix_nanos: request
                .submitted_at_unix_nanos
                .unwrap_or_else(|| now_unix_nanos().into()),
            account_id: request.account_id.clone(),
            segment_key: request.segment_key.clone(),
            instrument_id: request.instrument_id.clone(),
            market_id: request.market_id.clone(),
            participant_instrument: self.participant_instrument(source_symbol)?,
            side: match request.side {
                OrderSide::Buy => PrimitiveOrderSide::Buy,
                OrderSide::Sell => PrimitiveOrderSide::Sell,
            },
            quantity: kairos_conflux::DecimalValue::new(
                request.quantity.mantissa(),
                request.quantity.scale(),
            ),
            order_type: match request.order_type {
                OrderType::Market => kairos_conflux::OrderType::Market,
                OrderType::Limit => kairos_conflux::OrderType::Limit,
            },
            limit_price: request
                .limit_price
                .map(|value| kairos_conflux::DecimalValue::new(value.mantissa(), value.scale())),
            options,
        })
    }

    fn request_from_external(
        &self,
        order: &ExternalOrder,
    ) -> Result<OrderEntryRequest, IntegrationError> {
        Ok(OrderEntryRequest {
            order_id: order.order_id.clone(),
            intent_id: None,
            submitted_at_unix_nanos: order
                .occurred_at_unix_nanos
                .unwrap_or_else(|| now_unix_nanos().into()),
            account_id: self.account_id.clone(),
            segment_key: self.segment_key.clone(),
            instrument_id: InstrumentId::new(order.symbol.to_string()).map_err(invalid_request)?,
            market_id: None,
            participant_instrument: self.participant_instrument(order.symbol.as_str())?,
            side: order.side,
            quantity: order.quantity,
            order_type: order.order_type,
            limit_price: order.average_fill_price,
            options: OrderEntryOptions::default(),
        })
    }

    fn participant_instrument(
        &self,
        symbol: &str,
    ) -> Result<ParticipantInstrumentRef, IntegrationError> {
        ParticipantInstrumentRef::new(
            ParticipantRef::new(
                if self.provider.eq_ignore_ascii_case("ibkr") {
                    ParticipantKind::Broker
                } else {
                    ParticipantKind::Exchange
                },
                self.provider.clone(),
            )
            .map_err(invalid_request)?,
            Some(
                ParticipantInstrumentTypeRef::new(self.execution_channel.clone())
                    .map_err(invalid_request)?,
            ),
            symbol,
        )
        .map_err(invalid_request)
    }
}

fn provider_options(
    options: &crate::domain::ExecutionOrderOptions,
) -> Result<OrderEntryOptions, IntegrationError> {
    Ok(OrderEntryOptions {
        time_in_force: options
            .time_in_force
            .as_deref()
            .map(parse_time_in_force)
            .transpose()?,
        reduce_only: options.reduce_only,
        post_only: options.post_only,
        position_side: options.position_side.clone(),
        quote_asset: options.quote_asset.clone(),
        wallet_type: options.wallet_type.clone(),
        trading_session: options.trading_session.clone(),
        tokenize: options.tokenize,
    })
}

fn parse_time_in_force(value: &str) -> Result<TimeInForce, IntegrationError> {
    match value.trim().to_ascii_lowercase().replace('_', "-").as_str() {
        "gtc" | "good-til-canceled" => Ok(TimeInForce::GoodTilCanceled),
        "ioc" | "immediate-or-cancel" => Ok(TimeInForce::ImmediateOrCancel),
        "fok" | "fill-or-kill" => Ok(TimeInForce::FillOrKill),
        "day" => Ok(TimeInForce::Day),
        value => Err(IntegrationError::InvalidRequest(format!(
            "unsupported time in force: {value}"
        ))),
    }
}

fn normalize_order(order: &ExternalOrder) -> DirectOrder {
    DirectOrder {
        order_id: order.order_id.to_string(),
        remote_order_id: order.remote_order_id.to_string(),
        client_order_id: order.client_order_id.as_ref().map(ToString::to_string),
        symbol: order.symbol.to_string(),
        side: format!("{:?}", order.side).to_ascii_lowercase(),
        order_type: format!("{:?}", order.order_type).to_ascii_lowercase(),
        status: format!("{:?}", order.status).to_ascii_lowercase(),
        quantity: decimal(order.quantity),
        filled_quantity: decimal(order.filled_quantity),
        average_fill_price: order.average_fill_price.map(decimal),
        occurred_at_unix_nanos: order.occurred_at_unix_nanos.map(|value| value.get()),
    }
}

fn normalize_outcome(
    outcome: kairos_conflux::CommandOutcome<OrderEntryEvent>,
) -> DirectCommandOutcome {
    match outcome {
        kairos_conflux::CommandOutcome::Confirmed(event) => DirectCommandOutcome::Confirmed {
            order_id: event.order_id.to_string(),
            remote_order_id: event.remote_order_id.map(|value| value.to_string()),
            order_status: format!("{:?}", event.status).to_ascii_lowercase(),
            filled_quantity: event.filled_quantity.map(decimal),
            occurred_at_unix_nanos: event.occurred_at_unix_nanos.get(),
            reason: event.reason,
        },
        kairos_conflux::CommandOutcome::Rejected(value) => DirectCommandOutcome::Rejected {
            code: value.code,
            message: value.message,
            participant_request_id: value.participant_request_id,
        },
        kairos_conflux::CommandOutcome::Indeterminate(value) => {
            DirectCommandOutcome::Indeterminate {
                message: value.message,
                participant_request_id: value.participant_request_id,
            }
        },
    }
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u64::MAX as u128) as u64
}

fn invalid_request(error: impl std::fmt::Display) -> IntegrationError {
    IntegrationError::InvalidRequest(error.to_string())
}

pub(crate) enum DirectOrderConnection {
    BinanceSpot(BinanceSpotRestConnection),
    BinanceMargin(BinanceMarginRestConnection),
    BinanceUsdM(BinanceUsdMRestConnection),
    BinanceCoinM(BinanceCoinMRestConnection),
    BinanceOptions(BinanceOptionsRestConnection),
    BinanceStocks(BinanceStocksRestConnection),
    Okx(OkxPrivateRestConnection),
    Ibkr(IbkrOrderConnection),
}

impl DirectOrderConnection {
    pub async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        match self {
            Self::BinanceSpot(value) => value.open_orders(query).await,
            Self::BinanceMargin(value) => value.open_orders(query).await,
            Self::BinanceUsdM(value) => value.open_orders(query).await,
            Self::BinanceCoinM(value) => value.open_orders(query).await,
            Self::BinanceOptions(value) => value.open_orders(query).await,
            Self::BinanceStocks(value) => value.open_orders(query).await,
            Self::Okx(value) => value.open_orders(query).await,
            Self::Ibkr(value) => value.open_orders(query).await,
        }
    }

    pub async fn history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        match self {
            Self::BinanceSpot(value) => value.order_history(query).await,
            Self::BinanceMargin(value) => value.order_history(query).await,
            Self::BinanceUsdM(value) => value.order_history(query).await,
            Self::BinanceCoinM(value) => value.order_history(query).await,
            Self::BinanceOptions(value) => value.order_history(query).await,
            Self::BinanceStocks(value) => value.order_history(query).await,
            Self::Okx(value) => value.order_history(query).await,
            Self::Ibkr(value) => value.order_history(query).await,
        }
    }

    pub async fn order(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        match self {
            Self::BinanceSpot(value) => value.order_detail(query).await,
            Self::BinanceMargin(value) => value.order_detail(query).await,
            Self::BinanceUsdM(value) => value.order_detail(query).await,
            Self::BinanceCoinM(value) => value.order_detail(query).await,
            Self::BinanceOptions(value) => value.order_detail(query).await,
            Self::BinanceStocks(value) => value.order_detail(query).await,
            Self::Okx(value) => value.order_detail(query).await,
            Self::Ibkr(value) => value.order_detail(query).await,
        }
    }

    pub async fn submit(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<kairos_conflux::CommandOutcome<OrderEntryEvent>, IntegrationError> {
        match self {
            Self::BinanceSpot(value) => value.submit_order(request).await,
            Self::BinanceMargin(value) => value.submit_order(request).await,
            Self::BinanceUsdM(value) => value.submit_order(request).await,
            Self::BinanceCoinM(value) => value.submit_order(request).await,
            Self::BinanceOptions(value) => value.submit_order(request).await,
            Self::BinanceStocks(value) => value.submit_order(request).await,
            Self::Okx(value) => value.submit_order(request).await,
            Self::Ibkr(value) => value.submit_order(request).await,
        }
    }

    pub async fn cancel(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<kairos_conflux::CommandOutcome<OrderEntryEvent>, IntegrationError> {
        match self {
            Self::BinanceSpot(value) => {
                value
                    .cancel_order(request, remote_order_id, at_unix_nanos)
                    .await
            },
            Self::BinanceMargin(value) => {
                value
                    .cancel_order(request, remote_order_id, at_unix_nanos)
                    .await
            },
            Self::BinanceUsdM(value) => {
                value
                    .cancel_order(request, remote_order_id, at_unix_nanos)
                    .await
            },
            Self::BinanceCoinM(value) => {
                value
                    .cancel_order(request, remote_order_id, at_unix_nanos)
                    .await
            },
            Self::BinanceOptions(value) => {
                value
                    .cancel_order(request, remote_order_id, at_unix_nanos)
                    .await
            },
            Self::BinanceStocks(value) => {
                value
                    .cancel_order(request, remote_order_id, at_unix_nanos)
                    .await
            },
            Self::Okx(value) => {
                value
                    .cancel_order(request, remote_order_id, at_unix_nanos)
                    .await
            },
            Self::Ibkr(value) => {
                value
                    .cancel_order(request, remote_order_id, at_unix_nanos)
                    .await
            },
        }
    }

    pub async fn fills(
        &mut self,
        symbol: Option<&str>,
        order_id: Option<&str>,
        limit: Option<u16>,
    ) -> Result<Vec<DirectFill>, IntegrationError> {
        use kairos_conflux::{BinanceHistoryQuery, OkxHistoryQuery};
        let binance_query = || -> Result<BinanceHistoryQuery, IntegrationError> {
            Ok(BinanceHistoryQuery {
                symbol: symbol
                    .map(kairos_primitives::integration::ParticipantSymbol::new)
                    .transpose()
                    .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?,
                limit,
                ..Default::default()
            })
        };
        let rows = match self {
            Self::BinanceSpot(value) => value.fetch_account_trades(&binance_query()?).await?,
            Self::BinanceMargin(value) => value.fetch_account_trades(&binance_query()?).await?,
            Self::BinanceUsdM(value) => value.fetch_account_trades(&binance_query()?).await?,
            Self::BinanceCoinM(value) => value.fetch_account_trades(&binance_query()?).await?,
            Self::BinanceOptions(value) => value.fetch_account_trades(&binance_query()?).await?,
            Self::Okx(value) => {
                let rows = value
                    .fetch_fills(&OkxHistoryQuery {
                        instrument: symbol
                            .map(kairos_primitives::integration::ParticipantSymbol::new)
                            .transpose()
                            .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?,
                        limit,
                        ..Default::default()
                    })
                    .await?;
                return Ok(rows
                    .into_iter()
                    .filter(|row| order_id.is_none_or(|id| row.order_id == id))
                    .map(|row| DirectFill {
                        fill_id: row.trade_id,
                        remote_order_id: row.order_id,
                        symbol: row.instrument.to_string(),
                        side: format!("{:?}", row.side).to_ascii_lowercase(),
                        price: decimal(row.price),
                        quantity: decimal(row.quantity),
                        realized_pnl: None,
                        fee: row.fee.map(decimal),
                        fee_currency: row.fee_currency.map(|value| value.to_string()),
                        executed_at_unix_nanos: row.executed_at_unix_nanos.get(),
                    })
                    .collect());
            },
            Self::BinanceStocks(_) | Self::Ibkr(_) => {
                return Err(IntegrationError::Unavailable(
                    "standalone fill history is unavailable for this provider execution channel"
                        .into(),
                ));
            },
        };
        Ok(rows
            .into_iter()
            .filter(|row| order_id.is_none_or(|id| row.order_id == id))
            .map(|row| DirectFill {
                fill_id: row.trade_id,
                remote_order_id: row.order_id,
                symbol: row.symbol.to_string(),
                side: format!("{:?}", row.side).to_ascii_lowercase(),
                price: decimal(row.price),
                quantity: decimal(row.quantity),
                realized_pnl: row.realized_pnl.map(decimal),
                fee: row.commission.map(decimal),
                fee_currency: row.commission_asset.map(|value| value.to_string()),
                executed_at_unix_nanos: row.executed_at_unix_nanos.get(),
            })
            .collect())
    }
}

fn decimal(value: kairos_conflux::DecimalValue) -> String {
    value
        .format_fixed()
        .unwrap_or_else(|_| format!("{}e-{}", value.mantissa, value.scale))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn isolated_margin_fails_closed_instead_of_using_cross_margin() {
        let error = binance_connection(
            "isolated_margin",
            kairos_conflux::ConnectionKey::new("execution.direct.test").unwrap(),
            BinanceRestConfig {
                environment: "testnet".into(),
                endpoint: "https://example.invalid".into(),
                credential: None,
            },
        )
        .err()
        .expect("isolated margin must fail closed");
        assert!(error.to_string().contains("isolated margin"));
    }
}

pub fn binance_connection(
    execution_channel: &str,
    key: kairos_conflux::ConnectionKey,
    config: BinanceRestConfig,
) -> Result<DirectOrderConnection, IntegrationError> {
    match normalize(execution_channel).as_str() {
        "spot" => {
            BinanceSpotRestConnection::new(key, config).map(DirectOrderConnection::BinanceSpot)
        },
        "margin" | "cross-margin" => {
            BinanceMarginRestConnection::new(key, config).map(DirectOrderConnection::BinanceMargin)
        },
        "isolated-margin" => Err(IntegrationError::Unavailable(
            "standalone direct orders do not yet support Binance isolated margin routing".into(),
        )),
        "usd-m-futures" => {
            BinanceUsdMRestConnection::new(key, config).map(DirectOrderConnection::BinanceUsdM)
        },
        "coin-m-futures" => {
            BinanceCoinMRestConnection::new(key, config).map(DirectOrderConnection::BinanceCoinM)
        },
        "option" | "options" => BinanceOptionsRestConnection::new(key, config)
            .map(DirectOrderConnection::BinanceOptions),
        "equity" | "stocks" => {
            BinanceStocksRestConnection::new(key, config).map(DirectOrderConnection::BinanceStocks)
        },
        value => Err(IntegrationError::Unavailable(format!(
            "standalone direct order capability is unavailable for Binance {value}"
        ))),
    }
}

pub fn okx_connection(
    key: kairos_conflux::ConnectionKey,
    config: OkxPrivateRestConfig,
) -> Result<DirectOrderConnection, IntegrationError> {
    OkxPrivateRestConnection::new(key, config).map(DirectOrderConnection::Okx)
}

pub fn ibkr_connection(
    key: kairos_conflux::ConnectionKey,
    config: IbkrOrderConfig,
) -> Result<DirectOrderConnection, IntegrationError> {
    IbkrOrderConnection::new(key, config).map(DirectOrderConnection::Ibkr)
}

pub fn normalize(value: &str) -> String {
    value.trim().to_ascii_lowercase().replace('_', "-")
}

pub fn okx_rest_config(
    environment: String,
    endpoint: String,
    credential: kairos_conflux::OkxCredential,
) -> OkxPrivateRestConfig {
    OkxPrivateRestConfig {
        connection: OkxRestConfig {
            environment,
            endpoint,
        },
        credential,
    }
}
