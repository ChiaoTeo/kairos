use kairos_primitives::integration::ParticipantSymbol;
use kairos_primitives::reference::Currency;
use kairos_primitives::time::UnixNanos;
use serde_json::Value;

use crate::{
    ExternalInstrument, ExternalInstrumentKind, IntegrationError, MarketBar, MarketFundingRate,
    MarketIndexPrice, MarketMarkPrice, MarketOpenInterest, MarketOrderBook, MarketQuote,
    MarketTrade,
};

pub(crate) fn spot_instruments(value: &Value) -> Result<Vec<ExternalInstrument>, IntegrationError> {
    value
        .get("symbols")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance exchangeInfo symbols are missing".into())
        })?
        .iter()
        .map(|row| {
            let symbol = text(row, "symbol")?;
            let filters = row
                .get("filters")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            let filter = |kind: &str| {
                filters
                    .iter()
                    .find(|v| v.get("filterType").and_then(Value::as_str) == Some(kind))
            };
            Ok(ExternalInstrument {
                source_symbol: ParticipantSymbol::new(symbol).map_err(payload)?,
                source_venue: None,
                kind: ExternalInstrumentKind::Spot,
                base_currency: Some(Currency::new(text(row, "baseAsset")?).map_err(payload)?),
                quote_currency: Some(Currency::new(text(row, "quoteAsset")?).map_err(payload)?),
                settlement_currency: None,
                underlying: None,
                expiry_unix_nanos: None,
                strike: None,
                option_right: None,
                active: row.get("status").and_then(Value::as_str) == Some("TRADING"),
                price_tick: filter("PRICE_FILTER")
                    .and_then(|v| v.get("tickSize"))
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                quantity_tick: filter("LOT_SIZE")
                    .and_then(|v| v.get("stepSize"))
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                minimum_quantity: filter("LOT_SIZE")
                    .and_then(|v| v.get("minQty"))
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                minimum_notional: filter("NOTIONAL")
                    .or_else(|| filter("MIN_NOTIONAL"))
                    .and_then(|v| v.get("minNotional"))
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                contract_value: None,
                price_precision: row
                    .get("quotePrecision")
                    .and_then(Value::as_u64)
                    .and_then(|v| u32::try_from(v).ok()),
                quantity_precision: row
                    .get("baseAssetPrecision")
                    .and_then(Value::as_u64)
                    .and_then(|v| u32::try_from(v).ok()),
            })
        })
        .collect()
}

pub(crate) fn quote(
    symbol: &ParticipantSymbol,
    row: &Value,
) -> Result<MarketQuote, IntegrationError> {
    Ok(MarketQuote {
        venue: Default::default(),
        symbol: symbol.clone(),
        bid_price: parse(row.get("bidPrice"))?,
        bid_quantity: parse(row.get("bidQty"))?,
        ask_price: parse(row.get("askPrice"))?,
        ask_quantity: parse(row.get("askQty"))?,
        last_price: parse(row.get("lastPrice"))?,
        observed_at_unix_nanos: now(),
    })
}

