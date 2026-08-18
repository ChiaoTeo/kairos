use kairos_primitives::ParticipantSymbol;
use serde_json::Value;

use crate::IntegrationError;

rest_connection!(BinanceCopyTradingRestConnection, "advanced.copy.rest");

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceLeadTraderStatus {
    pub is_lead_trader: bool,
    pub portfolio_id: Option<String>,
    pub raw_status: Option<String>,
}

impl BinanceCopyTradingRestConnection {
    pub async fn futures_lead_trader_status(
        &mut self,
    ) -> Result<BinanceLeadTraderStatus, IntegrationError> {
        let value = self
            .service
            .signed_get("/sapi/v1/copyTrading/futures/userStatus", &[])
            .await?;
        let row = value.get("data").unwrap_or(&value);
        Ok(BinanceLeadTraderStatus {
            is_lead_trader: row
                .get("isLeadTrader")
                .or_else(|| row.get("leadTrader"))
                .and_then(Value::as_bool)
                .unwrap_or(false),
            portfolio_id: text(row, &["portfolioId", "leadPortfolioId"]),
            raw_status: text(row, &["status"]),
        })
    }

    pub async fn futures_lead_symbols(
        &mut self,
    ) -> Result<Vec<ParticipantSymbol>, IntegrationError> {
        let value = self
            .service
            .signed_get("/sapi/v1/copyTrading/futures/leadSymbol", &[])
            .await?;
        let rows = value
            .get("data")
            .unwrap_or(&value)
            .as_array()
            .ok_or_else(|| {
                IntegrationError::InvalidPayload(
                    "Binance Copy Trading leadSymbol response must be an array".into(),
                )
            })?;
        rows.iter()
            .filter_map(|row| {
                row.as_str()
                    .or_else(|| row.get("symbol").and_then(Value::as_str))
            })
            .map(|symbol| ParticipantSymbol::new(symbol).map_err(payload))
            .collect()
    }
}

fn text(value: &Value, fields: &[&str]) -> Option<String> {
    fields
        .iter()
        .find_map(|field| value.get(*field).and_then(Value::as_str))
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn payload(error: impl std::fmt::Display) -> IntegrationError {
    IntegrationError::InvalidPayload(error.to_string())
}
