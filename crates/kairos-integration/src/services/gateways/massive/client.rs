//! Massive public REST client composition boundary.

use serde_json::Value;

use crate::services::drivers::http::{ExchangeError, PublicHttpClient};

use super::market::{MassiveMarketClient, MassiveMarketRow};

#[derive(Clone)]
pub struct MassiveStocksRestClient {
    http: PublicHttpClient,
    api_key: String,
    base_url: String,
    underlying: String,
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
            underlying: "SPY".into(),
        })
    }

    pub fn for_underlying(mut self, underlying: impl Into<String>) -> Self {
        self.underlying = underlying.into().trim().to_ascii_uppercase();
        self
    }

    pub fn for_equity(mut self) -> Self {
        self.underlying.clear();
        self
    }

    pub fn option_contracts(&self) -> Result<Vec<MassiveMarketRow>, String> {
        let endpoint = format!("{}/v3/reference/options/contracts", self.base_url);
        let query = vec![
            ("underlying_ticker", self.underlying.clone()),
            ("expired", "false".into()),
            ("limit", "1000".into()),
            ("sort", "expiration_date".into()),
            ("order", "asc".into()),
            ("apiKey", self.api_key.clone()),
        ];
        let payload = self
            .http
            .get_json_with_query(&endpoint, &query)
            .map_err(|error| error.to_string())?;
        rows_from_payload(payload, &self.underlying)
    }

    pub fn equity_tickers(&self) -> Result<Vec<MassiveMarketRow>, String> {
        let endpoint = format!("{}/v3/reference/tickers", self.base_url);
        let query = vec![
            ("market", "stocks".into()),
            ("active", "true".into()),
            ("limit", "1000".into()),
            ("apiKey", self.api_key.clone()),
        ];
        let payload = self
            .http
            .get_json_with_query(&endpoint, &query)
            .map_err(|error| error.to_string())?;
        equity_rows_from_payload(payload)
    }
}

impl MassiveMarketClient for MassiveStocksRestClient {
    fn load_markets(&mut self) -> Result<Vec<MassiveMarketRow>, String> {
        if self.underlying.is_empty() {
            self.equity_tickers()
        } else {
            self.option_contracts()
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

fn rows_from_payload(payload: Value, underlying: &str) -> Result<Vec<MassiveMarketRow>, String> {
    let rows = payload
        .get("results")
        .and_then(Value::as_array)
        .ok_or_else(|| "Massive options response has no results list".to_string())?;
    Ok(rows
        .iter()
        .filter_map(|value| {
            let ticker = value.get("ticker")?.as_str()?.to_string();
            Some(MassiveMarketRow {
                ticker,
                market_type: "option".into(),
                base: Some(underlying.to_string()),
                quote: Some("USD".into()),
                active: value.get("active").and_then(Value::as_bool).unwrap_or(true),
                price_tick: Some("0.01".into()),
                amount_tick: Some("1".into()),
                price_precision: 2,
                amount_precision: 0,
                underlying: Some(underlying.to_string()),
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
    use super::{equity_rows_from_payload, rows_from_payload};

    #[test]
    fn maps_massive_option_contract_payload_to_market_rows() {
        let rows = rows_from_payload(
            serde_json::json!({
                "results": [{
                    "ticker": "O:SPY260821C00500000",
                    "expiration_date": "2026-08-21",
                    "strike_price": 500,
                    "contract_type": "call",
                    "shares_per_contract": 100,
                    "active": true
                }]
            }),
            "SPY",
        )
        .unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].ticker, "O:SPY260821C00500000");
        assert_eq!(rows[0].strike.as_deref(), Some("500"));
        assert!(rows[0].expiry_unix_nanos.is_some());
    }

    #[test]
    fn rejects_massive_payload_without_results() {
        let error = rows_from_payload(serde_json::json!({}), "SPY").unwrap_err();
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