pub(crate) fn equity_quote(
    symbol: &ParticipantSymbol,
    row: &Value,
) -> Result<MarketQuote, IntegrationError> {
    Ok(MarketQuote {
        venue: Default::default(),
        symbol: symbol.clone(),
        bid_price: parse(row.get("bidPrice"))?,
        bid_quantity: parse(row.get("bidSize"))?,
        ask_price: parse(row.get("askPrice"))?,
        ask_quantity: parse(row.get("askSize"))?,
        last_price: None,
        observed_at_unix_nanos: now(),
    })
}
pub(crate) fn mark_price(
    symbol: &ParticipantSymbol,
    row: &Value,
) -> Result<MarketMarkPrice, IntegrationError> {
    Ok(MarketMarkPrice {
        symbol: symbol.clone(),
        price: required(row.get("markPrice"))?,
        observed_at_unix_nanos: millis(row.get("time").and_then(Value::as_u64)),
    })
}
pub(crate) fn index_price(
    symbol: &ParticipantSymbol,
    row: &Value,
) -> Result<MarketIndexPrice, IntegrationError> {
    Ok(MarketIndexPrice {
        symbol: symbol.clone(),
        price: required(row.get("indexPrice"))?,
        observed_at_unix_nanos: millis(row.get("time").and_then(Value::as_u64)),
    })
}
pub(crate) fn funding_rate(
    symbol: &ParticipantSymbol,
    row: &Value,
) -> Result<MarketFundingRate, IntegrationError> {
    Ok(MarketFundingRate {
        symbol: symbol.clone(),
        rate: required(row.get("lastFundingRate"))?,
        next_funding_at_unix_nanos: row
            .get("nextFundingTime")
            .and_then(Value::as_u64)
            .map(millis_value),
        observed_at_unix_nanos: millis(row.get("time").and_then(Value::as_u64)),
    })
}
pub(crate) fn open_interest(
    symbol: &ParticipantSymbol,
    row: &Value,
) -> Result<MarketOpenInterest, IntegrationError> {
    Ok(MarketOpenInterest {
        symbol: symbol.clone(),
        quantity: required(row.get("openInterest"))?,
        observed_at_unix_nanos: millis(row.get("time").and_then(Value::as_u64)),
    })
}
pub(crate) fn book(
    symbol: &ParticipantSymbol,
    row: &Value,
) -> Result<MarketOrderBook, IntegrationError> {
    Ok(MarketOrderBook {
        symbol: symbol.clone(),
        bids: levels(row.get("bids"))?,
        asks: levels(row.get("asks"))?,
        sequence: row
            .get("lastUpdateId")
            .and_then(Value::as_u64)
            .map(Into::into),
        observed_at_unix_nanos: now(),
    })
}
pub(crate) fn trades(
    symbol: &ParticipantSymbol,
    value: &Value,
) -> Result<Vec<MarketTrade>, IntegrationError> {
    value
        .as_array()
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance trades response must be an array".into())
        })?
        .iter()
        .map(|row| {
            Ok(MarketTrade {
                symbol: symbol.clone(),
                participant_trade_id: row.get("id").and_then(Value::as_u64).map(|v| v.to_string()),
                price: required(row.get("price"))?,
                quantity: required(row.get("qty"))?,
                is_buyer_maker: row.get("isBuyerMaker").and_then(Value::as_bool),
                event_at_unix_nanos: millis(row.get("time").and_then(Value::as_u64)),
            })
        })
        .collect()
}
pub(crate) fn bars(
    symbol: &ParticipantSymbol,
    interval: &str,
    value: &Value,
) -> Result<Vec<MarketBar>, IntegrationError> {
    value
        .as_array()
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance klines response must be an array".into())
        })?
        .iter()
        .map(|row| {
            let row = row.as_array().ok_or_else(|| {
                IntegrationError::InvalidPayload("Binance kline row must be an array".into())
            })?;
            Ok(MarketBar {
                symbol: symbol.clone(),
                interval: interval.into(),
                open: required(row.get(1))?,
                high: required(row.get(2))?,
                low: required(row.get(3))?,
                close: required(row.get(4))?,
                volume: parse(row.get(5))?,
                opened_at_unix_nanos: millis(row.first().and_then(Value::as_u64)),
                closed_at_unix_nanos: row.get(6).and_then(Value::as_u64).map(millis_value),
                adjusted: None,
                derivation: "participant".into(),
            })
        })
        .collect()
}
pub(crate) fn derivative_instruments(
    value: &Value,
    kind: ExternalInstrumentKind,
) -> Result<Vec<ExternalInstrument>, IntegrationError> {
    value
        .get("symbols")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance derivative symbols are missing".into())
        })?
        .iter()
        .map(|row| {
            let symbol = text(row, "symbol")?;
            Ok(ExternalInstrument {
                source_symbol: ParticipantSymbol::new(symbol).map_err(payload)?,
                source_venue: None,
                kind,
                base_currency: row
                    .get("baseAsset")
                    .and_then(Value::as_str)
                    .map(Currency::new)
                    .transpose()
                    .map_err(payload)?,
                quote_currency: row
                    .get("quoteAsset")
                    .and_then(Value::as_str)
                    .map(Currency::new)
                    .transpose()
                    .map_err(payload)?,
                // exchangeInfo identifies collateral here, not settlement.
                // This generic parser has no product-specific settlement proof.
                settlement_currency: None,
                underlying: row
                    .get("underlying")
                    .and_then(Value::as_str)
                    .map(ParticipantSymbol::new)
                    .transpose()
                    .map_err(payload)?,
                expiry_unix_nanos: row
                    .get("deliveryDate")
                    .and_then(Value::as_u64)
                    .filter(|v| *v > 0)
                    .map(millis_value),
                strike: row
                    .get("strikePrice")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                option_right: row.get("side").and_then(Value::as_str).map(str::to_owned),
                active: row
                    .get("status")
                    .and_then(Value::as_str)
                    .is_none_or(|v| matches!(v, "TRADING" | "PENDING_TRADING")),
                price_tick: None,
                quantity_tick: None,
                minimum_quantity: None,
                minimum_notional: None,
                contract_value: row
                    .get("contractSize")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                price_precision: row
                    .get("pricePrecision")
                    .and_then(Value::as_u64)
                    .and_then(|v| u32::try_from(v).ok()),
                quantity_precision: row
                    .get("quantityPrecision")
                    .and_then(Value::as_u64)
                    .and_then(|v| u32::try_from(v).ok()),
            })
        })
        .collect()
}
#[cfg(test)]
mod settlement_tests {
    #[test]
    fn generic_derivative_parser_does_not_treat_margin_as_settlement() {
        for margin in ["USDT", "BTC"] {
            let payload = serde_json::json!({"symbols": [{
                "symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
                "marginAsset": margin, "status": "TRADING"
            }]});
            let instruments =
                super::derivative_instruments(&payload, crate::ExternalInstrumentKind::Perpetual)
                    .unwrap();
            assert_eq!(instruments.len(), 1);
            assert_eq!(
                instruments[0].quote_currency.as_ref().unwrap().as_str(),
                "USDT"
            );
            assert!(instruments[0].settlement_currency.is_none());
        }
    }
}

