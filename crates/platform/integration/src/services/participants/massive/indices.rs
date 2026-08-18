//! Massive Indices REST transport and provider-native definitions.

use serde_json::Value;

use crate::transport::http::{ExchangeError, HttpClient};

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct IndexDefinitionRow {
    pub ticker: String,
    pub name: String,
    pub currency: Option<String>,
    pub source_venue: Option<String>,
    pub active: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct IndexBarRow {
    pub opened_at_unix_millis: u64,
    pub open: String,
    pub high: String,
    pub low: String,
    pub close: String,
}

#[derive(Clone)]
pub(crate) struct IndicesRestService {
    http: HttpClient,
    api_key: String,
    base_url: String,
    as_of: Option<String>,
}

impl IndicesRestService {
    pub(crate) fn new(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
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
            http: HttpClient::new("kairos-integration/massive-indices")?,
            api_key,
            base_url: base_url.trim_end_matches('/').into(),
            as_of: as_of.filter(|value| !value.trim().is_empty()),
        })
    }

    pub(crate) async fn definitions(&self) -> Result<Vec<IndexDefinitionRow>, ExchangeError> {
        let endpoint = format!("{}/v3/reference/tickers", self.base_url);
        let mut next_url = Some(endpoint);
        let mut result = Vec::new();
        for page in 0..10_000 {
            let Some(url) = next_url.take() else {
                return Ok(result);
            };
            let mut query = if page == 0 {
                vec![
                    ("market", "indices".into()),
                    ("active", "true".into()),
                    ("sort", "ticker".into()),
                    ("order", "asc".into()),
                    ("limit", "1000".into()),
                ]
            } else {
                Vec::new()
            };
            if page == 0 {
                if let Some(as_of) = &self.as_of {
                    query.push(("date", as_of.clone()));
                }
            }
            let payload = self.get(&url, &query).await?;
            result.extend(parse_definitions(&payload).map_err(ExchangeError::InvalidRequest)?);
            next_url = payload
                .get("next_url")
                .and_then(Value::as_str)
                .map(|value| private_next_url(value, &self.base_url));
        }
        Err(ExchangeError::InvalidRequest(
            "Massive Indices pagination exceeded safety limit".into(),
        ))
    }

    pub(crate) async fn bars(
        &self,
        ticker: &str,
        multiplier: u32,
        timespan: &str,
        start_unix_millis: u64,
        end_unix_millis: u64,
    ) -> Result<Vec<IndexBarRow>, ExchangeError> {
        let endpoint = format!(
            "{}/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{start_unix_millis}/{end_unix_millis}",
            self.base_url
        );
        let mut next_url = Some(endpoint);
        let mut rows = Vec::new();
        for page in 0..10_000 {
            let Some(url) = next_url.take() else {
                return Ok(rows);
            };
            let query = if page == 0 {
                vec![("sort", "asc".into()), ("limit", "50000".into())]
            } else {
                Vec::new()
            };
            let payload = self.get(&url, &query).await?;
            rows.extend(parse_bars(&payload).map_err(ExchangeError::InvalidRequest)?);
            next_url = payload
                .get("next_url")
                .and_then(Value::as_str)
                .map(|value| private_next_url(value, &self.base_url));
        }
        Err(ExchangeError::InvalidRequest(
            "Massive Indices aggregate pagination exceeded safety limit".into(),
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

fn parse_definitions(payload: &Value) -> Result<Vec<IndexDefinitionRow>, String> {
    payload
        .get("results")
        .and_then(Value::as_array)
        .ok_or_else(|| "Massive Indices response has no results list".to_string())?
        .iter()
        .map(|row| {
            Ok(IndexDefinitionRow {
                ticker: required_text(row, "ticker")?,
                name: required_text(row, "name")?,
                currency: text(row, "currency_symbol")
                    .or_else(|| text(row, "base_currency_symbol")),
                source_venue: text(row, "primary_exchange"),
                active: row.get("active").and_then(Value::as_bool).unwrap_or(true),
            })
        })
        .collect()
}

fn parse_bars(payload: &Value) -> Result<Vec<IndexBarRow>, String> {
    payload
        .get("results")
        .and_then(Value::as_array)
        .ok_or_else(|| "Massive Indices aggregate response has no results list".to_string())?
        .iter()
        .map(|row| {
            Ok(IndexBarRow {
                opened_at_unix_millis: row
                    .get("t")
                    .and_then(Value::as_u64)
                    .ok_or_else(|| "Massive index bar has no timestamp".to_string())?,
                open: required_number(row, "o")?,
                high: required_number(row, "h")?,
                low: required_number(row, "l")?,
                close: required_number(row, "c")?,
            })
        })
        .collect()
}

fn required_text(value: &Value, key: &str) -> Result<String, String> {
    text(value, key).ok_or_else(|| format!("Massive index row has no {key}"))
}

fn text(value: &Value, key: &str) -> Option<String> {
    value.get(key).and_then(Value::as_str).map(str::to_owned)
}

fn required_number(value: &Value, key: &str) -> Result<String, String> {
    value
        .get(key)
        .map(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| value.to_string())
        })
        .ok_or_else(|| format!("Massive index row has no {key}"))
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
    fn official_index_definition_and_bar_are_typed_without_equity_semantics() {
        let definitions = parse_definitions(&json!({"results":[{
            "ticker":"I:SPX","name":"S&P 500","market":"indices",
            "currency_symbol":"USD","active":true
        }]}))
        .unwrap();
        let bars = parse_bars(&json!({"results":[{
            "o":3985.67,"h":3990.0,"l":3980.0,"c":3988.5,"t":1678220675805u64
        }]}))
        .unwrap();

        assert_eq!(definitions[0].ticker, "I:SPX");
        assert_eq!(definitions[0].currency.as_deref(), Some("USD"));
        assert_eq!(bars[0].opened_at_unix_millis, 1_678_220_675_805);
    }
}
