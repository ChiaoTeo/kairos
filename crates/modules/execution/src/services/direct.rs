//! Short-lived provider connections used only by the standalone CLI facade.

use kairos_conflux::{
    BinanceCoinMRestConnection, BinanceMarginRestConnection, BinanceOptionsRestConnection,
    BinanceRestConfig, BinanceSpotRestConnection, BinanceStocksRestConnection,
    BinanceUsdMRestConnection, ExternalOrder, ExternalOrderQuery, IbkrOrderConfig,
    IbkrOrderConnection, IntegrationError, OkxPrivateRestConfig, OkxPrivateRestConnection,
    OkxRestConfig, OrderCommand, OrderEntryEvent, OrderEntryRequest, OrderQuery,
};

pub enum DirectOrderConnection {
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
    ) -> Result<Vec<serde_json::Value>, IntegrationError> {
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
                    .map(|row| {
                        serde_json::json!({
                            "fill_id": row.trade_id,
                            "remote_order_id": row.order_id,
                            "symbol": row.instrument.to_string(),
                            "side": format!("{:?}", row.side).to_ascii_lowercase(),
                            "price": decimal(row.price),
                            "quantity": decimal(row.quantity),
                            "fee": row.fee.map(decimal),
                            "fee_currency": row.fee_currency.map(|value| value.to_string()),
                            "executed_at_unix_nanos": row.executed_at_unix_nanos.get(),
                        })
                    })
                    .collect());
            },
            Self::BinanceStocks(_) | Self::Ibkr(_) => {
                return Err(IntegrationError::Unavailable(
                    "standalone fill history is unavailable for this provider product".into(),
                ));
            },
        };
        Ok(rows
            .into_iter()
            .filter(|row| order_id.is_none_or(|id| row.order_id == id))
            .map(|row| {
                serde_json::json!({
                    "fill_id": row.trade_id,
                    "remote_order_id": row.order_id,
                    "symbol": row.symbol.to_string(),
                    "side": format!("{:?}", row.side).to_ascii_lowercase(),
                    "price": decimal(row.price),
                    "quantity": decimal(row.quantity),
                    "realized_pnl": row.realized_pnl.map(decimal),
                    "fee": row.commission.map(decimal),
                    "fee_currency": row.commission_asset.map(|value| value.to_string()),
                    "executed_at_unix_nanos": row.executed_at_unix_nanos.get(),
                })
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
    product: &str,
    key: kairos_conflux::ConnectionKey,
    config: BinanceRestConfig,
) -> Result<DirectOrderConnection, IntegrationError> {
    match normalize(product).as_str() {
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
