//! Typed OKX fills and account-bill records.

use kairos_primitives::{Currency, ParticipantSymbol, UnixNanos};
use serde_json::Value;

use crate::{ExternalDecimal, IntegrationError, OrderSide};

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct OkxHistoryQuery {
    pub instrument_type: Option<String>,
    pub instrument: Option<ParticipantSymbol>,
    pub begin_unix_nanos: Option<UnixNanos>,
    pub end_unix_nanos: Option<UnixNanos>,
    pub after: Option<String>,
    pub before: Option<String>,
    pub limit: Option<u16>,
}

impl OkxHistoryQuery {
    pub(crate) fn params(&self) -> Result<Vec<(&'static str, String)>, IntegrationError> {
        if let (Some(begin), Some(end)) = (self.begin_unix_nanos, self.end_unix_nanos) {
            if begin.get() > end.get() {
                return Err(IntegrationError::InvalidRequest(
                    "OKX history begin must not exceed end".into(),
                ));
            }
        }
        let mut params = Vec::new();
        if let Some(value) = &self.instrument_type {
            params.push(("instType", value.to_ascii_uppercase()));
        }
        if let Some(value) = &self.instrument {
            params.push(("instId", value.to_string()));
        }
        if let Some(value) = self.begin_unix_nanos {
            params.push(("begin", (value.get() / 1_000_000).to_string()));
        }
        if let Some(value) = self.end_unix_nanos {
            params.push(("end", (value.get() / 1_000_000).to_string()));
        }
        if let Some(value) = &self.after {
            params.push(("after", value.clone()));
        }
        if let Some(value) = &self.before {
            params.push(("before", value.clone()));
        }
        params.push(("limit", self.limit.unwrap_or(100).clamp(1, 100).to_string()));
        Ok(params)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OkxFillRecord {
    pub trade_id: String,
    pub order_id: String,
    pub instrument: ParticipantSymbol,
    pub side: OrderSide,
    pub price: ExternalDecimal,
    pub quantity: ExternalDecimal,
    pub fee: Option<ExternalDecimal>,
    pub fee_currency: Option<Currency>,
    pub executed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OkxBillRecord {
    pub bill_id: String,
    pub currency: Currency,
    pub balance_change: ExternalDecimal,
    pub balance: Option<ExternalDecimal>,
    pub bill_type: String,
    pub subtype: String,
    pub instrument: Option<ParticipantSymbol>,
    pub order_id: Option<String>,
    pub occurred_at_unix_nanos: UnixNanos,
}

pub(crate) fn fills(payload: &Value) -> Result<Vec<OkxFillRecord>, IntegrationError> {
    rows(payload)?
        .iter()
        .map(|row| {
            Ok(OkxFillRecord {
                trade_id: required(row, "tradeId")?,
                order_id: required(row, "ordId")?,
                instrument: ParticipantSymbol::new(required(row, "instId")?)
                    .map_err(payload_error)?,
                side: if required(row, "side")? == "sell" {
                    OrderSide::Sell
                } else {
                    OrderSide::Buy
                },
                price: decimal(row, "fillPx")?,
                quantity: decimal(row, "fillSz")?,
                fee: optional_decimal(row, "fee")?,
                fee_currency: optional(row, "feeCcy")
                    .map(Currency::new)
                    .transpose()
                    .map_err(payload_error)?,
                executed_at_unix_nanos: millis(row, "ts")?,
            })
        })
        .collect()
}

pub(crate) fn bills(payload: &Value) -> Result<Vec<OkxBillRecord>, IntegrationError> {
    rows(payload)?
        .iter()
        .map(|row| {
            Ok(OkxBillRecord {
                bill_id: required(row, "billId")?,
                currency: Currency::new(required(row, "ccy")?).map_err(payload_error)?,
                balance_change: decimal(row, "balChg")?,
                balance: optional_decimal(row, "bal")?,
                bill_type: required(row, "type")?,
                subtype: required(row, "subType")?,
                instrument: optional(row, "instId")
                    .map(ParticipantSymbol::new)
                    .transpose()
                    .map_err(payload_error)?,
                order_id: optional(row, "ordId"),
                occurred_at_unix_nanos: millis(row, "ts")?,
            })
        })
        .collect()
}

fn rows(payload: &Value) -> Result<&Vec<Value>, IntegrationError> {
    payload
        .get("data")
        .and_then(Value::as_array)
        .ok_or_else(|| IntegrationError::InvalidPayload("OKX history data is missing".into()))
}

fn required(row: &Value, field: &str) -> Result<String, IntegrationError> {
    optional(row, field)
        .ok_or_else(|| IntegrationError::InvalidPayload(format!("OKX {field} is missing")))
}

fn optional(row: &Value, field: &str) -> Option<String> {
    row.get(field)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn decimal(row: &Value, field: &str) -> Result<ExternalDecimal, IntegrationError> {
    ExternalDecimal::parse(&required(row, field)?).map_err(IntegrationError::InvalidPayload)
}

fn optional_decimal(row: &Value, field: &str) -> Result<Option<ExternalDecimal>, IntegrationError> {
    optional(row, field)
        .map(|value| ExternalDecimal::parse(&value).map_err(IntegrationError::InvalidPayload))
        .transpose()
}

fn millis(row: &Value, field: &str) -> Result<UnixNanos, IntegrationError> {
    required(row, field)?
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
    fn fill_and_bill_fixtures_preserve_signed_fees_and_balance_changes() {
        let fill = fills(&json!({"data":[{
            "tradeId":"10","ordId":"20","instId":"BTC-USDT-SWAP","side":"buy",
            "fillPx":"60000.1","fillSz":"0.01","fee":"-0.2","feeCcy":"USDT",
            "ts":"1720000000000"
        }]}))
        .unwrap()
        .remove(0);
        let bill = bills(&json!({"data":[{
            "billId":"30","ccy":"USDT","balChg":"-0.2","bal":"99.8",
            "type":"2","subType":"1","instId":"BTC-USDT-SWAP","ordId":"20",
            "ts":"1720000000000"
        }]}))
        .unwrap()
        .remove(0);

        assert_eq!(fill.fee.unwrap().mantissa, -2);
        assert_eq!(bill.balance_change.mantissa, -2);
        assert_eq!(bill.order_id.as_deref(), Some("20"));
    }

    #[test]
    fn query_is_bounded_and_converts_nanoseconds_to_milliseconds() {
        let query = OkxHistoryQuery {
            begin_unix_nanos: Some(2_000_000.into()),
            end_unix_nanos: Some(3_000_000.into()),
            limit: Some(500),
            ..Default::default()
        };
        let params = query.params().unwrap();
        assert!(params.contains(&("begin", "2".into())));
        assert!(params.contains(&("limit", "100".into())));
    }
}
