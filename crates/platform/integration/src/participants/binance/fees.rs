use kairos_primitives::reference::{Currency, Symbol};
use serde_json::Value;

use crate::{
    ExternalDecimal, ExternalFeeComponent, ExternalFeeDiscount, ExternalFeeSchedule,
    IntegrationError,
};

pub(crate) fn spot(value: &Value) -> Result<ExternalFeeSchedule, IntegrationError> {
    let standard = component(value.get("standardCommission"))?;
    let maker = standard
        .as_ref()
        .and_then(|value| value.maker)
        .ok_or_else(|| payload("Binance Spot standard maker commission is missing"))?;
    let taker = standard
        .as_ref()
        .and_then(|value| value.taker)
        .ok_or_else(|| payload("Binance Spot standard taker commission is missing"))?;
    let discount = value
        .get("discount")
        .map(|discount| {
            Ok(ExternalFeeDiscount {
                enabled_for_account: discount.get("enabledForAccount").and_then(Value::as_bool),
                enabled_for_symbol: discount.get("enabledForSymbol").and_then(Value::as_bool),
                asset: discount
                    .get("discountAsset")
                    .and_then(Value::as_str)
                    .map(Currency::new)
                    .transpose()
                    .map_err(payload)?,
                rate: decimal(discount, "discount")?,
            })
        })
        .transpose()?;
    Ok(ExternalFeeSchedule {
        symbol: Symbol::new(required_text(value, "symbol")?).map_err(payload)?,
        maker,
        taker,
        buyer: standard.as_ref().and_then(|value| value.buyer),
        seller: standard.as_ref().and_then(|value| value.seller),
        standard,
        special: component(value.get("specialCommission"))?,
        tax: component(value.get("taxCommission"))?,
        discount,
        rpi: None,
    })
}

pub(crate) fn futures(value: &Value) -> Result<ExternalFeeSchedule, IntegrationError> {
    Ok(ExternalFeeSchedule {
        symbol: Symbol::new(required_text(value, "symbol")?).map_err(payload)?,
        maker: required_decimal(value, "makerCommissionRate")?,
        taker: required_decimal(value, "takerCommissionRate")?,
        buyer: None,
        seller: None,
        standard: None,
        special: None,
        tax: None,
        discount: None,
        rpi: decimal(value, "rpiCommissionRate")?,
    })
}

fn component(value: Option<&Value>) -> Result<Option<ExternalFeeComponent>, IntegrationError> {
    value
        .map(|value| {
            Ok(ExternalFeeComponent {
                maker: decimal(value, "maker")?,
                taker: decimal(value, "taker")?,
                buyer: decimal(value, "buyer")?,
                seller: decimal(value, "seller")?,
            })
        })
        .transpose()
}

fn required_text<'a>(value: &'a Value, field: &str) -> Result<&'a str, IntegrationError> {
    value
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| payload(format!("Binance commission {field} is missing")))
}

fn required_decimal(value: &Value, field: &str) -> Result<ExternalDecimal, IntegrationError> {
    decimal(value, field)?.ok_or_else(|| payload(format!("Binance commission {field} is missing")))
}

fn decimal(value: &Value, field: &str) -> Result<Option<ExternalDecimal>, IntegrationError> {
    value
        .get(field)
        .and_then(Value::as_str)
        .map(ExternalDecimal::parse)
        .transpose()
        .map_err(payload)
}

fn payload(message: impl ToString) -> IntegrationError {
    IntegrationError::InvalidPayload(message.to_string())
}

#[cfg(test)]
mod tests {
    use super::{futures, spot};

    #[test]
    fn spot_schedule_preserves_components_and_discount() {
        let value = serde_json::json!({
            "symbol": "BTCUSDT",
            "standardCommission": {"maker":"0.001", "taker":"0.002", "buyer":"0", "seller":"0"},
            "specialCommission": {"maker":"0.0001", "taker":"0.0002", "buyer":"0", "seller":"0"},
            "taxCommission": {"maker":"0", "taker":"0", "buyer":"0", "seller":"0"},
            "discount": {"enabledForAccount":true, "enabledForSymbol":true, "discountAsset":"BNB", "discount":"0.25"}
        });
        let schedule = spot(&value).expect("schedule");
        assert_eq!((schedule.maker.mantissa, schedule.maker.scale), (1, 3));
        assert_eq!(
            schedule
                .discount
                .expect("discount")
                .asset
                .expect("asset")
                .as_str(),
            "BNB"
        );
    }

    #[test]
    fn futures_schedule_preserves_rpi_rate() {
        let value = serde_json::json!({
            "symbol":"BTCUSDT", "makerCommissionRate":"0.0002",
            "takerCommissionRate":"0.0005", "rpiCommissionRate":"0.0001"
        });
        let rpi = futures(&value).expect("schedule").rpi.expect("rpi");
        assert_eq!((rpi.mantissa, rpi.scale), (1, 4));
    }
}
