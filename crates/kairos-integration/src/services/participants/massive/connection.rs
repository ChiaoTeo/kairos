//! Massive public REST client composition boundary.

use serde_json::Value;

use crate::services::transport::http::{AsyncPublicHttpClient, ExchangeError, PublicHttpClient};

use super::reference::{MassiveMarketClient, MassiveMarketRow};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MassiveMarketPage {
    pub rows: Vec<MassiveMarketRow>,
    pub next_cursor: Option<String>,
    pub complete: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct MassiveHistoricalBar {
    pub(crate) open_time_unix_millis: i64,
    pub(crate) open: String,
    pub(crate) high: String,
    pub(crate) low: String,
    pub(crate) close: String,
    pub(crate) volume: Option<String>,
}

#[derive(Clone)]
pub struct MassiveStocksRestClient {
    http: PublicHttpClient,
    api_key: String,
    base_url: String,
    options: bool,
    option_underlying: Option<String>,
}

#[derive(Clone)]
pub struct MassiveAsyncRestClient {
    http: AsyncPublicHttpClient,
    api_key: String,
    base_url: String,
    options: bool,
    option_underlying: Option<String>,
}

impl MassiveAsyncRestClient {
    pub fn with_base_url(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
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
            http: AsyncPublicHttpClient::new("kairos-integration/massive")?,
            api_key,
            base_url: base_url.trim_end_matches('/').into(),
            options: false,
            option_underlying: None,
        })
    }

    pub fn for_options(mut self) -> Self {
        self.options = true;
        self
    }

    pub fn with_option_underlying(mut self, underlying: impl Into<String>) -> Self {
        let underlying = underlying.into();
        if !underlying.trim().is_empty() {
            self.option_underlying = Some(underlying);
        }
        self
    }

    pub fn for_equity(mut self) -> Self {
        self.options = false;
        self
    }

    pub async fn load_markets_page(
        &self,
        cursor: Option<&str>,
        limit: usize,
    ) -> Result<MassiveMarketPage, ExchangeError> {
        let (endpoint, mut query) = if self.options {
            (
                format!("{}/v3/reference/options/contracts", self.base_url),
                vec![
                    ("expired", "false".into()),
                    ("limit", limit.clamp(1, 1000).to_string()),
                    ("sort", "expiration_date".into()),
                    ("order", "asc".into()),
                ],
            )
        } else {
            (
                format!("{}/v3/reference/tickers", self.base_url),
                vec![
                    ("market", "stocks".into()),
                    ("active", "true".into()),
                    ("limit", limit.clamp(1, 1000).to_string()),
                ],
            )
        };
        if let Some(cursor) = cursor {
            query.push(("cursor", cursor.to_owned()));
        }
        if self.options {
            if let Some(underlying) = &self.option_underlying {
                query.push(("underlying_ticker", underlying.clone()));
            }
        }
        let payload = self
            .http
            .get_json_response_with_headers_and_query(
                &endpoint,
                &query,
                &[("Authorization", format!("Bearer {}", self.api_key))],
            )
            .await?
            .body;
        let rows = if self.options {
            rows_from_payload(&payload).map_err(ExchangeError::InvalidRequest)?
        } else {
            equity_rows_from_payload(payload.clone()).map_err(ExchangeError::InvalidRequest)?
        };
        let next_cursor = next_cursor(&payload);
        Ok(MassiveMarketPage {
            complete: next_cursor.is_none(),
            rows,
            next_cursor,
        })
    }

    pub async fn load_markets(&self) -> Result<Vec<MassiveMarketRow>, ExchangeError> {
        let mut cursor = None;
        let mut rows = Vec::new();
        for _ in 0..10_000 {
            let page = self.load_markets_page(cursor.as_deref(), 1000).await?;
            rows.extend(page.rows);
            cursor = page.next_cursor;
            if cursor.is_none() {
                return Ok(rows);
            }
        }
        Err(ExchangeError::InvalidRequest(
            "Massive instrument pagination exceeded safety limit".into(),
        ))
    }

    pub(crate) async fn historical_bars(
        &self,
        ticker: &str,
        multiplier: u32,
        timespan: &str,
        start_unix_millis: i64,
        end_unix_millis: i64,
    ) -> Result<Vec<MassiveHistoricalBar>, ExchangeError> {
        let endpoint = format!(
            "{}/v2/aggs/ticker/{}/range/{}/{}/{}/{}",
            self.base_url, ticker, multiplier, timespan, start_unix_millis, end_unix_millis
        );
        let mut next_url = Some(endpoint);
        let mut result = Vec::new();
        for _ in 0..10_000 {
            let Some(url) = next_url.take() else {
                return Ok(result);
            };
            let query = if url.contains('?') {
                Vec::new()
            } else {
                vec![
                    ("adjusted", "false".into()),
                    ("sort", "asc".into()),
                    ("limit", "50000".into()),
                ]
            };
            let payload = self
                .http
                .get_json_response_with_headers_and_query(
                    &url,
                    &query,
                    &[("Authorization", format!("Bearer {}", self.api_key))],
                )
                .await?
                .body;
            append_historical_rows(&mut result, &payload);
            next_url = payload
                .get("next_url")
                .and_then(Value::as_str)
                .map(|value| private_next_url(value, &self.base_url));
        }
        Err(ExchangeError::InvalidRequest(
            "Massive historical pagination exceeded safety limit".into(),
        ))
    }
}

