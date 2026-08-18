//! Massive public REST client composition boundary.

use serde_json::Value;

use crate::transport::http::{ExchangeError, HttpClient};

use super::reference::MassiveMarketRow;

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct MassiveMarketPage {
    pub(crate) rows: Vec<MassiveMarketRow>,
    pub(crate) next_cursor: Option<String>,
    pub(crate) complete: bool,
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

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct MassiveHistoricalQuote {
    pub(crate) sip_timestamp_unix_nanos: u64,
    pub(crate) participant_timestamp_unix_nanos: Option<u64>,
    pub(crate) bid_price: Option<String>,
    pub(crate) bid_size: Option<String>,
    pub(crate) bid_exchange: Option<String>,
    pub(crate) ask_price: Option<String>,
    pub(crate) ask_size: Option<String>,
    pub(crate) ask_exchange: Option<String>,
    pub(crate) tape: Option<u32>,
    pub(crate) sequence_number: Option<u64>,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct MassiveHistoricalTrade {
    pub(crate) sip_timestamp_unix_nanos: u64,
    pub(crate) participant_timestamp_unix_nanos: Option<u64>,
    pub(crate) trf_timestamp_unix_nanos: Option<u64>,
    pub(crate) price: String,
    pub(crate) size: String,
    pub(crate) exchange: Option<String>,
    pub(crate) tape: Option<u32>,
    pub(crate) trf_id: Option<u32>,
    pub(crate) sequence_number: Option<u64>,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct MassiveCashDividendRow {
    pub(crate) id: String,
    pub(crate) ticker: String,
    pub(crate) ex_dividend_date: String,
    pub(crate) declaration_date: Option<String>,
    pub(crate) record_date: Option<String>,
    pub(crate) pay_date: Option<String>,
    pub(crate) cash_amount: Option<String>,
    pub(crate) split_adjusted_cash_amount: Option<String>,
    pub(crate) historical_adjustment_factor: Option<String>,
    pub(crate) currency: Option<String>,
    pub(crate) distribution_type: Option<String>,
    pub(crate) frequency: Option<u32>,
}

#[derive(Clone)]
pub(crate) struct RestService {
    http: HttpClient,
    api_key: String,
    base_url: String,
    options: bool,
    option_underlying: Option<String>,
    option_as_of: Option<String>,
    option_expiration_start: Option<String>,
    option_expiration_end: Option<String>,
    option_contract_type: Option<String>,
}

impl RestService {
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
            http: HttpClient::new("kairos-integration/massive")?,
            api_key,
            base_url: base_url.trim_end_matches('/').into(),
            options: false,
            option_underlying: None,
            option_as_of: None,
            option_expiration_start: None,
            option_expiration_end: None,
            option_contract_type: None,
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

    pub fn with_option_as_of(mut self, value: impl Into<String>) -> Self {
        self.option_as_of = non_empty(value);
        self
    }

    pub fn with_option_expiration_range(
        mut self,
        start: impl Into<String>,
        end: impl Into<String>,
    ) -> Self {
        self.option_expiration_start = non_empty(start);
        self.option_expiration_end = non_empty(end);
        self
    }

    pub fn with_option_contract_type(mut self, value: impl Into<String>) -> Self {
        self.option_contract_type = non_empty(value);
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
            if let Some(as_of) = &self.option_as_of {
                query.push(("as_of", as_of.clone()));
            }
            if let Some(start) = &self.option_expiration_start {
                query.push(("expiration_date.gte", start.clone()));
            }
            if let Some(end) = &self.option_expiration_end {
                query.push(("expiration_date.lte", end.clone()));
            }
            if let Some(contract_type) = &self.option_contract_type {
                query.push(("contract_type", contract_type.clone()));
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
        adjusted: bool,
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
                    ("adjusted", adjusted.to_string()),
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

    pub(crate) async fn historical_quotes(
        &self,
        ticker: &str,
        start_unix_nanos: u64,
        end_unix_nanos: u64,
    ) -> Result<Vec<MassiveHistoricalQuote>, ExchangeError> {
        let payloads = self
            .historical_ticks("quotes", ticker, start_unix_nanos, end_unix_nanos)
            .await?;
        payloads
            .iter()
            .map(massive_historical_quote)
            .collect::<Result<Vec<_>, _>>()
            .map_err(ExchangeError::InvalidRequest)
    }

    pub(crate) async fn historical_trades(
        &self,
        ticker: &str,
        start_unix_nanos: u64,
        end_unix_nanos: u64,
    ) -> Result<Vec<MassiveHistoricalTrade>, ExchangeError> {
        let payloads = self
            .historical_ticks("trades", ticker, start_unix_nanos, end_unix_nanos)
            .await?;
        payloads
            .iter()
            .map(massive_historical_trade)
            .collect::<Result<Vec<_>, _>>()
            .map_err(ExchangeError::InvalidRequest)
    }

    pub(crate) async fn cash_dividends(
        &self,
        ticker: &str,
        start_date: &str,
        end_date: &str,
    ) -> Result<Vec<MassiveCashDividendRow>, ExchangeError> {
        let endpoint = format!("{}/stocks/v1/dividends", self.base_url);
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
                    ("ticker", ticker.to_owned()),
                    ("ex_dividend_date.gte", start_date.to_owned()),
                    ("ex_dividend_date.lte", end_date.to_owned()),
                    ("sort", "ex_dividend_date".into()),
                    ("order", "asc".into()),
                    ("limit", "1000".into()),
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
            let rows = payload
                .get("results")
                .and_then(Value::as_array)
                .ok_or_else(|| {
                    ExchangeError::InvalidRequest(
                        "Massive dividend response has no results list".into(),
                    )
                })?;
            for value in rows {
                let Some(id) = value.get("id").and_then(Value::as_str) else {
                    continue;
                };
                let Some(row_ticker) = value.get("ticker").and_then(Value::as_str) else {
                    continue;
                };
                let Some(ex_dividend_date) = value.get("ex_dividend_date").and_then(Value::as_str)
                else {
                    continue;
                };
                let number = |name: &str| {
                    value.get(name).map(|item| {
                        item.as_str()
                            .map(str::to_owned)
                            .unwrap_or_else(|| item.to_string())
                    })
                };
                let text = |name: &str| value.get(name).and_then(Value::as_str).map(str::to_owned);
                result.push(MassiveCashDividendRow {
                    id: id.into(),
                    ticker: row_ticker.into(),
                    ex_dividend_date: ex_dividend_date.into(),
                    declaration_date: text("declaration_date"),
                    record_date: text("record_date"),
                    pay_date: text("pay_date"),
                    cash_amount: number("cash_amount"),
                    split_adjusted_cash_amount: number("split_adjusted_cash_amount"),
                    historical_adjustment_factor: number("historical_adjustment_factor"),
                    currency: text("currency"),
                    distribution_type: text("distribution_type").or_else(|| text("dividend_type")),
                    frequency: value
                        .get("frequency")
                        .and_then(Value::as_u64)
                        .and_then(|value| u32::try_from(value).ok()),
                });
            }
            next_url = payload
                .get("next_url")
                .and_then(Value::as_str)
                .map(|value| private_next_url(value, &self.base_url));
        }
        Err(ExchangeError::InvalidRequest(
            "Massive dividend pagination exceeded safety limit".into(),
        ))
    }

    async fn historical_ticks(
        &self,
        resource: &str,
        ticker: &str,
        start_unix_nanos: u64,
        end_unix_nanos: u64,
    ) -> Result<Vec<Value>, ExchangeError> {
        let endpoint = format!("{}/v3/{resource}/{ticker}", self.base_url);
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
                    ("timestamp.gte", start_unix_nanos.to_string()),
                    ("timestamp.lte", end_unix_nanos.to_string()),
                    ("sort", "timestamp".into()),
                    ("order", "asc".into()),
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
            let rows = payload
                .get("results")
                .and_then(Value::as_array)
                .ok_or_else(|| {
                    ExchangeError::InvalidRequest(format!(
                        "Massive historical {resource} response has no results list"
                    ))
                })?;
            result.extend(rows.iter().cloned());
            next_url = payload
                .get("next_url")
                .and_then(Value::as_str)
                .map(|value| private_next_url(value, &self.base_url));
        }
        Err(ExchangeError::InvalidRequest(format!(
            "Massive historical {resource} pagination exceeded safety limit"
        )))
    }
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

fn value_text(value: &Value, key: &str) -> Option<String> {
    value.get(key).map(|item| {
        item.as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| item.to_string())
    })
}

fn massive_historical_quote(value: &Value) -> Result<MassiveHistoricalQuote, String> {
    Ok(MassiveHistoricalQuote {
        sip_timestamp_unix_nanos: value
            .get("sip_timestamp")
            .and_then(Value::as_u64)
            .ok_or_else(|| "Massive historical quote has no sip_timestamp".to_string())?,
        participant_timestamp_unix_nanos: value
            .get("participant_timestamp")
            .and_then(Value::as_u64),
        bid_price: value_text(value, "bid_price"),
        bid_size: value_text(value, "bid_size"),
        bid_exchange: value_text(value, "bid_exchange"),
        ask_price: value_text(value, "ask_price"),
        ask_size: value_text(value, "ask_size"),
        ask_exchange: value_text(value, "ask_exchange"),
        tape: value
            .get("tape")
            .and_then(Value::as_u64)
            .and_then(|value| u32::try_from(value).ok()),
        sequence_number: value.get("sequence_number").and_then(Value::as_u64),
    })
}

fn massive_historical_trade(value: &Value) -> Result<MassiveHistoricalTrade, String> {
    Ok(MassiveHistoricalTrade {
        sip_timestamp_unix_nanos: value
            .get("sip_timestamp")
            .and_then(Value::as_u64)
            .ok_or_else(|| "Massive historical trade has no sip_timestamp".to_string())?,
        participant_timestamp_unix_nanos: value
            .get("participant_timestamp")
            .and_then(Value::as_u64),
        trf_timestamp_unix_nanos: value.get("trf_timestamp").and_then(Value::as_u64),
        price: value_text(value, "price")
            .ok_or_else(|| "Massive historical trade has no price".to_string())?,
        size: value_text(value, "size")
            .ok_or_else(|| "Massive historical trade has no size".to_string())?,
        exchange: value_text(value, "exchange"),
        tape: value
            .get("tape")
            .and_then(Value::as_u64)
            .and_then(|value| u32::try_from(value).ok()),
        trf_id: value
            .get("trf_id")
            .and_then(Value::as_u64)
            .and_then(|value| u32::try_from(value).ok()),
        sequence_number: value.get("sequence_number").and_then(Value::as_u64),
    })
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

fn non_empty(value: impl Into<String>) -> Option<String> {
    let value = value.into();
    (!value.trim().is_empty()).then_some(value)
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
        equity_rows_from_payload, massive_historical_trade, private_next_url, rows_from_payload,
        RestService,
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

        let page = RestService::with_base_url("test-secret", endpoint)
            .unwrap()
            .for_equity()
            .load_markets_page(None, 1)
            .await
            .unwrap();

        assert!(page.complete);
        assert!(page.rows.is_empty());
        server.join().unwrap();
    }

    #[tokio::test]
    async fn historical_option_catalog_sends_point_in_time_filters() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buffer = [0u8; 8192];
            let size = stream.read(&mut buffer).unwrap();
            let request = String::from_utf8_lossy(&buffer[..size]);
            let request_line = request.lines().next().unwrap_or_default();
            assert!(request_line.contains("underlying_ticker=SPY"));
            assert!(request_line.contains("as_of=2024-12-19"));
            assert!(request_line.contains("expiration_date.gte=2024-12-20"));
            assert!(request_line.contains("expiration_date.lte=2025-01-31"));
            assert!(request_line.contains("contract_type=put"));
            assert!(request_line.contains("expired=false"));
            let body = r#"{"results":[]}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        });

        let page = RestService::with_base_url("test-secret", endpoint)
            .unwrap()
            .for_options()
            .with_option_underlying("SPY")
            .with_option_as_of("2024-12-19")
            .with_option_expiration_range("2024-12-20", "2025-01-31")
            .with_option_contract_type("put")
            .load_markets_page(None, 1000)
            .await
            .unwrap();

        assert!(page.complete);
        server.join().unwrap();
    }

    #[tokio::test]
    async fn cash_dividends_use_bounded_query_and_map_rows() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buffer = [0u8; 8192];
            let size = stream.read(&mut buffer).unwrap();
            let request = String::from_utf8_lossy(&buffer[..size]);
            let request_line = request.lines().next().unwrap_or_default();
            assert!(request_line.contains("/stocks/v1/dividends?"));
            assert!(request_line.contains("ticker=SPY"));
            assert!(request_line.contains("ex_dividend_date.gte=2024-01-01"));
            assert!(request_line.contains("ex_dividend_date.lte=2024-12-31"));
            assert!(request
                .to_ascii_lowercase()
                .contains("authorization: bearer test-secret\r\n"));
            let body = r#"{"results":[{"id":"div-1","ticker":"SPY","ex_dividend_date":"2024-03-15","declaration_date":"2024-02-29","cash_amount":1.59,"split_adjusted_cash_amount":1.59,"currency":"USD","frequency":4}]}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        });

        let rows = RestService::with_base_url("test-secret", endpoint)
            .unwrap()
            .cash_dividends("SPY", "2024-01-01", "2024-12-31")
            .await
            .unwrap();

        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].id, "div-1");
        assert_eq!(rows[0].cash_amount.as_deref(), Some("1.59"));
        server.join().unwrap();
    }

    #[tokio::test]
    async fn historical_option_quotes_use_bounded_timestamp_query_and_map_rows() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buffer = [0u8; 8192];
            let size = stream.read(&mut buffer).unwrap();
            let request = String::from_utf8_lossy(&buffer[..size]);
            let request_line = request.lines().next().unwrap_or_default();
            assert!(request_line.contains("/v3/quotes/O:SPY250117P00500000?"));
            assert!(request_line.contains("timestamp.gte=100"));
            assert!(request_line.contains("timestamp.lte=200"));
            assert!(request_line.contains("order=asc"));
            assert!(request
                .to_ascii_lowercase()
                .contains("authorization: bearer test-secret\r\n"));
            let body = r#"{"results":[{"bid_price":1.1,"bid_size":2,"bid_exchange":301,"ask_price":1.2,"ask_size":3,"ask_exchange":302,"participant_timestamp":140,"sequence_number":7,"sip_timestamp":150,"tape":3}]}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        });

        let rows = RestService::with_base_url("test-secret", endpoint)
            .unwrap()
            .for_options()
            .historical_quotes("O:SPY250117P00500000", 100, 200)
            .await
            .unwrap();

        server.join().unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].bid_price.as_deref(), Some("1.1"));
        assert_eq!(rows[0].ask_size.as_deref(), Some("3"));
        assert_eq!(rows[0].sequence_number, Some(7));
        assert_eq!(rows[0].sip_timestamp_unix_nanos, 150);
        assert_eq!(rows[0].participant_timestamp_unix_nanos, Some(140));
        assert_eq!(rows[0].bid_exchange.as_deref(), Some("301"));
        assert_eq!(rows[0].ask_exchange.as_deref(), Some("302"));
        assert_eq!(rows[0].tape, Some(3));
    }

    #[test]
    fn historical_trade_preserves_exchange_tape_and_trf_evidence() {
        let row = massive_historical_trade(&serde_json::json!({
            "price": 306.64,
            "size": 1,
            "exchange": 4,
            "tape": 3,
            "trf_id": 202,
            "participant_timestamp": 1786970875219715143_u64,
            "sip_timestamp": 1786970875220887116_u64,
            "trf_timestamp": 1786970875220868005_u64,
            "sequence_number": 353918
        }))
        .unwrap();

        assert_eq!(row.exchange.as_deref(), Some("4"));
        assert_eq!(row.tape, Some(3));
        assert_eq!(row.trf_id, Some(202));
        assert_eq!(
            row.participant_timestamp_unix_nanos,
            Some(1786970875219715143)
        );
        assert_eq!(row.trf_timestamp_unix_nanos, Some(1786970875220868005));
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
