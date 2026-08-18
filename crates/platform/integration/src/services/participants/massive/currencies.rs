//! Massive Forex/Crypto REST transport and bounded pagination.

use serde_json::Value;

use crate::transport::http::{ExchangeError, HttpClient};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum CurrencyMarket {
    Forex,
    Crypto,
}

impl CurrencyMarket {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Forex => "fx",
            Self::Crypto => "crypto",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct CurrencyTickerRow {
    pub ticker: String,
    pub base_currency: Option<String>,
    pub quote_currency: Option<String>,
    pub source_venue: Option<String>,
    pub active: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct CurrencyBarRow {
    pub opened_at_unix_millis: u64,
    pub open: String,
    pub high: String,
    pub low: String,
    pub close: String,
    pub volume: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct CurrencyQuoteRow {
    pub timestamp_unix_nanos: u64,
    pub bid_price: Option<String>,
    pub bid_size: Option<String>,
    pub ask_price: Option<String>,
    pub ask_size: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct CurrencyTradeRow {
    pub timestamp_unix_nanos: u64,
    pub trade_id: Option<String>,
    pub price: String,
    pub size: String,
}

#[derive(Clone)]
pub(crate) struct CurrenciesRestService {
    http: HttpClient,
    api_key: String,
    base_url: String,
    market: CurrencyMarket,
    as_of: Option<String>,
}

impl CurrenciesRestService {
    pub(crate) fn new(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
        market: CurrencyMarket,
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
            http: HttpClient::new("kairos-integration/massive-currencies")?,
            api_key,
            base_url: base_url.trim_end_matches('/').into(),
            market,
            as_of: as_of.filter(|value| !value.trim().is_empty()),
        })
    }

    pub(crate) async fn tickers(&self) -> Result<Vec<CurrencyTickerRow>, ExchangeError> {
        let mut query = vec![
            ("market", self.market.as_str().into()),
            ("active", "true".into()),
            ("sort", "ticker".into()),
            ("order", "asc".into()),
            ("limit", "1000".into()),
        ];
        if let Some(as_of) = &self.as_of {
            query.push(("date", as_of.clone()));
        }
        self.paginate(
            format!("{}/v3/reference/tickers", self.base_url),
            query,
            parse_ticker,
        )
        .await
    }

    pub(crate) async fn bars(
        &self,
        ticker: &str,
        multiplier: u32,
        timespan: &str,
        start_unix_millis: u64,
        end_unix_millis: u64,
    ) -> Result<Vec<CurrencyBarRow>, ExchangeError> {
        self.paginate(
            format!(
                "{}/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{start_unix_millis}/{end_unix_millis}",
                self.base_url
            ),
            vec![("sort", "asc".into()), ("limit", "50000".into())],
            parse_bar,
        )
        .await
    }

    pub(crate) async fn quotes(
        &self,
        ticker: &str,
        start_unix_nanos: u64,
        end_unix_nanos: u64,
    ) -> Result<Vec<CurrencyQuoteRow>, ExchangeError> {
        self.paginate(
            format!("{}/v3/quotes/{ticker}", self.base_url),
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
    ) -> Result<Vec<CurrencyTradeRow>, ExchangeError> {
        if self.market != CurrencyMarket::Crypto {
            return Err(ExchangeError::InvalidRequest(
                "Massive Forex does not expose a historical trade feed".into(),
            ));
        }
        self.paginate(
            format!("{}/v3/trades/{ticker}", self.base_url),
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
                        "Massive currency response has no results list".into(),
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
            "Massive currency pagination exceeded safety limit".into(),
        ))
    }

    async fn get(&self, endpoint: &str, query: &[(&str, String)]) -> Result<Value, ExchangeError> {
        Ok(self
            .http
            .get_json_response_with_headers_and_query(
                endpoint,
                query,
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
        ("sort", "timestamp".into()),
        ("order", "asc".into()),
        ("limit", "50000".into()),
    ]
}

fn parse_ticker(row: &Value) -> Result<CurrencyTickerRow, String> {
    Ok(CurrencyTickerRow {
        ticker: required_text(row, "ticker")?,
        base_currency: text(row, "base_currency_symbol"),
        quote_currency: text(row, "currency_symbol").or_else(|| text(row, "quote_currency_symbol")),
        source_venue: text(row, "primary_exchange"),
        active: row.get("active").and_then(Value::as_bool).unwrap_or(true),
    })
}

fn parse_bar(row: &Value) -> Result<CurrencyBarRow, String> {
    Ok(CurrencyBarRow {
        opened_at_unix_millis: required_u64(row, "t")?,
        open: required_number(row, "o")?,
        high: required_number(row, "h")?,
        low: required_number(row, "l")?,
        close: required_number(row, "c")?,
        volume: number(row, "v"),
    })
}

fn parse_quote(row: &Value) -> Result<CurrencyQuoteRow, String> {
    Ok(CurrencyQuoteRow {
        timestamp_unix_nanos: row
            .get("participant_timestamp")
            .or_else(|| row.get("sip_timestamp"))
            .or_else(|| row.get("timestamp"))
            .and_then(Value::as_u64)
            .ok_or_else(|| "Massive currency quote has no timestamp".to_string())?,
        bid_price: number(row, "bid_price"),
        bid_size: number(row, "bid_size"),
        ask_price: number(row, "ask_price"),
        ask_size: number(row, "ask_size"),
    })
}

fn parse_trade(row: &Value) -> Result<CurrencyTradeRow, String> {
    Ok(CurrencyTradeRow {
        timestamp_unix_nanos: row
            .get("participant_timestamp")
            .or_else(|| row.get("sip_timestamp"))
            .and_then(Value::as_u64)
            .ok_or_else(|| "Massive crypto trade has no timestamp".to_string())?,
        trade_id: text(row, "id"),
        price: required_number(row, "price")?,
        size: required_number(row, "size")?,
    })
}

fn text(value: &Value, key: &str) -> Option<String> {
    value.get(key).and_then(Value::as_str).map(str::to_owned)
}

fn required_text(value: &Value, key: &str) -> Result<String, String> {
    text(value, key).ok_or_else(|| format!("Massive currency row has no {key}"))
}

fn number(value: &Value, key: &str) -> Option<String> {
    value.get(key).and_then(|value| match value {
        Value::String(value) => Some(value.clone()),
        Value::Number(value) => Some(value.to_string()),
        _ => None,
    })
}

fn required_number(value: &Value, key: &str) -> Result<String, String> {
    number(value, key).ok_or_else(|| format!("Massive currency row has no {key}"))
}

fn required_u64(value: &Value, key: &str) -> Result<u64, String> {
    value
        .get(key)
        .and_then(Value::as_u64)
        .ok_or_else(|| format!("Massive currency row has no {key}"))
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

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn official_crypto_trade_and_forex_ticker_are_typed() {
        let trade = parse_trade(&json!({
            "id":"191450340","participant_timestamp":1625097600103000000_u64,
            "price":35060,"size":1.0434526,"exchange":1
        }))
        .unwrap();
        let ticker = parse_ticker(&json!({
            "ticker":"C:EURUSD","market":"fx","base_currency_symbol":"EUR",
            "currency_symbol":"USD","active":true
        }))
        .unwrap();

        assert_eq!(trade.trade_id.as_deref(), Some("191450340"));
        assert_eq!(trade.price, "35060");
        assert_eq!(ticker.base_currency.as_deref(), Some("EUR"));
        assert_eq!(ticker.quote_currency.as_deref(), Some("USD"));
    }

    #[test]
    fn pagination_url_is_rebased_without_api_key() {
        assert_eq!(
            private_next_url(
                "https://api.massive.com/v3/trades/X:BTCUSD?cursor=abc&apiKey=secret",
                "http://127.0.0.1:9000"
            ),
            "http://127.0.0.1:9000/v3/trades/X:BTCUSD?cursor=abc"
        );
    }
}