impl MassiveStocksRestClient {
    pub fn with_base_url(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
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
            http: PublicHttpClient::new("kairos-integration/massive")?,
            api_key,
            base_url: base_url.trim_end_matches('/').into(),
            options: false,
            option_underlying: None,
        })
    }

    pub fn for_options(mut self) -> Self {
        self.options = true;
        self
    }

    pub fn with_option_underlying(mut self, underlying: impl Into<String>) -> Self {
        let underlying = underlying.into();
        if !underlying.trim().is_empty() {
            self.option_underlying = Some(underlying);
        }
        self
    }

    pub fn for_equity(mut self) -> Self {
        self.options = false;
        self
    }

    pub(crate) fn historical_bars(
        &self,
        ticker: &str,
        multiplier: u32,
        timespan: &str,
        start_unix_millis: i64,
        end_unix_millis: i64,
    ) -> Result<Vec<MassiveHistoricalBar>, String> {
        let endpoint = format!(
            "{}/v2/aggs/ticker/{}/range/{}/{}/{}/{}",
            self.base_url, ticker, multiplier, timespan, start_unix_millis, end_unix_millis
        );
        let mut next_url = Some(endpoint);
        let mut result = Vec::new();
        let mut pages = 0;
        while let Some(url) = next_url.take() {
            pages += 1;
            if pages > 10_000 {
                return Err("Massive historical pagination exceeded safety limit".into());
            }
            let query = if url.contains('?') {
                Vec::new()
            } else {
                vec![
                    ("adjusted", "false".into()),
                    ("sort", "asc".into()),
                    ("limit", "50000".into()),
                ]
            };
            let payload = self
                .http
                .get_json_with_headers_and_query(
                    &url,
                    &query,
                    &[("Authorization", format!("Bearer {}", self.api_key))],
                )
                .map_err(|error| error.to_string())?;
            let rows = payload
                .get("results")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            for row in rows {
                let Some(timestamp) = row.get("t").and_then(Value::as_i64) else {
                    continue;
                };
                let number = |key: &str| {
                    row.get(key).map(|value| {
                        value
                            .as_str()
                            .map(str::to_owned)
                            .unwrap_or_else(|| value.to_string())
                    })
                };
                let Some(open) = number("o") else { continue };
                let Some(high) = number("h") else { continue };
                let Some(low) = number("l") else { continue };
                let Some(close) = number("c") else { continue };
                result.push(MassiveHistoricalBar {
                    open_time_unix_millis: timestamp,
                    open,
                    high,
                    low,
                    close,
                    volume: number("v"),
                });
            }
            next_url = payload
                .get("next_url")
                .and_then(Value::as_str)
                .map(|value| private_next_url(value, &self.base_url));
        }
        Ok(result)
    }

    pub fn option_contracts(&self) -> Result<Vec<MassiveMarketRow>, String> {
        let endpoint = format!("{}/v3/reference/options/contracts", self.base_url);
        let mut next_url = Some(endpoint);
        let mut rows = Vec::new();
        let mut pages = 0;
        while let Some(url) = next_url.take() {
            pages += 1;
            if pages > 10_000 {
                return Err("Massive options pagination exceeded safety limit".into());
            }
            let query = if url.contains('?') {
                Vec::new()
            } else {
                let mut query = vec![
                    ("expired", "false".into()),
                    ("limit", "1000".into()),
                    ("sort", "expiration_date".into()),
                    ("order", "asc".into()),
                ];
                if let Some(underlying) = &self.option_underlying {
                    query.push(("underlying_ticker", underlying.clone()));
                }
                query
            };
            let payload = self
                .http
                .get_json_with_headers_and_query(
                    &url,
                    &query,
                    &[("Authorization", format!("Bearer {}", self.api_key))],
                )
                .map_err(|error| error.to_string())?;
            rows.extend(rows_from_payload(&payload)?);
            next_url = payload
                .get("next_url")
                .and_then(Value::as_str)
                .map(|url| private_next_url(url, &self.base_url));
        }
        Ok(rows)
    }

    pub fn option_contracts_page(
        &self,
        cursor: Option<&str>,
        limit: usize,
    ) -> Result<MassiveMarketPage, String> {
        let endpoint = format!("{}/v3/reference/options/contracts", self.base_url);
        let limit = limit.clamp(1, 1000);
        let mut query = vec![
            ("expired", "false".into()),
            ("limit", limit.to_string()),
            ("sort", "expiration_date".into()),
            ("order", "asc".into()),
        ];
        if let Some(cursor) = cursor {
            query.push(("cursor", cursor.to_owned()));
        }
        if let Some(underlying) = &self.option_underlying {
            query.push(("underlying_ticker", underlying.clone()));
        }
        let payload = self
            .http
            .get_json_with_headers_and_query(
                &endpoint,
                &query,
                &[("Authorization", format!("Bearer {}", self.api_key))],
            )
            .map_err(|error| error.to_string())?;
        let rows = rows_from_payload(&payload)?;
        let next_cursor = payload
            .get("next_url")
            .and_then(Value::as_str)
            .and_then(|next_url| {
                url::Url::parse(next_url).ok().and_then(|parsed| {
                    parsed
                        .query_pairs()
                        .find(|(key, _)| key == "cursor")
                        .map(|(_, value)| value.into_owned())
                })
            })
            .filter(|value| !value.is_empty());
        Ok(MassiveMarketPage {
            complete: next_cursor.is_none(),
            rows,
            next_cursor,
        })
    }

    pub fn equity_tickers(&self) -> Result<Vec<MassiveMarketRow>, String> {
        let mut cursor = None;
        let mut rows = Vec::new();
        let mut pages = 0;
        loop {
            pages += 1;
            if pages > 10_000 {
                return Err("Massive equity pagination exceeded safety limit".into());
            }
            let page = self.equity_tickers_page(cursor.as_deref(), 1000)?;
            rows.extend(page.rows);
            cursor = page.next_cursor;
            if cursor.is_none() {
                return Ok(rows);
            }
        }
    }

    fn equity_tickers_page(
        &self,
        cursor: Option<&str>,
        limit: usize,
    ) -> Result<MassiveMarketPage, String> {
        let endpoint = format!("{}/v3/reference/tickers", self.base_url);
        let mut query = vec![
            ("market", "stocks".into()),
            ("active", "true".into()),
            ("limit", limit.clamp(1, 1000).to_string()),
        ];
        if let Some(cursor) = cursor {
            query.push(("cursor", cursor.to_owned()));
        }
        let payload = self
            .http
            .get_json_with_headers_and_query(
                &endpoint,
                &query,
                &[("Authorization", format!("Bearer {}", self.api_key))],
            )
            .map_err(|error| error.to_string())?;
        let rows = equity_rows_from_payload(payload.clone())?;
        let next_cursor = payload
            .get("next_url")
            .and_then(Value::as_str)
            .and_then(|next_url| {
                url::Url::parse(next_url).ok().and_then(|parsed| {
                    parsed
                        .query_pairs()
                        .find(|(key, _)| key == "cursor")
                        .map(|(_, value)| value.into_owned())
                })
            })
            .filter(|value| !value.is_empty());
        Ok(MassiveMarketPage {
            complete: next_cursor.is_none(),
            rows,
            next_cursor,
        })
    }
}

