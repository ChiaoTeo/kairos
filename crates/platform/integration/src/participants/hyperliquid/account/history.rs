//! Typed Hyperliquid fills, funding payments, and non-funding ledger records.

use kairos_primitives::integration::ParticipantSymbol;
use kairos_primitives::reference::Currency;
use kairos_primitives::time::UnixNanos;
use serde_json::Value;

use crate::{ExternalDecimal, IntegrationError, OrderSide};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HyperliquidHistoryQuery {
    pub start_unix_nanos: UnixNanos,
    pub end_unix_nanos: Option<UnixNanos>,
    pub aggregate_fills_by_time: bool,
}

impl HyperliquidHistoryQuery {
    pub fn new(start_unix_nanos: UnixNanos) -> Self {
        Self {
            start_unix_nanos,
            end_unix_nanos: None,
            aggregate_fills_by_time: false,
        }
    }

    pub(crate) fn validate(&self) -> Result<(), IntegrationError> {
        if self
            .end_unix_nanos
            .is_some_and(|end| self.start_unix_nanos.get() > end.get())
        {
            return Err(IntegrationError::InvalidRequest(
                "Hyperliquid history start must not exceed end".into(),
            ));
        }
        Ok(())
    }

    pub(crate) fn start_millis(&self) -> u64 {
        self.start_unix_nanos.get() / 1_000_000
    }

