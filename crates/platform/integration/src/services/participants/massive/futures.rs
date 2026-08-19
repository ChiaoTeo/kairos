//! Massive Futures REST transport records and bounded pagination.

use serde_json::Value;

use crate::transport::http::{ExchangeError, HttpClient};

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct FuturesContractRow {
    pub ticker: String,
    pub product_code: Option<String>,
    pub trading_venue: Option<String>,
    pub active: bool,
    pub settlement_date: Option<String>,
    pub trade_tick_size: Option<String>,
    pub min_order_quantity: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct FuturesBarRow {
    pub window_start_unix_nanos: u64,
    pub open: String,
    pub high: String,
    pub low: String,
    pub close: String,
    pub volume: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct FuturesQuoteRow {
    pub timestamp_unix_nanos: u64,
    pub bid_price: Option<String>,
    pub bid_size: Option<String>,
    pub ask_price: Option<String>,
    pub ask_size: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct FuturesTradeRow {
    pub timestamp_unix_nanos: u64,
    pub sequence_number: Option<u64>,
    pub price: String,
    pub size: String,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct FuturesPage<T> {
    pub rows: Vec<T>,
    pub next_cursor: Option<String>,
}

#[derive(Clone)]
pub(crate) struct FuturesRestService {
    http: HttpClient,
    api_key: String,
    base_url: String,
    product_code: Option<String>,
    as_of: Option<String>,
}

impl FuturesRestService {
    pub(crate) fn new(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
        product_code: Option<String>,
        as_of: Option<String>,
    ) -> Result<Self, ExchangeError> {
        let api_key = api_key.into();
        if api_key.trim().is_empty() {
            return Err(ExchangeError::Authentication(
                "Massive API key is required".into(),
            ));
        }
        let base_url = base_url.into();
        if base_url.trim().is_empty() {
            return Err(ExchangeError::InvalidRequest(
                "Massive base URL is required".into(),
            ));
        }
        Ok(Self {
            http: HttpClient::new("kairos-integration/massive-futures")?,
            api_key,
            base_url: base_url.trim_end_matches('/').into(),
            product_code: non_empty(product_code),
            as_of: non_empty(as_of),
        })
    }

    pub(crate) async fn contracts_page(
        &self,
        cursor: Option<&str>,
        limit: usize,
    ) -> Result<FuturesPage<FuturesContractRow>, ExchangeError> {
        let endpoint = format!("{}/futures/v1/contracts", self.base_url);
        let mut query = vec![
            ("active", "true".into()),
            ("limit", limit.clamp(1, 1000).to_string()),
            ("sort", "ticker.asc".into()),
        ];
        if let Some(cursor) = cursor.filter(|value| !value.trim().is_empty()) {
            query.push(("cursor", cursor.into()));
        }
        if let Some(product_code) = &self.product_code {
            query.push(("product_code", product_code.clone()));
        }
        if let Some(as_of) = &self.as_of {
            query.push(("date", as_of.clone()));
        }
        let payload = self.get(&endpoint, &query).await?;
        Ok(FuturesPage {
            rows: parse_contracts(&payload).map_err(ExchangeError::InvalidRequest)?,
            next_cursor: next_cursor(&payload),
        })
    }

    pub(crate) async fn bars(
        &self,
        ticker: &str,
        resolution: &str,
        start_unix_nanos: u64,
        end_unix_nanos: u64,
    ) -> Result<Vec<FuturesBarRow>, ExchangeError> {
        self.paginate(
            format!("{}/futures/v1/aggs/{ticker}", self.base_url),
            vec![
                ("resolution", resolution.into()),
                ("window_start.gte", start_unix_nanos.to_string()),
                ("window_start.lte", end_unix_nanos.to_string()),
                ("sort", "window_start.asc".into()),
                ("limit", "50000".into()),
            ],
            parse_bar,
        )
        .await
    }

    pub(crate) async fn quotes(
        &self,
        ticker: &str,
        start_unix_nanos: u64,
        end_unix_nanos: u64,
    ) -> Result<Vec<FuturesQuoteRow>, ExchangeError> {
        self.paginate(
            format!("{}/futures/v1/quotes/{ticker}", self.base_url),
            tick_query(start_unix_nanos, end_unix_nanos),
            parse_quote,
        )
        .await
    }

    pub(crate) async fn trades(
        &self,
        ticker: &str,
        start_unix_nanos: u64,
        end_unix_nanos: u64,
    ) -> Result<Vec<FuturesTradeRow>, ExchangeError> {
        self.paginate(
            format!("{}/futures/v1/trades/{ticker}", self.base_url),
            tick_query(start_unix_nanos, end_unix_nanos),
            parse_trade,
        )
        .await
    }

    async fn paginate<T>(
        &self,
        endpoint: String,
        first_query: Vec<(&'static str, String)>,
        parse: fn(&Value) -> Result<T, String>,
    ) -> Result<Vec<T>, ExchangeError> {
        let mut next_url = Some(endpoint);
        let mut rows = Vec::new();
        for page in 0..10_000 {
            let Some(url) = next_url.take() else {
                return Ok(rows);
            };
            let query = if page == 0 {
                first_query.clone()
            } else {
                Vec::new()
            };
            let payload = self.get(&url, &query).await?;
            let values = payload
                .get("results")
                .and_then(Value::as_array)
                .ok_or_else(|| {
                    ExchangeError::InvalidRequest(
                        "Massive Futures response has no results list".into(),
                    )
                })?;
            rows.extend(
                values
                    .iter()
                    .map(parse)
                    .collect::<Result<Vec<_>, _>>()
                    .map_err(ExchangeError::InvalidRequest)?,
            );
            next_url = payload
                .get("next_url")
                .and_then(Value::as_str)
                .map(|value| private_next_url(value, &self.base_url));
        }
        Err(ExchangeError::InvalidRequest(
            "Massive Futures pagination exceeded safety limit".into(),
        ))
    }

    async fn get(
        &self,
        endpoint: &str,
        query: &[(impl AsRef<str>, String)],
    ) -> Result<Value, ExchangeError> {
        let query = query
            .iter()
            .map(|(key, value)| (key.as_ref(), value.clone()))
            .collect::<Vec<_>>();
        Ok(self
            .http
            .get_json_response_with_headers_and_query(
                endpoint,
                &query,
                &[("Authorization", format!("Bearer {}", self.api_key))],
            )
            .await?
            .body)
    }
}

fn tick_query(start: u64, end: u64) -> Vec<(&'static str, String)> {
    vec![
        ("timestamp.gte", start.to_string()),
        ("timestamp.lte", end.to_string()),
        ("sort", "timestamp.asc".into()),
        ("limit", "49999".into()),
    ]
}

fn parse_contracts(payload: &Value) -> Result<Vec<FuturesContractRow>, String> {
    payload
        .get("results")
        .and_then(Value::as_array)
        .ok_or_else(|| "Massive Futures contracts response has no results list".to_string())?
        .iter()
        .map(|row| {
            Ok(FuturesContractRow {
                ticker: required_text(row, "ticker")?,
                product_code: text(row, "product_code"),
                trading_venue: text(row, "trading_venue"),
                active: row.get("active").and_then(Value::as_bool).unwrap_or(true),
                settlement_date: text(row, "settlement_date"),
                trade_tick_size: number(row, "trade_tick_size"),
                min_order_quantity: number(row, "min_order_quantity"),
            })
        })
        .collect()
}

fn parse_bar(row: &Value) -> Result<FuturesBarRow, String> {
    Ok(FuturesBarRow {
        window_start_unix_nanos: required_u64(row, "window_start")?,
        open: required_number(row, "open")?,
        high: required_number(row, "high")?,
        low: required_number(row, "low")?,
        close: required_number(row, "close")?,
        volume: number(row, "volume"),
    })
}

fn parse_quote(row: &Value) -> Result<FuturesQuoteRow, String> {
    Ok(FuturesQuoteRow {
        timestamp_unix_nanos: required_u64(row, "timestamp")?,
        bid_price: number(row, "bid_price"),
        bid_size: number(row, "bid_size"),
        ask_price: number(row, "ask_price"),
        ask_size: number(row, "ask_size"),
    })
}

fn parse_trade(row: &Value) -> Result<FuturesTradeRow, String> {
    Ok(FuturesTradeRow {
        timestamp_unix_nanos: required_u64(row, "timestamp")?,
        sequence_number: row.get("sequence_number").and_then(Value::as_u64),
        price: required_number(row, "price")?,
        size: required_number(row, "size")?,
    })
}

fn required_text(value: &Value, key: &str) -> Result<String, String> {
    text(value, key).ok_or_else(|| format!("Massive Futures row has no {key}"))
}

fn text(value: &Value, key: &str) -> Option<String> {
    value.get(key).and_then(Value::as_str).map(str::to_owned)
}

fn required_number(value: &Value, key: &str) -> Result<String, String> {
    number(value, key).ok_or_else(|| format!("Massive Futures row has no {key}"))
}

fn number(value: &Value, key: &str) -> Option<String> {
    value.get(key).map(|value| {
        value
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| value.to_string())
    })
}

fn required_u64(value: &Value, key: &str) -> Result<u64, String> {
    value
        .get(key)
        .and_then(|value| value.as_u64().or_else(|| value.as_str()?.parse().ok()))
        .ok_or_else(|| format!("Massive Futures row has no valid {key}"))
}

fn next_cursor(payload: &Value) -> Option<String> {
    payload
        .get("next_url")
        .and_then(Value::as_str)
        .and_then(|url| url::Url::parse(url).ok())
        .and_then(|url| {
            url.query_pairs()
                .find(|(key, _)| key == "cursor")
                .map(|(_, value)| value.into_owned())
        })
}

fn private_next_url(next_url: &str, base_url: &str) -> String {
    let Ok(parsed) = url::Url::parse(next_url) else {
        return next_url.to_owned();
    };
    let query = parsed
        .query_pairs()
        .filter(|(key, _)| !key.eq_ignore_ascii_case("apikey"))
        .map(|(key, value)| (key.into_owned(), value.into_owned()))
        .collect::<Vec<_>>();
    let mut target = format!("{}{}", base_url.trim_end_matches('/'), parsed.path());
    if !query.is_empty() {
        target.push('?');
        target.push_str(
            &url::form_urlencoded::Serializer::new(String::new())
                .extend_pairs(query)
                .finish(),
        );
    }
    target
}

fn non_empty(value: Option<String>) -> Option<String> {
    value.filter(|value| !value.trim().is_empty())
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;

    use serde_json::json;

    use super::*;

    #[test]
    fn official_contract_and_market_rows_keep_futures_units() {
        let contracts = parse_contracts(&json!({"results":[{
            "ticker":"GCJ5","product_code":"GC","trading_venue":"XNYM",
            "active":true,"settlement_date":"2025-04-28","trade_tick_size":0.1,
            "min_order_quantity":1
        }]}))
        .unwrap();
        let bar = parse_bar(&json!({
            "window_start":1738627200000000000u64,"open":2849.8,"high":2877.1,
            "low":2837.4,"close":2874.2,"volume":133072
        }))
        .unwrap();
        let quote = parse_quote(&json!({
            "timestamp":1734472770000588000u64,"bid_price":2684.7,"bid_size":1,
            "ask_price":2686,"ask_size":1
        }))
        .unwrap();

        assert_eq!(contracts[0].ticker, "GCJ5");
        assert_eq!(contracts[0].trade_tick_size.as_deref(), Some("0.1"));
        assert_eq!(bar.window_start_unix_nanos, 1_738_627_200_000_000_000);
        assert_eq!(quote.bid_size.as_deref(), Some("1"));
    }

    #[test]
    fn pagination_rewrites_provider_url_to_configured_endpoint_and_drops_api_key() {
        let url = private_next_url(
            "https://api.massive.com/futures/v1/contracts?cursor=next&apiKey=secret",
            "http://127.0.0.1:9999",
        );
        assert_eq!(
            url,
            "http://127.0.0.1:9999/futures/v1/contracts?cursor=next"
        );
    }

    #[tokio::test]
    async fn futures_contract_query_is_bounded_filtered_and_header_authenticated() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buffer = [0u8; 8192];
            let size = stream.read(&mut buffer).unwrap();
            let request = String::from_utf8_lossy(&buffer[..size]);
            let request_line = request.lines().next().unwrap_or_default();
            assert!(request_line.contains("/futures/v1/contracts?"));
            assert!(request_line.contains("active=true"));
            assert!(request_line.contains("product_code=GC"));
            assert!(request_line.contains("date=2025-02-26"));
            assert!(request_line.contains("limit=25"));
            assert!(!request_line.to_ascii_lowercase().contains("apikey"));
            assert!(
                request
                    .to_ascii_lowercase()
                    .contains("authorization: bearer futures-secret\r\n")
            );
            let body = r#"{"results":[]}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        });

        let page = FuturesRestService::new(
            "futures-secret",
            endpoint,
            Some("GC".into()),
            Some("2025-02-26".into()),
        )
        .unwrap()
        .contracts_page(None, 25)
        .await
        .unwrap();

        assert!(page.rows.is_empty());
        server.join().unwrap();
    }
}