/// Massive's private proxy may return a pagination URL pointing at the
/// public api.massive.com host. Keep pagination inside the configured proxy;
/// the proxy is the endpoint that recognizes the workspace credential.
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
        let encoded = url::form_urlencoded::Serializer::new(String::new())
            .extend_pairs(query)
            .finish();
        target.push('?');
        target.push_str(&encoded);
    }
    target
}

fn next_cursor(payload: &Value) -> Option<String> {
    payload
        .get("next_url")
        .and_then(Value::as_str)
        .and_then(|next_url| {
            url::Url::parse(next_url).ok().and_then(|parsed| {
                parsed
                    .query_pairs()
                    .find(|(key, _)| key == "cursor")
                    .map(|(_, value)| value.into_owned())
            })
        })
        .filter(|value| !value.is_empty())
}

fn append_historical_rows(result: &mut Vec<MassiveHistoricalBar>, payload: &Value) {
    for row in payload
        .get("results")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let Some(timestamp) = row.get("t").and_then(Value::as_i64) else {
            continue;
        };
        let number = |key: &str| {
            row.get(key).map(|value| {
                value
                    .as_str()
                    .map(str::to_owned)
                    .unwrap_or_else(|| value.to_string())
            })
        };
        let Some(open) = number("o") else { continue };
        let Some(high) = number("h") else { continue };
        let Some(low) = number("l") else { continue };
        let Some(close) = number("c") else {
            continue;
        };
        result.push(MassiveHistoricalBar {
            open_time_unix_millis: timestamp,
            open,
            high,
            low,
            close,
            volume: number("v"),
        });
    }
}