    pub(crate) fn end_millis(&self) -> Option<u64> {
        self.end_unix_nanos.map(|value| value.get() / 1_000_000)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HyperliquidHistoryPage<T> {
    pub records: Vec<T>,
    /// Exclusive timestamp suitable for the next inclusive `startTime` request.
    pub resume_after_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HyperliquidFillLiquidation {
    pub liquidated_user: Option<String>,
    pub mark_price: ExternalDecimal,
    pub method: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HyperliquidFillRecord {
    pub trade_id: String,
    pub order_id: String,
    pub client_order_id: Option<String>,
    pub transaction_hash: String,
    pub symbol: ParticipantSymbol,
    pub side: OrderSide,
    pub price: ExternalDecimal,
    pub quantity: ExternalDecimal,
    pub start_position: ExternalDecimal,
    pub direction: String,
    pub closed_pnl: ExternalDecimal,
    pub crossed: bool,
    pub fee: ExternalDecimal,
    pub fee_token: Currency,
    pub builder_fee: Option<ExternalDecimal>,
    pub liquidation: Option<HyperliquidFillLiquidation>,
    pub executed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HyperliquidFundingRecord {
    pub transaction_hash: String,
    pub symbol: ParticipantSymbol,
    /// Positive means received and negative means paid.
    pub usdc: ExternalDecimal,
    pub signed_position_size: ExternalDecimal,
    pub funding_rate: ExternalDecimal,
    pub sample_count: Option<u64>,
    pub occurred_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HyperliquidLedgerPosition {
    pub symbol: ParticipantSymbol,
    pub signed_size: ExternalDecimal,
}

/// A provider-native, normalized ledger record of every documented ledger delta.
///
/// Fields that do not apply to a delta kind remain `None`. Keeping the provider
/// discriminator makes additions forward-compatible without exposing raw JSON.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HyperliquidLedgerRecord {
    pub transaction_hash: String,
    pub kind: String,
    pub usdc: Option<ExternalDecimal>,
    pub amount: Option<ExternalDecimal>,
    pub token: Option<String>,
    pub usdc_value: Option<ExternalDecimal>,
    pub fee: Option<ExternalDecimal>,
    pub nonce: Option<u64>,
    pub user: Option<String>,
    pub destination: Option<String>,
    pub vault: Option<String>,
    pub to_perp: Option<bool>,
    pub account_value: Option<ExternalDecimal>,
    pub leverage_type: Option<String>,
    pub liquidated_positions: Vec<HyperliquidLedgerPosition>,
    pub requested_usd: Option<ExternalDecimal>,
    pub commission: Option<ExternalDecimal>,
    pub closing_cost: Option<ExternalDecimal>,
    pub basis: Option<ExternalDecimal>,
    pub net_withdrawn_usd: Option<ExternalDecimal>,
    pub occurred_at_unix_nanos: UnixNanos,
}

pub(crate) fn fills(
    payload: &Value,
) -> Result<HyperliquidHistoryPage<HyperliquidFillRecord>, IntegrationError> {
    let records = rows(payload, "fills")?
        .iter()
        .map(|row| {
            let liquidation = row
                .get("liquidation")
                .filter(|value| !value.is_null())
                .map(|value| {
                    Ok(HyperliquidFillLiquidation {
                        liquidated_user: optional_text(value, "liquidatedUser"),
                        mark_price: decimal(value, "markPx")?,
                        method: required_text(value, "method")?,
                    })
                })
                .transpose()?;
            Ok(HyperliquidFillRecord {
                trade_id: scalar(row, "tid")?,
                order_id: scalar(row, "oid")?,
                client_order_id: optional_text(row, "cloid"),
                transaction_hash: required_text(row, "hash")?,
                symbol: ParticipantSymbol::new(required_text(row, "coin")?)
                    .map_err(payload_error)?,
                side: if required_text(row, "side")? == "A" {
                    OrderSide::Sell
                } else {
                    OrderSide::Buy
                },
                price: decimal(row, "px")?,
                quantity: decimal(row, "sz")?,
                start_position: decimal(row, "startPosition")?,
                direction: required_text(row, "dir")?,
                closed_pnl: decimal(row, "closedPnl")?,
                crossed: required_bool(row, "crossed")?,
                fee: decimal(row, "fee")?,
                fee_token: Currency::new(required_text(row, "feeToken")?).map_err(payload_error)?,
                builder_fee: optional_decimal(row, "builderFee")?,
                liquidation,
                executed_at_unix_nanos: millis(row, "time")?,
            })
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    Ok(page(records, |record| record.executed_at_unix_nanos))
}

pub(crate) fn funding(
    payload: &Value,
) -> Result<HyperliquidHistoryPage<HyperliquidFundingRecord>, IntegrationError> {
    let records = rows(payload, "funding")?
        .iter()
        .map(|row| {
            let delta = row.get("delta").ok_or_else(|| {
                IntegrationError::InvalidPayload("Hyperliquid funding delta is missing".into())
            })?;
            Ok(HyperliquidFundingRecord {
                transaction_hash: required_text(row, "hash")?,
                symbol: ParticipantSymbol::new(required_text(delta, "coin")?)
                    .map_err(payload_error)?,
                usdc: decimal(delta, "usdc")?,
                signed_position_size: decimal(delta, "szi")?,
                funding_rate: decimal(delta, "fundingRate")?,
                sample_count: optional_u64(delta, "nSamples")?,
                occurred_at_unix_nanos: millis(row, "time")?,
            })
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    Ok(page(records, |record| record.occurred_at_unix_nanos))
}

pub(crate) fn ledger(
    payload: &Value,
) -> Result<HyperliquidHistoryPage<HyperliquidLedgerRecord>, IntegrationError> {
    let records = rows(payload, "non-funding ledger")?
        .iter()
        .map(|row| {
            let delta = row.get("delta").ok_or_else(|| {
                IntegrationError::InvalidPayload("Hyperliquid ledger delta is missing".into())
            })?;
            let positions = delta
                .get("liquidatedPositions")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .map(|position| {
                    Ok(HyperliquidLedgerPosition {
                        symbol: ParticipantSymbol::new(required_text(position, "coin")?)
                            .map_err(payload_error)?,
                        signed_size: decimal(position, "szi")?,
                    })
                })
                .collect::<Result<Vec<_>, IntegrationError>>()?;
            Ok(HyperliquidLedgerRecord {
                transaction_hash: required_text(row, "hash")?,
                kind: required_text(delta, "type")?,
                usdc: optional_decimal(delta, "usdc")?,
                amount: optional_decimal(delta, "amount")?,
                token: optional_text(delta, "token"),
                usdc_value: optional_decimal(delta, "usdcValue")?,
                fee: optional_decimal(delta, "fee")?,
                nonce: optional_u64(delta, "nonce")?,
                user: optional_text(delta, "user"),
                destination: optional_text(delta, "destination"),
                vault: optional_text(delta, "vault"),
                to_perp: optional_bool(delta, "toPerp")?,
                account_value: optional_decimal(delta, "accountValue")?,
                leverage_type: optional_text(delta, "leverageType"),
                liquidated_positions: positions,
                requested_usd: optional_decimal(delta, "requestedUsd")?,
                commission: optional_decimal(delta, "commission")?,
                closing_cost: optional_decimal(delta, "closingCost")?,
                basis: optional_decimal(delta, "basis")?,
                net_withdrawn_usd: optional_decimal(delta, "netWithdrawnUsd")?,
                occurred_at_unix_nanos: millis(row, "time")?,
            })
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    Ok(page(records, |record| record.occurred_at_unix_nanos))
}

fn page<T>(records: Vec<T>, timestamp: impl Fn(&T) -> UnixNanos) -> HyperliquidHistoryPage<T> {
    let resume_after_unix_nanos = records
        .iter()
        .map(timestamp)
        .max_by_key(|value| value.get())
        .map(|value| UnixNanos::from(value.get().saturating_add(1)));
    HyperliquidHistoryPage {
        records,
        resume_after_unix_nanos,
    }
}

fn rows<'a>(payload: &'a Value, kind: &str) -> Result<&'a Vec<Value>, IntegrationError> {
    payload.as_array().ok_or_else(|| {
        IntegrationError::InvalidPayload(format!("Hyperliquid {kind} must be an array"))
    })
}

fn scalar(row: &Value, field: &str) -> Result<String, IntegrationError> {
    row.get(field)
        .and_then(|value| match value {
            Value::String(value) => Some(value.clone()),
            Value::Number(value) => Some(value.to_string()),
            _ => None,
        })
        .ok_or_else(|| IntegrationError::InvalidPayload(format!("Hyperliquid {field} is missing")))
}

fn required_text(row: &Value, field: &str) -> Result<String, IntegrationError> {
    optional_text(row, field)
        .ok_or_else(|| IntegrationError::InvalidPayload(format!("Hyperliquid {field} is missing")))
}

fn optional_text(row: &Value, field: &str) -> Option<String> {
    row.get(field)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn decimal(row: &Value, field: &str) -> Result<ExternalDecimal, IntegrationError> {
    ExternalDecimal::parse(&scalar(row, field)?).map_err(IntegrationError::InvalidPayload)
}

fn optional_decimal(row: &Value, field: &str) -> Result<Option<ExternalDecimal>, IntegrationError> {
    row.get(field)
        .filter(|value| !value.is_null())
        .map(|_| decimal(row, field))
        .transpose()
}

fn optional_u64(row: &Value, field: &str) -> Result<Option<u64>, IntegrationError> {
    row.get(field)
        .filter(|value| !value.is_null())
        .map(|_| {
            scalar(row, field)?
                .parse::<u64>()
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
        })
        .transpose()
}

fn required_bool(row: &Value, field: &str) -> Result<bool, IntegrationError> {
    row.get(field)
        .and_then(Value::as_bool)
        .ok_or_else(|| IntegrationError::InvalidPayload(format!("Hyperliquid {field} is missing")))
}

fn optional_bool(row: &Value, field: &str) -> Result<Option<bool>, IntegrationError> {
    match row.get(field) {
        None | Some(Value::Null) => Ok(None),
        Some(value) => value.as_bool().map(Some).ok_or_else(|| {
            IntegrationError::InvalidPayload(format!("Hyperliquid {field} must be a boolean"))
        }),
    }
}

fn millis(row: &Value, field: &str) -> Result<UnixNanos, IntegrationError> {
    scalar(row, field)?
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
    fn fixtures_preserve_fill_funding_and_ledger_identity() {
        let fill = fills(&json!([{
            "closedPnl":"1.2","coin":"BTC","crossed":true,"dir":"Close Long",
            "hash":"0xfill","oid":10,"px":"60000.1","side":"A",
            "startPosition":"0.02","sz":"0.01","time":1720000000000_u64,
            "fee":"-0.03","feeToken":"USDC","tid":20,"builderFee":"0.01",
            "liquidation":{"liquidatedUser":"0xuser","markPx":"59999","method":"market"}
        }]))
        .unwrap();
        let funding = funding(&json!([{
            "time":1720000000000_u64,"hash":"0xfunding","delta":{
                "type":"funding","coin":"BTC","usdc":"-0.2","szi":"0.01",
                "fundingRate":"0.0001","nSamples":8
            }
        }]))
        .unwrap();
        let ledger = ledger(&json!([{
            "time":1720000000000_u64,"hash":"0xledger","delta":{
                "type":"vaultWithdraw","vault":"0xvault","user":"0xuser",
                "requestedUsd":"1000.50","commission":"10.00","closingCost":"5.25",
                "basis":"950.00","netWithdrawnUsd":"985.25"
            }
        }]))
        .unwrap();

        assert_eq!(fill.records[0].side, OrderSide::Sell);
        assert_eq!(fill.records[0].fee.mantissa, -3);
        assert_eq!(funding.records[0].usdc.mantissa, -2);
        assert_eq!(funding.records[0].sample_count, Some(8));
        assert_eq!(ledger.records[0].kind, "vaultWithdraw");
        assert_eq!(ledger.records[0].net_withdrawn_usd.unwrap().mantissa, 98525);
        assert_eq!(
            fill.resume_after_unix_nanos.unwrap().get(),
            1_720_000_000_000_000_001
        );
    }

    #[test]
    fn query_validates_and_converts_nanoseconds_to_milliseconds() {
        let mut query = HyperliquidHistoryQuery::new(2_000_000.into());
        query.end_unix_nanos = Some(3_000_000.into());
        assert_eq!(query.start_millis(), 2);
        assert_eq!(query.end_millis(), Some(3));
        query.end_unix_nanos = Some(1_000_000.into());
        assert!(matches!(
            query.validate(),
            Err(IntegrationError::InvalidRequest(_))
        ));
    }
}
