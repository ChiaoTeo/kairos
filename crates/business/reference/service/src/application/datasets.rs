//! Immutable Reference dataset preparation use cases.

use std::collections::BTreeSet;

use chrono::NaiveDate;
use serde::{Deserialize, Serialize};

use crate::domain::{ReferenceError, ReferenceResult};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OptionContractSnapshotRequest {
    pub underlying: String,
    pub as_of: String,
    pub expiration_start: String,
    pub expiration_end: String,
    pub option_right: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OptionContractInput {
    pub provider_symbol: String,
    pub source_venue: Option<String>,
    pub underlying: String,
    pub expiry_unix_nanos: u64,
    pub strike: String,
    pub option_right: String,
    pub contract_multiplier: String,
    pub active: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct OptionContractDatasetRecord {
    pub kind: String,
    pub schema_version: String,
    pub source_id: String,
    pub observed_at_unix_nanos: u64,
    pub available_at_unix_nanos: u64,
    pub as_of: String,
    pub instrument_id: String,
    pub market_id: String,
    pub listing_id: String,
    pub provider_symbol: String,
    pub source_venue: Option<String>,
    pub underlying_instrument_id: String,
    pub underlying: String,
    pub expiry_unix_nanos: u64,
    pub strike: String,
    pub option_right: String,
    pub contract_multiplier: String,
    pub status: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OptionContractSnapshotResult {
    pub snapshot_id: String,
    pub observed_at_unix_nanos: u64,
    pub records: Vec<OptionContractDatasetRecord>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CashDividendDatasetRequest {
    pub ticker: String,
    pub start_date: String,
    pub end_date: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CashDividendInput {
    pub id: String,
    pub ticker: String,
    pub ex_dividend_date: String,
    pub declaration_date: Option<String>,
    pub record_date: Option<String>,
    pub pay_date: Option<String>,
    pub cash_amount: Option<String>,
    pub split_adjusted_cash_amount: Option<String>,
    pub historical_adjustment_factor: Option<String>,
    pub currency: Option<String>,
    pub distribution_type: Option<String>,
    pub frequency: Option<u32>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct CashDividendDatasetRecord {
    pub kind: String,
    pub schema_version: String,
    pub source_id: String,
    pub observed_at_unix_nanos: u64,
    pub available_at_unix_nanos: u64,
    pub dividend_id: String,
    pub instrument_id: String,
    pub ticker: String,
    pub ex_dividend_date: String,
    pub declaration_date: Option<String>,
    pub record_date: Option<String>,
    pub pay_date: Option<String>,
    pub cash_amount: Option<String>,
    pub split_adjusted_cash_amount: Option<String>,
    pub historical_adjustment_factor: Option<String>,
    pub currency: Option<String>,
    pub distribution_type: Option<String>,
    pub frequency: Option<u32>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CashDividendDatasetResult {
    pub records: Vec<CashDividendDatasetRecord>,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct ReferenceDatasetApplication;

impl ReferenceDatasetApplication {
    pub fn cash_dividends(
        &self,
        request: &CashDividendDatasetRequest,
        inputs: Vec<CashDividendInput>,
    ) -> ReferenceResult<CashDividendDatasetResult> {
        let ticker = required_upper("ticker", &request.ticker)?;
        let start = date_start_unix_nanos(&request.start_date)?;
        let end = date_start_unix_nanos(&request.end_date)?;
        if start > end {
            return Err(ReferenceError::Provider(
                "dividend start date is after end date".into(),
            ));
        }
        let mut seen = BTreeSet::new();
        let mut records = Vec::new();
        for input in inputs {
            if required_upper("dividend ticker", &input.ticker)? != ticker {
                return Err(ReferenceError::Provider(format!(
                    "dividend {} belongs to another ticker",
                    input.id
                )));
            }
            if input.id.trim().is_empty() || !seen.insert(input.id.clone()) {
                return Err(ReferenceError::Provider(
                    "dividend identity is missing or duplicated".into(),
                ));
            }
            let observed = date_start_unix_nanos(&input.ex_dividend_date)?;
            if !(start..=end).contains(&observed) {
                return Err(ReferenceError::Provider(format!(
                    "dividend {} is outside the requested range",
                    input.id
                )));
            }
            let available = input
                .declaration_date
                .as_deref()
                .map(date_start_unix_nanos)
                .transpose()?
                .unwrap_or(observed)
                .min(observed);
            let normalize =
                |value: Option<String>| value.as_deref().map(canonical_decimal).transpose();
            records.push(CashDividendDatasetRecord {
                kind: "cash-dividend".into(),
                schema_version: "1".into(),
                source_id: "massive".into(),
                observed_at_unix_nanos: observed,
                available_at_unix_nanos: available,
                dividend_id: input.id,
                instrument_id: format!("instrument:equity:US:{ticker}:common"),
                ticker: ticker.clone(),
                ex_dividend_date: input.ex_dividend_date,
                declaration_date: input.declaration_date,
                record_date: input.record_date,
                pay_date: input.pay_date,
                cash_amount: normalize(input.cash_amount)?,
                split_adjusted_cash_amount: normalize(input.split_adjusted_cash_amount)?,
                historical_adjustment_factor: normalize(input.historical_adjustment_factor)?,
                currency: input.currency,
                distribution_type: input.distribution_type,
                frequency: input.frequency,
            });
        }
        records.sort_by(|left, right| {
            left.observed_at_unix_nanos
                .cmp(&right.observed_at_unix_nanos)
                .then_with(|| left.dividend_id.cmp(&right.dividend_id))
        });
        Ok(CashDividendDatasetResult { records })
    }

    pub fn option_contract_snapshot(
        &self,
        request: &OptionContractSnapshotRequest,
        inputs: Vec<OptionContractInput>,
    ) -> ReferenceResult<OptionContractSnapshotResult> {
        let underlying = required_upper("underlying", &request.underlying)?;
        let observed_at_unix_nanos = date_start_unix_nanos(&request.as_of)?;
        let expiration_start = date_start_unix_nanos(&request.expiration_start)?;
        let expiration_end = date_start_unix_nanos(&request.expiration_end)?;
        if expiration_start > expiration_end {
            return Err(ReferenceError::Provider(
                "option snapshot expiration start is after end".into(),
            ));
        }
        let requested_right = request
            .option_right
            .as_deref()
            .map(canonical_right)
            .transpose()?;
        let mut seen = BTreeSet::new();
        let mut records = Vec::new();
        for input in inputs {
            if required_upper("input underlying", &input.underlying)? != underlying {
                return Err(ReferenceError::Provider(format!(
                    "option contract {} belongs to another underlying",
                    input.provider_symbol
                )));
            }
            if !(expiration_start..=expiration_end).contains(&input.expiry_unix_nanos) {
                return Err(ReferenceError::Provider(format!(
                    "option contract {} is outside the requested expiry range",
                    input.provider_symbol
                )));
            }
            let right = canonical_right(&input.option_right)?;
            if requested_right
                .as_deref()
                .is_some_and(|value| value != right)
            {
                return Err(ReferenceError::Provider(format!(
                    "option contract {} has an unexpected right",
                    input.provider_symbol
                )));
            }
            let strike = canonical_decimal(&input.strike)?;
            let multiplier = canonical_decimal(&input.contract_multiplier)?;
            let expiry = NaiveDate::from_ymd_opt(1970, 1, 1)
                .and_then(|epoch| {
                    epoch.checked_add_days(chrono::Days::new(
                        input.expiry_unix_nanos / 1_000_000_000 / 86_400,
                    ))
                })
                .ok_or_else(|| {
                    ReferenceError::Provider("option expiry cannot be represented".into())
                })?
                .format("%Y%m%d")
                .to_string();
            let instrument_id = format!("instrument:option:{underlying}:{expiry}:{strike}:{right}");
            if !seen.insert(instrument_id.clone()) {
                return Err(ReferenceError::Provider(format!(
                    "duplicate canonical option contract: {instrument_id}"
                )));
            }
            records.push(OptionContractDatasetRecord {
                kind: "option-contract".into(),
                schema_version: "1".into(),
                source_id: "massive".into(),
                observed_at_unix_nanos,
                available_at_unix_nanos: observed_at_unix_nanos,
                as_of: request.as_of.clone(),
                instrument_id,
                market_id: format!("market:massive:options:{}", input.provider_symbol),
                listing_id: format!("listing:massive:options:{}", input.provider_symbol),
                provider_symbol: input.provider_symbol,
                source_venue: input.source_venue,
                underlying_instrument_id: format!("instrument:equity:US:{underlying}:common"),
                underlying: underlying.clone(),
                expiry_unix_nanos: input.expiry_unix_nanos,
                strike,
                option_right: right,
                contract_multiplier: multiplier,
                status: if input.active {
                    "active-as-of".into()
                } else {
                    "inactive-as-of".into()
                },
            });
        }
        records.sort_by(|left, right| {
            left.expiry_unix_nanos
                .cmp(&right.expiry_unix_nanos)
                .then_with(|| left.strike.cmp(&right.strike))
                .then_with(|| left.option_right.cmp(&right.option_right))
                .then_with(|| left.provider_symbol.cmp(&right.provider_symbol))
        });
        Ok(OptionContractSnapshotResult {
            snapshot_id: format!("reference.option-contract/{underlying}/{}", request.as_of),
            observed_at_unix_nanos,
            records,
        })
    }
}

fn required_upper(name: &str, value: &str) -> ReferenceResult<String> {
    if value.trim().is_empty() {
        return Err(ReferenceError::Provider(format!("{name} is required")));
    }
    Ok(value.trim().to_ascii_uppercase())
}

fn canonical_right(value: &str) -> ReferenceResult<String> {
    match value.trim().to_ascii_lowercase().as_str() {
        "put" | "p" => Ok("P".into()),
        "call" | "c" => Ok("C".into()),
        other => Err(ReferenceError::Provider(format!(
            "unsupported option right: {other}"
        ))),
    }
}

fn canonical_decimal(value: &str) -> ReferenceResult<String> {
    let value = value.trim();
    if value.is_empty() || value.starts_with('-') {
        return Err(ReferenceError::Provider(
            "option decimal must be non-negative".into(),
        ));
    }
    let mut pieces = value.split('.');
    let integer = pieces.next().unwrap_or_default();
    let fraction = pieces.next();
    if pieces.next().is_some()
        || integer.is_empty()
        || !integer.chars().all(|value| value.is_ascii_digit())
        || fraction.is_some_and(|value| !value.chars().all(|item| item.is_ascii_digit()))
    {
        return Err(ReferenceError::Provider(format!(
            "invalid option decimal: {value}"
        )));
    }
    let integer = integer.trim_start_matches('0');
    let integer = if integer.is_empty() { "0" } else { integer };
    let fraction = fraction.unwrap_or_default().trim_end_matches('0');
    Ok(if fraction.is_empty() {
        integer.into()
    } else {
        format!("{integer}.{fraction}")
    })
}

fn date_start_unix_nanos(value: &str) -> ReferenceResult<u64> {
    let date = NaiveDate::parse_from_str(value, "%Y-%m-%d").map_err(|_| {
        ReferenceError::Provider(format!("invalid date (expected YYYY-MM-DD): {value}"))
    })?;
    let seconds = date
        .and_hms_opt(0, 0, 0)
        .ok_or_else(|| ReferenceError::Provider(format!("invalid date: {value}")))?
        .and_utc()
        .timestamp();
    u64::try_from(seconds)
        .ok()
        .and_then(|value| value.checked_mul(1_000_000_000))
        .ok_or_else(|| ReferenceError::Provider(format!("date is out of range: {value}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_deterministic_point_in_time_option_contract_records() {
        let result = ReferenceDatasetApplication
            .option_contract_snapshot(
                &OptionContractSnapshotRequest {
                    underlying: "spy".into(),
                    as_of: "2024-12-19".into(),
                    expiration_start: "2024-12-20".into(),
                    expiration_end: "2025-01-31".into(),
                    option_right: Some("put".into()),
                },
                vec![OptionContractInput {
                    provider_symbol: "O:SPY241220P00590000".into(),
                    source_venue: Some("BATO".into()),
                    underlying: "SPY".into(),
                    expiry_unix_nanos: 1_734_652_800_000_000_000,
                    strike: "590.000".into(),
                    option_right: "put".into(),
                    contract_multiplier: "100.0".into(),
                    active: true,
                }],
            )
            .unwrap();
        assert_eq!(result.records.len(), 1);
        assert_eq!(
            result.records[0].instrument_id,
            "instrument:option:SPY:20241220:590:P"
        );
        assert_eq!(result.records[0].contract_multiplier, "100");
        assert_eq!(
            result.snapshot_id,
            "reference.option-contract/SPY/2024-12-19"
        );
    }
}
