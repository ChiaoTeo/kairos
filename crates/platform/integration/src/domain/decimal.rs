use std::str::FromStr;

use rust_decimal::Decimal as RustDecimal;
use serde::{Deserialize, Serialize};

/// Exact participant-neutral decimal parts used at Integration boundaries.
///
/// This is an integration representation, not a business quantity. Business
/// modules must convert it into `Quantity`, `Price`, `Money`, or another
/// semantic type before applying business rules.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize)]
pub struct DecimalValue {
    pub mantissa: i64,
    pub scale: u8,
}

impl DecimalValue {
    pub const fn new(mantissa: i64, scale: u8) -> Self {
        Self { mantissa, scale }
    }

    pub fn try_new(mantissa: i64, scale: u8) -> Result<Self, String> {
        kairos_primitives::decimal::DecimalParts::new(mantissa, scale)
            .map_err(|error| error.to_string())?;
        Ok(Self { mantissa, scale })
    }

    pub fn parse(value: &str) -> Result<Self, String> {
        let value = value
            .trim()
            .parse::<kairos_primitives::decimal::DecimalParts>()
            .map_err(|error| error.to_string())?;
        Ok(Self::new(value.mantissa(), value.scale()))
    }

    pub fn rescale_exact(self, scale: u8) -> Result<Self, String> {
        from_rust(rescale(self.as_rust()?, scale)?)
    }

    pub fn checked_add(self, other: Self) -> Result<Self, String> {
        from_rust(
            self.as_rust()?
                .checked_add(other.as_rust()?)
                .ok_or_else(|| "decimal addition overflow".to_string())?,
        )
    }

    pub fn normalized(self) -> Result<Self, String> {
        from_rust(self.as_rust()?.normalize())
    }

    pub fn format_fixed(self) -> Result<String, String> {
        Ok(self.as_rust()?.to_string())
    }

    fn as_rust(self) -> Result<RustDecimal, String> {
        if self.scale > kairos_primitives::decimal::MAX_DECIMAL_SCALE {
            return Err("decimal scale exceeds 18 digits".into());
        }
        RustDecimal::try_new(self.mantissa, u32::from(self.scale))
            .map_err(|error| format!("invalid decimal: {error}"))
    }
}

impl<'de> Deserialize<'de> for DecimalValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        #[derive(Deserialize)]
        struct RawDecimalValue {
            mantissa: i64,
            scale: u8,
        }

        let value = RawDecimalValue::deserialize(deserializer)?;
        Self::try_new(value.mantissa, value.scale).map_err(serde::de::Error::custom)
    }
}

fn rescale(value: RustDecimal, scale: u8) -> Result<RustDecimal, String> {
    let mut candidate = value;
    if scale > kairos_primitives::decimal::MAX_DECIMAL_SCALE {
        return Err("decimal scale exceeds 18 digits".into());
    }
    candidate.rescale(u32::from(scale));
    if candidate.normalize() != value.normalize() {
        return Err("decimal precision exceeds target scale".into());
    }
    Ok(candidate)
}

fn from_rust(value: RustDecimal) -> Result<DecimalValue, String> {
    let scale = u8::try_from(value.scale()).map_err(|_| "decimal scale overflow".to_string())?;
    if scale > kairos_primitives::decimal::MAX_DECIMAL_SCALE {
        return Err("decimal scale exceeds 18 digits".into());
    }
    Ok(DecimalValue::new(
        i64::try_from(value.mantissa()).map_err(|_| "decimal overflow".to_string())?,
        scale,
    ))
}

impl FromStr for DecimalValue {
    type Err = String;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        Self::parse(value)
    }
}

#[cfg(test)]
mod tests {
    use super::DecimalValue;

    #[test]
    fn parses_and_rescales_without_float_rounding() {
        let value = DecimalValue::parse("1.2300").unwrap();
        assert_eq!(value, DecimalValue::new(12300, 4));
        assert_eq!(value.rescale_exact(2).unwrap(), DecimalValue::new(123, 2));
        assert!(
            DecimalValue::parse("1.2310")
                .unwrap()
                .rescale_exact(2)
                .is_err()
        );
        assert!(DecimalValue::parse("0.0000000000000000001").is_err());
        assert!(value.rescale_exact(19).is_err());
        assert!(serde_json::from_str::<DecimalValue>(r#"{"mantissa":1,"scale":19}"#).is_err());
    }
}