impl MassiveMarketClient for MassiveStocksRestClient {
    fn load_markets(&mut self) -> Result<Vec<MassiveMarketRow>, String> {
        if self.options {
            self.option_contracts()
        } else {
            self.equity_tickers()
        }
    }

    fn load_markets_page(
        &mut self,
        cursor: Option<&str>,
        limit: usize,
    ) -> Result<MassiveMarketPage, String> {
        if self.options {
            self.option_contracts_page(cursor, limit)
        } else {
            self.equity_tickers_page(cursor, limit)
        }
    }
}

fn equity_rows_from_payload(payload: Value) -> Result<Vec<MassiveMarketRow>, String> {
    let rows = payload
        .get("results")
        .and_then(Value::as_array)
        .ok_or_else(|| "Massive equity response has no results list".to_string())?;
    Ok(rows
        .iter()
        .filter_map(|value| {
            let ticker = value.get("ticker")?.as_str()?.to_string();
            Some(MassiveMarketRow {
                ticker: ticker.clone(),
                exchange: value
                    .get("primary_exchange")
                    .or_else(|| value.get("exchange"))
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                market_type: "equity".into(),
                base: Some(ticker),
                quote: Some("USD".into()),
                active: value.get("active").and_then(Value::as_bool).unwrap_or(true),
                price_tick: None,
                amount_tick: Some("1".into()),
                price_precision: 2,
                amount_precision: 0,
                underlying: None,
                expiry_unix_nanos: None,
                strike: None,
                option_right: None,
                contract_size: Some("1".into()),
            })
        })
        .collect())
}

fn rows_from_payload(payload: &Value) -> Result<Vec<MassiveMarketRow>, String> {
    let rows = payload
        .get("results")
        .and_then(Value::as_array)
        .ok_or_else(|| "Massive options response has no results list".to_string())?;
    Ok(rows
        .iter()
        .filter_map(|value| {
            let ticker = value.get("ticker")?.as_str()?.to_string();
            let underlying = value
                .get("underlying_ticker")
                .and_then(Value::as_str)
                .map(str::to_owned)
                .or_else(|| underlying_from_option_ticker(&ticker));
            Some(MassiveMarketRow {
                ticker,
                exchange: value
                    .get("primary_exchange")
                    .or_else(|| value.get("exchange"))
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                market_type: "options".into(),
                base: underlying.clone(),
                quote: Some("USD".into()),
                active: value.get("active").and_then(Value::as_bool).unwrap_or(true),
                price_tick: Some("0.01".into()),
                amount_tick: Some("1".into()),
                price_precision: 2,
                amount_precision: 0,
                underlying,
                expiry_unix_nanos: value
                    .get("expiration_date")
                    .and_then(Value::as_str)
                    .and_then(date_to_unix_nanos),
                strike: value
                    .get("strike_price")
                    .map(|value| value.to_string().trim_matches('"').to_string()),
                option_right: value
                    .get("contract_type")
                    .and_then(Value::as_str)
                    .map(str::to_ascii_lowercase),
                contract_size: value
                    .get("shares_per_contract")
                    .map(|value| value.to_string().trim_matches('"').to_string()),
            })
        })
        .collect())
}

