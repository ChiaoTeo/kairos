use std::str::FromStr;

use rust_decimal::Decimal as RustDecimal;

use super::{account::ExternalDecimal, execution::DecimalValue};

impl DecimalValue {
    pub fn parse(value: &str) -> Result<Self, String> {
        from_rust(
            RustDecimal::from_str_exact(value.trim())
                .map_err(|error| format!("invalid decimal: {error}"))?,
        )
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

    pub fn format_fixed(self) -> Result<String, String> {
        Ok(self.as_rust()?.to_string())
    }

    fn as_rust(self) -> Result<RustDecimal, String> {
        RustDecimal::try_new(self.mantissa, u32::from(self.scale))
            .map_err(|error| format!("invalid decimal: {error}"))
    }
}

impl ExternalDecimal {
    pub fn parse(value: &str) -> Result<Self, String> {
        let value = RustDecimal::from_str_exact(value.trim())
            .map_err(|error| format!("invalid decimal: {error}"))?;
        Ok(Self::new(
            i64::try_from(value.mantissa()).map_err(|_| "decimal overflow".to_string())?,
            u8::try_from(value.scale()).map_err(|_| "decimal scale overflow".to_string())?,
        ))
    }

    pub fn format_fixed(self) -> Result<String, String> {
        RustDecimal::try_new(self.mantissa, u32::from(self.scale))
            .map(|value| value.to_string())
            .map_err(|error| format!("invalid decimal: {error}"))
    }

    pub fn rescale_exact(self, scale: u8) -> Result<Self, String> {
        let value = RustDecimal::try_new(self.mantissa, u32::from(self.scale))
            .map_err(|error| format!("invalid decimal: {error}"))?;
        let value = rescale(value, scale)?;
        Ok(Self::new(
            i64::try_from(value.mantissa()).map_err(|_| "decimal overflow".to_string())?,
            u8::try_from(value.scale()).map_err(|_| "decimal scale overflow".to_string())?,
        ))
    }
}

fn rescale(value: RustDecimal, scale: u8) -> Result<RustDecimal, String> {
    let mut candidate = value;
    if u32::from(scale) > RustDecimal::MAX_SCALE {
        return Err("decimal scale overflow".into());
    }
    candidate.rescale(u32::from(scale));
    if candidate.normalize() != value.normalize() {
        return Err("decimal precision exceeds target scale".into());
    }
    Ok(candidate)
}

fn from_rust(value: RustDecimal) -> Result<DecimalValue, String> {
    Ok(DecimalValue::new(
        i64::try_from(value.mantissa()).map_err(|_| "decimal overflow".to_string())?,
        u8::try_from(value.scale()).map_err(|_| "decimal scale overflow".to_string())?,
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
        assert!(DecimalValue::parse("1.2310")
            .unwrap()
            .rescale_exact(2)
            .is_err());
    }
}