fn levels(
    value: Option<&Value>,
) -> Result<
    Vec<(
        kairos_primitives::decimal::Price,
        kairos_primitives::decimal::Quantity,
    )>,
    IntegrationError,
> {
    value
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|row| {
            let row = row.as_array().ok_or_else(|| {
                IntegrationError::InvalidPayload("Binance book level must be an array".into())
            })?;
            Ok((required(row.first())?, required(row.get(1))?))
        })
        .collect()
}
fn text<'a>(row: &'a Value, field: &str) -> Result<&'a str, IntegrationError> {
    row.get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| IntegrationError::InvalidPayload(format!("Binance {field} is missing")))
}
fn parse<T: std::str::FromStr>(value: Option<&Value>) -> Result<Option<T>, IntegrationError>
where
    T::Err: std::fmt::Display,
{
    value
        .and_then(|value| match value {
            Value::String(value) if !value.is_empty() => Some(value.clone()),
            Value::Number(value) => Some(value.to_string()),
            _ => None,
        })
        .map(|value| value.parse())
        .transpose()
        .map_err(payload)
}
fn required<T: std::str::FromStr>(value: Option<&Value>) -> Result<T, IntegrationError>
where
    T::Err: std::fmt::Display,
{
    value
        .and_then(Value::as_str)
        .ok_or_else(|| IntegrationError::InvalidPayload("Binance numeric field is missing".into()))?
        .parse()
        .map_err(payload)
}
fn payload(error: impl std::fmt::Display) -> IntegrationError {
    IntegrationError::InvalidPayload(error.to_string())
}
fn millis(value: Option<u64>) -> UnixNanos {
    value.map(millis_value).unwrap_or_else(now)
}
fn millis_value(value: u64) -> UnixNanos {
    UnixNanos::from(value.saturating_mul(1_000_000))
}
fn now() -> UnixNanos {
    use std::time::{SystemTime, UNIX_EPOCH};
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|v| v.as_nanos())
        .unwrap_or_default();
    UnixNanos::from(u64::try_from(nanos).unwrap_or(u64::MAX))
}