fn underlying_from_option_ticker(ticker: &str) -> Option<String> {
    let ticker = ticker.strip_prefix("O:")?;
    let end = ticker
        .char_indices()
        .find(|(_, value)| value.is_ascii_digit())
        .map(|(index, _)| index)?;
    (!ticker[..end].is_empty()).then(|| ticker[..end].to_ascii_uppercase())
}

fn date_to_unix_nanos(value: &str) -> Option<u64> {
    let mut parts = value.split('-');
    let year: i64 = parts.next()?.parse().ok()?;
    let month: i64 = parts.next()?.parse().ok()?;
    let day: i64 = parts.next()?.parse().ok()?;
    if !(1..=12).contains(&month) || !(1..=31).contains(&day) {
        return None;
    }
    let adjusted_year = year - i64::from(month <= 2);
    let era = if adjusted_year >= 0 {
        adjusted_year / 400
    } else {
        (adjusted_year - 399) / 400
    };
    let year_of_era = adjusted_year - era * 400;
    let adjusted_month = month + if month > 2 { -3 } else { 9 };
    let day_of_year = (153 * adjusted_month + 2) / 5 + day - 1;
    let day_of_era = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    let days = era * 146097 + day_of_era - 719468;
    u64::try_from(days.checked_mul(86_400)?)
        .ok()?
        .checked_mul(1_000_000_000)
}

#[cfg(test)]
mod tests {
    use super::{
        equity_rows_from_payload, private_next_url, rows_from_payload, MassiveAsyncRestClient,
    };
    use std::io::{Read, Write};
    use std::net::TcpListener;

    #[test]
    fn pagination_stays_on_private_massive_proxy() {
        assert_eq!(
            private_next_url(
                "https://api.massive.com/v3/reference/options/contracts?cursor=abc&apiKey=secret",
                "http://api.massiveprivateserver.site",
            ),
            "http://api.massiveprivateserver.site/v3/reference/options/contracts?cursor=abc"
        );
    }

    #[tokio::test]
    async fn async_massive_authentication_uses_a_header_not_the_request_url() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buffer = [0u8; 8192];
            let size = stream.read(&mut buffer).unwrap();
            let request = String::from_utf8_lossy(&buffer[..size]);
            let request_lower = request.to_ascii_lowercase();
            assert!(!request
                .lines()
                .next()
                .unwrap_or_default()
                .contains("apiKey"));
            assert!(request_lower.contains("authorization: bearer test-secret\r\n"));
            let body = r#"{"results":[]}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        });

        let page = MassiveAsyncRestClient::with_base_url("test-secret", endpoint)
            .unwrap()
            .for_equity()
            .load_markets_page(None, 1)
            .await
            .unwrap();

        assert!(page.complete);
        assert!(page.rows.is_empty());
        server.join().unwrap();
    }

    #[test]
    fn maps_massive_option_contract_payload_to_market_rows() {
        let rows = rows_from_payload(&serde_json::json!({
            "results": [{
                "ticker": "O:SPY260821C00500000",
                "expiration_date": "2026-08-21",
                "strike_price": 500,
                "contract_type": "call",
                "shares_per_contract": 100,
                "active": true
            }]
        }))
        .unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].ticker, "O:SPY260821C00500000");
        assert_eq!(rows[0].strike.as_deref(), Some("500"));
        assert!(rows[0].expiry_unix_nanos.is_some());
        assert_eq!(rows[0].underlying.as_deref(), Some("SPY"));
    }

    #[test]
    fn maps_all_option_underlyings_without_a_global_filter() {
        let rows = rows_from_payload(&serde_json::json!({
            "results": [
                {"ticker": "O:SPY260821C00500000", "underlying_ticker": "SPY"},
                {"ticker": "O:NVDA260821C00100000", "underlying_ticker": "NVDA"}
            ]
        }))
        .unwrap();
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0].underlying.as_deref(), Some("SPY"));
        assert_eq!(rows[1].underlying.as_deref(), Some("NVDA"));
    }

    #[test]
    fn rejects_massive_payload_without_results() {
        let error = rows_from_payload(&serde_json::json!({})).unwrap_err();
        assert!(error.contains("results"));
    }

    #[test]
    fn maps_massive_equity_ticker_payload_to_market_rows() {
        let rows = equity_rows_from_payload(serde_json::json!({
            "results": [{"ticker": "AAPL", "active": true}]
        }))
        .unwrap();
        assert_eq!(rows[0].market_type, "equity");
        assert_eq!(rows[0].ticker, "AAPL");
    }
}
