//! Binance Stocks Trading REST connection for `/sapi/v1/equity/*`.

use kairos_primitives::integration::ParticipantSymbol;
use serde::Deserialize;
use serde_json::Value;

use crate::services::participants::binance::{execution, market};
use crate::{
    CommandResult, ExternalInstrument, ExternalInstrumentCatalog, ExternalInstrumentCatalogPage,
    ExternalInstrumentKind, ExternalOrder, ExternalOrderQuery, InstrumentCatalogQuery,
    IntegrationError, MarketQuote, MarketQuoteQuery, OrderCommand, OrderEntryEvent,
    OrderEntryRequest, OrderQuery, ParticipantKind, ParticipantRef,
};

rest_connection!(BinanceStocksRestConnection, "advanced.stocks.rest");

#[derive(Debug, Deserialize)]
struct BinanceStocksExchangeInfo {
    timezone: String,
    symbols: Vec<BinanceStockSymbol>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct BinanceStockSymbol {
    symbol: String,
    extended_session: bool,
    fractionable: bool,
    fractionable_eh: bool,
    listing_time: u64,
    max_notional: String,
    max_num_orders: u64,
    max_qty: String,
    min_notional: String,
    multiplier_down: String,
    multiplier_up: String,
    overnight_supported: bool,
    step_size: String,
    tradability: String,
    tradability_update_time: u64,
}

impl InstrumentCatalogQuery for BinanceStocksRestConnection {
    async fn fetch_instruments(&self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        let value = self
            .service
            .keyed_get("/sapi/v1/equity/market/exchangeInfo", &[])
            .await?;
        let response: BinanceStocksExchangeInfo =
            serde_json::from_value(value).map_err(|error| {
                IntegrationError::InvalidPayload(format!(
                    "Binance Stocks exchangeInfo response is invalid: {error}"
                ))
            })?;
        if response.timezone.trim().is_empty() {
            return Err(IntegrationError::InvalidPayload(
                "Binance Stocks exchangeInfo timezone is empty".into(),
            ));
        }
        let instruments = response
            .symbols
            .iter()
            .map(stock_instrument)
            .collect::<Result<Vec<_>, _>>()?;
        Ok(ExternalInstrumentCatalog {
            participant: participant(),
            instruments,
            venues: Vec::new(),
        })
    }

    async fn fetch_instruments_page(
        &self,
        cursor: Option<&str>,
        limit: usize,
    ) -> Result<ExternalInstrumentCatalogPage, IntegrationError> {
        if cursor.is_some() {
            return Ok(ExternalInstrumentCatalogPage {
                catalog: ExternalInstrumentCatalog {
                    participant: participant(),
                    instruments: Vec::new(),
                    venues: Vec::new(),
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

fn stock_instrument(row: &BinanceStockSymbol) -> Result<ExternalInstrument, IntegrationError> {
    let symbol = row.symbol.trim();
    Ok(ExternalInstrument {
        source_symbol: ParticipantSymbol::new(symbol).map_err(payload)?,
        // The official Stocks exchangeInfo response does not identify a
        // listing exchange. Reference must not manufacture XNAS/XNYS facts.
        source_venue: None,
        kind: ExternalInstrumentKind::Equity,
        base_currency: None,
        quote_currency: None,
        settlement_currency: None,
        underlying: None,
        expiry_unix_nanos: None,
        strike: None,
        option_right: None,
        active: stock_is_active(&row.tradability),
        price_tick: None,
        quantity_tick: Some(row.step_size.clone()),
        minimum_quantity: None,
        minimum_notional: Some(row.min_notional.clone()),
        contract_value: None,
        price_precision: None,
        quantity_precision: None,
    })
}

fn stock_is_active(tradability: &str) -> bool {
    matches!(tradability, "BUY_SELL" | "BUY_ONLY" | "SELL_ONLY")
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
    async fn equity_catalog_parses_official_exchange_info_shape_once_at_boundary() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buffer = [0_u8; 8192];
            let size = stream.read(&mut buffer).unwrap();
            let request = String::from_utf8_lossy(&buffer[..size]);
            let request_line = request.lines().next().unwrap_or_default();
            assert!(request_line.contains("/sapi/v1/equity/market/exchangeInfo"));
            assert!(
                request
                    .to_ascii_lowercase()
                    .contains("x-mbx-apikey: test-api-key")
            );
            let body = r#"{"timezone":"UTC","symbols":[{"extendedSession":true,"fractionable":true,"fractionableEh":true,"listingTime":1751328000000,"maxNotional":"1000000","maxNumOrders":200,"maxQty":"1000000","minNotional":"1","multiplierDown":"0.8","multiplierUp":"1.2","overnightSupported":true,"stepSize":"0.0001","symbol":"AAPL","tradability":"BUY_SELL","tradabilityUpdateTime":1751328000000}]}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        });
        let connection = BinanceStocksRestConnection::new(
            crate::ConnectionKey::new("reference.binance.equity.test").unwrap(),
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

        let catalog = connection.fetch_instruments().await.unwrap();

        server.join().unwrap();
        assert_eq!(catalog.participant, participant());
        let instrument = catalog.instruments.first().unwrap();
        assert_eq!(instrument.source_symbol.as_str(), "AAPL");
        assert_eq!(instrument.source_venue, None);
        assert_eq!(instrument.quote_currency, None);
        assert_eq!(instrument.settlement_currency, None);
        assert_eq!(instrument.price_tick, None);
        assert_eq!(instrument.quantity_tick.as_deref(), Some("0.0001"));
        assert_eq!(instrument.minimum_quantity, None);
        assert_eq!(instrument.minimum_notional.as_deref(), Some("1"));
        assert!(instrument.active);
        assert!(!stock_is_active("NONE"));
        assert!(!stock_is_active("OFFMARKET"));
        assert!(!stock_is_active("FUTURE_UNKNOWN_STATE"));
    }

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
