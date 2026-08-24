//! Binance Stocks Trading REST connection for `/sapi/v1/equity/*`.

use kairos_primitives::integration::ParticipantSymbol;
use kairos_primitives::reference::Currency;
use serde_json::Value;

use crate::services::participants::binance::{execution, market};
use crate::{
    CommandResult, ExternalInstrument, ExternalInstrumentCatalog, ExternalInstrumentCatalogPage,
    ExternalInstrumentKind, ExternalOrder, ExternalOrderQuery, InstrumentCatalogQuery,
    IntegrationError, MarketQuote, MarketQuoteQuery, OrderCommand, OrderEntryEvent,
    OrderEntryRequest, OrderQuery, ParticipantKind, ParticipantRef,
};

rest_connection!(BinanceStocksRestConnection, "advanced.stocks.rest");

impl InstrumentCatalogQuery for BinanceStocksRestConnection {
    async fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        let value = self
            .service
            .keyed_get("/sapi/v1/equity/market/exchangeInfo", &[])
            .await?;
        let rows = value
            .get("symbols")
            .and_then(Value::as_array)
            .ok_or_else(|| {
                IntegrationError::InvalidPayload(
                    "Binance Stocks exchangeInfo symbols are missing".into(),
                )
            })?;
        let instruments = rows
            .iter()
            .map(stock_instrument)
            .collect::<Result<Vec<_>, _>>()?;
        Ok(ExternalInstrumentCatalog {
            participant: participant(),
            instruments,
        })
    }

    async fn fetch_instruments_page(
        &mut self,
        cursor: Option<&str>,
        limit: usize,
    ) -> Result<ExternalInstrumentCatalogPage, IntegrationError> {
        if cursor.is_some() {
            return Ok(ExternalInstrumentCatalogPage {
                catalog: ExternalInstrumentCatalog {
                    participant: participant(),
                    instruments: Vec::new(),
                },
                next_cursor: None,
                complete: true,
            });
        }
        let mut catalog = self.fetch_instruments().await?;
        if limit > 0 {
            catalog
                .instruments
                .truncate(limit.min(catalog.instruments.len()));
        }
        Ok(ExternalInstrumentCatalogPage {
            catalog,
            next_cursor: None,
            complete: true,
        })
    }
}

impl MarketQuoteQuery for BinanceStocksRestConnection {
    async fn fetch_quotes(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketQuote>, IntegrationError> {
        let mut quotes = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            let value = self
                .service
                .keyed_get(
                    "/sapi/v1/equity/market/quote",
                    &[("symbol", symbol.as_str().into())],
                )
                .await?;
            quotes.push(market::equity_quote(symbol, &value)?);
        }
        Ok(quotes)
    }
}

impl OrderCommand for BinanceStocksRestConnection {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        let params = stock_order_params(request);
        let outcome = self
            .service
            .signed_post_command("/sapi/v1/equity/order/place", &params)
            .await?;
        execution::submitted_outcome(request, outcome)
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        let params = [("orderId", remote_order_id.into())];
        let outcome = self
            .service
            .signed_post_command("/sapi/v1/equity/order/cancel", &params)
            .await?;
        execution::canceled_outcome(request, remote_order_id, at_unix_nanos, outcome)
    }
}

impl OrderQuery for BinanceStocksRestConnection {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let params = stock_query_params(query, false)?;
        let value = self
            .service
            .signed_get("/sapi/v1/equity/order/open-orders", &params)
            .await?;
        execution::orders(&self.descriptor().connection_key, rows(&value))
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let params = stock_query_params(query, false)?;
        let value = self
            .service
            .signed_get("/sapi/v1/equity/order/history", &params)
            .await?;
        execution::orders(&self.descriptor().connection_key, rows(&value))
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        let params = stock_query_params(query, true)?;
        let value = self
            .service
            .signed_get("/sapi/v1/equity/order/detail", &params)
            .await?;
        Ok(
            execution::orders(&self.descriptor().connection_key, &value)?
                .into_iter()
                .next(),
        )
    }
}

fn stock_order_params(request: &OrderEntryRequest) -> Vec<(&'static str, String)> {
    let mut params = vec![
        (
            "symbol",
            request.participant_instrument.source_symbol.to_string(),
        ),
        (
            "side",
            if request.side == crate::OrderSide::Buy {
                "BUY".into()
            } else {
                "SELL".into()
            },
        ),
        (
            "orderType",
            if request.order_type == crate::OrderType::Market {
                "MARKET".into()
            } else {
                "LIMIT".into()
            },
        ),
        ("quantity", execution::decimal(request.quantity)),
        ("clientOrderId", request.order_id.to_string()),
        (
            "tradingSession",
            request
                .options
                .trading_session
                .clone()
                .unwrap_or_else(|| "RTH".into()),
        ),
    ];
    if let Some(price) = request.limit_price {
        params.push(("price", execution::decimal(price)));
    }
    if request.order_type == crate::OrderType::Limit {
        params.push((
            "timeInForce",
            match request
                .options
                .time_in_force
                .unwrap_or(crate::TimeInForce::Day)
            {
                crate::TimeInForce::Day => "DAY",
                crate::TimeInForce::GoodTilCanceled => "GTC",
                crate::TimeInForce::ImmediateOrCancel => "IOC",
                crate::TimeInForce::FillOrKill => "FOK",
            }
            .into(),
        ));
    }
    if let Some(quote_asset) = &request.options.quote_asset {
        params.push(("quoteAsset", quote_asset.clone()));
    }
    if let Some(wallet_type) = &request.options.wallet_type {
        params.push(("walletType", wallet_type.clone()));
    }
    if let Some(tokenize) = request.options.tokenize {
        params.push(("tokenize", tokenize.to_string()));
    }
    params
}

