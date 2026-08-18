//! Typed Binance trade, commission, funding, and income records.

use kairos_primitives::{Currency, ParticipantSymbol, UnixNanos};
use serde_json::Value;

use crate::{ExternalDecimal, IntegrationError, OrderSide};

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct BinanceHistoryQuery {
    pub symbol: Option<ParticipantSymbol>,
    pub start_unix_nanos: Option<UnixNanos>,
    pub end_unix_nanos: Option<UnixNanos>,
    pub from_id: Option<u64>,
    pub income_type: Option<String>,
    pub limit: Option<u16>,
}

impl BinanceHistoryQuery {
    pub(crate) fn params(
        &self,
        require_symbol: bool,
        income: bool,
    ) -> Result<Vec<(&'static str, String)>, IntegrationError> {
        if let (Some(start), Some(end)) = (self.start_unix_nanos, self.end_unix_nanos) {
            if start.get() > end.get() {
                return Err(IntegrationError::InvalidRequest(
                    "Binance history start must not exceed end".into(),
                ));
            }
        }
        if require_symbol && self.symbol.is_none() {
            return Err(IntegrationError::InvalidRequest(
                "Binance trade history requires symbol".into(),
            ));
        }
        let mut params = Vec::new();
        if let Some(value) = &self.symbol {
            params.push(("symbol", value.to_string()));
        }
        if let Some(value) = self.start_unix_nanos {
            params.push(("startTime", (value.get() / 1_000_000).to_string()));
        }
        if let Some(value) = self.end_unix_nanos {
            params.push(("endTime", (value.get() / 1_000_000).to_string()));
        }
        if !income {
            if let Some(value) = self.from_id {
                params.push(("fromId", value.to_string()));
            }
        }
        if income {
            if let Some(value) = &self.income_type {
                params.push(("incomeType", value.clone()));
            }
        }
        params.push((
            "limit",
            self.limit.unwrap_or(500).clamp(1, 1_000).to_string(),
        ));
        Ok(params)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceTradeRecord {
    pub trade_id: String,
    pub order_id: String,
    pub symbol: ParticipantSymbol,
    pub side: OrderSide,
    pub price: ExternalDecimal,
    pub quantity: ExternalDecimal,
    pub realized_pnl: Option<ExternalDecimal>,
    pub commission: Option<ExternalDecimal>,
    pub commission_asset: Option<Currency>,
    pub executed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceIncomeRecord {
    pub transaction_id: String,
    pub income_type: String,
    pub asset: Currency,
    pub amount: ExternalDecimal,
    pub symbol: Option<ParticipantSymbol>,
    pub occurred_at_unix_nanos: UnixNanos,
    pub provider_info: Option<String>,
}

pub(crate) fn trades(payload: &Value) -> Result<Vec<BinanceTradeRecord>, IntegrationError> {
    payload
        .as_array()
        .ok_or_else(|| IntegrationError::InvalidPayload("Binance trades must be an array".into()))?
        .iter()
        .map(|row| {
            let side = match row.get("side").and_then(Value::as_str) {
                Some("SELL") => OrderSide::Sell,
                Some("BUY") => OrderSide::Buy,
                _ if row
                    .get("buyer")
                    .or_else(|| row.get("isBuyer"))
                    .and_then(Value::as_bool)
                    == Some(false) =>
                {
                    OrderSide::Sell
                }
                _ => OrderSide::Buy,
            };
            Ok(BinanceTradeRecord {
                trade_id: scalar_alias(row, &["id", "tradeId"])?,
                order_id: scalar(row, "orderId")?,
                symbol: ParticipantSymbol::new(required_text(row, "symbol")?)
                    .map_err(payload_error)?,
                side,
                price: decimal(row, "price")?,
                quantity: decimal_alias(row, &["qty", "quantity"])?,
                realized_pnl: optional_decimal(row, "realizedPnl")?,
                commission: optional_decimal_alias(row, &["commission", "fee"])?,
                commission_asset: optional_text_alias(row, &["commissionAsset", "feeAsset"])
                    .map(Currency::new)
                    .transpose()
                    .map_err(payload_error)?,
                executed_at_unix_nanos: millis_alias(row, &["time", "tradeTime", "createTime"])?,
            })
        })
        .collect()
}

pub(crate) fn income(payload: &Value) -> Result<Vec<BinanceIncomeRecord>, IntegrationError> {
    payload
        .as_array()
        .ok_or_else(|| IntegrationError::InvalidPayload("Binance income must be an array".into()))?
        .iter()
        .map(|row| {
            Ok(BinanceIncomeRecord {
                transaction_id: scalar(row, "tranId")?,
                income_type: required_text(row, "incomeType")?,
                asset: Currency::new(required_text(row, "asset")?).map_err(payload_error)?,
                amount: decimal(row, "income")?,
                symbol: optional_text(row, "symbol")
                    .map(ParticipantSymbol::new)
                    .transpose()
                    .map_err(payload_error)?,
                occurred_at_unix_nanos: millis(row, "time")?,
                provider_info: optional_text(row, "info"),
            })
        })
        .collect()
}

fn scalar(row: &Value, field: &str) -> Result<String, IntegrationError> {
    row.get(field)
        .and_then(|value| match value {
            Value::String(value) => Some(value.clone()),
            Value::Number(value) => Some(value.to_string()),
            _ => None,
        })
        .ok_or_else(|| IntegrationError::InvalidPayload(format!("Binance {field} is missing")))
}

fn scalar_alias(row: &Value, fields: &[&str]) -> Result<String, IntegrationError> {
    fields
        .iter()
        .find_map(|field| scalar(row, field).ok())
        .ok_or_else(|| {
            IntegrationError::InvalidPayload(format!("Binance {} is missing", fields.join("/")))
        })
}

fn required_text(row: &Value, field: &str) -> Result<String, IntegrationError> {
    optional_text(row, field)
        .ok_or_else(|| IntegrationError::InvalidPayload(format!("Binance {field} is missing")))
}

fn optional_text(row: &Value, field: &str) -> Option<String> {
    row.get(field)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn optional_text_alias(row: &Value, fields: &[&str]) -> Option<String> {
    fields.iter().find_map(|field| optional_text(row, field))
}

fn decimal(row: &Value, field: &str) -> Result<ExternalDecimal, IntegrationError> {
    ExternalDecimal::parse(&scalar(row, field)?).map_err(IntegrationError::InvalidPayload)
}

fn decimal_alias(row: &Value, fields: &[&str]) -> Result<ExternalDecimal, IntegrationError> {
    ExternalDecimal::parse(&scalar_alias(row, fields)?).map_err(IntegrationError::InvalidPayload)
}

fn optional_decimal(row: &Value, field: &str) -> Result<Option<ExternalDecimal>, IntegrationError> {
    row.get(field)
        .filter(|value| !value.is_null())
        .map(|_| decimal(row, field))
        .transpose()
}

fn optional_decimal_alias(
    row: &Value,
    fields: &[&str],
) -> Result<Option<ExternalDecimal>, IntegrationError> {
    fields
        .iter()
        .find(|field| row.get(**field).is_some_and(|value| !value.is_null()))
        .map(|field| decimal(row, field))
        .transpose()
}

fn millis(row: &Value, field: &str) -> Result<UnixNanos, IntegrationError> {
    scalar(row, field)?
        .parse::<u64>()
        .map(|value| UnixNanos::from(value.saturating_mul(1_000_000)))
        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
}

fn millis_alias(row: &Value, fields: &[&str]) -> Result<UnixNanos, IntegrationError> {
    scalar_alias(row, fields)?
        .parse::<u64>()
        .map(|value| UnixNanos::from(value.saturating_mul(1_000_000)))
        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
}

fn payload_error(error: impl std::fmt::Display) -> IntegrationError {
    IntegrationError::InvalidPayload(error.to_string())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn trade_and_income_keep_commission_funding_and_transaction_identity() {
        let trade = trades(&json!([{
            "symbol":"BTCUSDT","id":10,"orderId":20,"side":"BUY","price":"60000",
            "qty":"0.01","realizedPnl":"1.2","commission":"0.03",
            "commissionAsset":"USDT","time":1720000000000_u64
        }]))
        .unwrap()
        .remove(0);
        let funding = income(&json!([{
            "symbol":"BTCUSDT","incomeType":"FUNDING_FEE","income":"-0.2",
            "asset":"USDT","info":"","time":1720000000000_u64,"tranId":30
        }]))
        .unwrap()
        .remove(0);

        assert_eq!(trade.commission.unwrap().mantissa, 3);
        assert_eq!(funding.income_type, "FUNDING_FEE");
        assert_eq!(funding.amount.mantissa, -2);
        assert_eq!(funding.transaction_id, "30");
    }

    #[test]
    fn margin_and_options_trade_aliases_preserve_side_quantity_and_fee() {
        let margin = trades(&json!([{
            "symbol":"ETHUSDT","id":11,"orderId":21,"isBuyer":false,
            "price":"3000","qty":"0.5","commission":"0.01",
            "commissionAsset":"USDT","time":1720000000000_u64
        }]))
        .unwrap()
        .remove(0);
        let option = trades(&json!([{
            "symbol":"BTC-260925-100000-C","tradeId":12,"orderId":22,"side":"BUY",
            "price":"100","quantity":"0.2","fee":"0.02","feeAsset":"USDT",
            "createTime":1720000000001_u64
        }]))
        .unwrap()
        .remove(0);

        assert_eq!(margin.side, OrderSide::Sell);
        assert_eq!(option.trade_id, "12");
        assert_eq!(option.quantity, ExternalDecimal::new(2, 1));
        assert_eq!(option.commission, Some(ExternalDecimal::new(2, 2)));
    }
}