fn stock_query_params(
    query: &ExternalOrderQuery,
    detail: bool,
) -> Result<Vec<(&'static str, String)>, IntegrationError> {
    let mut values = Vec::new();
    if let Some(symbol) = &query.symbol {
        values.push(("symbol", symbol.to_string()));
    }
    if detail {
        let order_id = query.order_id.as_ref().ok_or_else(|| {
            IntegrationError::InvalidRequest("Binance Stocks order detail requires order id".into())
        })?;
        values.push(("clientOrderId", order_id.to_string()));
    }
    if let Some(limit) = query.limit {
        values.push(("limit", limit.to_string()));
    }
    Ok(values)
}

fn rows(value: &Value) -> &Value {
    value.get("rows").unwrap_or(value)
}

fn stock_instrument(row: &Value) -> Result<ExternalInstrument, IntegrationError> {
    let symbol = text(row, "symbol")?;
    Ok(ExternalInstrument {
        source_symbol: ParticipantSymbol::new(symbol).map_err(payload)?,
        source_venue: row
            .get("exchange")
            .or_else(|| row.get("primaryExchange"))
            .and_then(Value::as_str)
            .map(str::to_owned),
        kind: ExternalInstrumentKind::Equity,
        base_currency: None,
        quote_currency: Some(Currency::new("USD").map_err(payload)?),
        settlement_currency: row
            .get("quoteAsset")
            .and_then(Value::as_str)
            .map(Currency::new)
            .transpose()
            .map_err(payload)?,
        underlying: None,
        expiry_unix_nanos: None,
        strike: None,
        option_right: None,
        active: row
            .get("tradability")
            .or_else(|| row.get("status"))
            .and_then(Value::as_str)
            .is_none_or(|status| !matches!(status, "NOT_TRADABLE" | "HALTED" | "INACTIVE")),
        price_tick: row
            .get("tickSize")
            .and_then(Value::as_str)
            .map(str::to_owned),
        quantity_tick: row
            .get("stepSize")
            .or_else(|| row.get("minQty"))
            .and_then(Value::as_str)
            .map(str::to_owned),
        minimum_quantity: row.get("minQty").and_then(Value::as_str).map(str::to_owned),
        minimum_notional: row
            .get("minNotional")
            .and_then(Value::as_str)
            .map(str::to_owned),
        contract_value: None,
        price_precision: row
            .get("pricePrecision")
            .and_then(Value::as_u64)
            .and_then(|value| u32::try_from(value).ok()),
        quantity_precision: row
            .get("quantityPrecision")
            .and_then(Value::as_u64)
            .and_then(|value| u32::try_from(value).ok()),
    })
}

fn text<'a>(row: &'a Value, field: &str) -> Result<&'a str, IntegrationError> {
    row.get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| IntegrationError::InvalidPayload(format!("Binance Stocks {field} missing")))
}

fn payload(error: impl std::fmt::Display) -> IntegrationError {
    IntegrationError::InvalidPayload(error.to_string())
}

fn participant() -> ParticipantRef {
    ParticipantRef::new(ParticipantKind::Exchange, "binance").expect("static Binance participant")
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;

    use secrecy::SecretString;

    use super::*;
    use crate::participants::binance::{BinanceCredential, BinanceRestConfig};

    #[tokio::test]
    async fn latest_equity_quote_uses_keyed_endpoint_and_stock_size_fields() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buffer = [0_u8; 8192];
            let size = stream.read(&mut buffer).unwrap();
            let request = String::from_utf8_lossy(&buffer[..size]);
            let request_line = request.lines().next().unwrap_or_default();
            assert!(request_line.contains("/sapi/v1/equity/market/quote?symbol=AAPL"));
            assert!(
                request
                    .to_ascii_lowercase()
                    .contains("x-mbx-apikey: test-api-key")
            );
            assert!(!request_line.contains("signature="));
            let body = r#"{"symbol":"AAPL","bidPrice":"180.50","askPrice":"180.52","bidSize":100,"askSize":200}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        });
        let mut connection = BinanceStocksRestConnection::new(
            crate::ConnectionKey::new("market.binance.equity.test").unwrap(),
            BinanceRestConfig {
                environment: "test".into(),
                endpoint,
                credential: Some(BinanceCredential {
                    principal_id: "test".into(),
                    api_key: SecretString::from("test-api-key".to_owned()),
                    secret: SecretString::from("test-secret".to_owned()),
                }),
            },
        )
        .unwrap();
        let symbol = ParticipantSymbol::new("AAPL").unwrap();

        let quote = connection
            .fetch_quotes(std::slice::from_ref(&symbol))
            .await
            .unwrap()
            .remove(0);

        server.join().unwrap();
        assert_eq!(quote.symbol, symbol);
        assert_eq!(quote.bid_price.unwrap().to_string(), "180.5");
        assert_eq!(quote.ask_price.unwrap().to_string(), "180.52");
        assert_eq!(quote.bid_quantity.unwrap().to_string(), "100");
        assert_eq!(quote.ask_quantity.unwrap().to_string(), "200");
        assert!(quote.last_price.is_none());
    }
}
