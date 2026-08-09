use rust_decimal::Decimal as RustDecimal;
use serde::{Deserialize, Serialize};

use super::AccountDomainError;

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Decimal {
    pub mantissa: i64,
    pub scale: u8,
}

impl Decimal {
    pub const ZERO: Self = Self::new(0, 0);

    pub const fn new(mantissa: i64, scale: u8) -> Self {
        Self { mantissa, scale }
    }

    pub fn parse(value: &str) -> Result<Self, AccountDomainError> {
        let value = value
            .parse::<RustDecimal>()
            .map_err(|_| AccountDomainError::DecimalOverflow)?;
        Self::from_rust(value)
    }

    pub const fn is_zero(self) -> bool {
        self.mantissa == 0
    }

    pub const fn is_positive(self) -> bool {
        self.mantissa > 0
    }

    pub const fn is_negative(self) -> bool {
        self.mantissa < 0
    }

    pub fn checked_neg(self) -> Result<Self, AccountDomainError> {
        let mantissa = self
            .mantissa
            .checked_neg()
            .ok_or(AccountDomainError::DecimalOverflow)?;
        Ok(Self::new(mantissa, self.scale))
    }

    pub fn rescale(self, scale: u8) -> Result<Self, AccountDomainError> {
        let value = self.as_rust()?;
        if scale == self.scale {
            return Ok(self);
        }
        let mut candidate = value;
        if u32::from(scale) > RustDecimal::MAX_SCALE {
            return Err(AccountDomainError::DecimalOverflow);
        }
        candidate.rescale(u32::from(scale));
        let candidate = Self::from_rust(candidate)?;
        if candidate.as_rust()?.normalize() != value.normalize() {
            return Err(AccountDomainError::InexactRescale {
                from: self.scale,
                to: scale,
            });
        }
        Ok(candidate)
    }

    pub fn checked_add(self, other: Self) -> Result<Self, AccountDomainError> {
        let value = self
            .as_rust()?
            .checked_add(other.as_rust()?)
            .ok_or(AccountDomainError::DecimalOverflow)?;
        Self::from_rust(value)
    }

    pub fn checked_sub(self, other: Self) -> Result<Self, AccountDomainError> {
        self.checked_add(other.checked_neg()?)
    }

    pub fn checked_mul(self, other: Self) -> Result<Self, AccountDomainError> {
        let value = self
            .as_rust()?
            .checked_mul(other.as_rust()?)
            .ok_or(AccountDomainError::DecimalOverflow)?;
        Self::from_rust(value)
    }

    pub fn cmp_value(self, other: Self) -> Result<std::cmp::Ordering, AccountDomainError> {
        Ok(self.as_rust()?.cmp(&other.as_rust()?))
    }

    fn as_rust(self) -> Result<RustDecimal, AccountDomainError> {
        RustDecimal::try_new(self.mantissa, u32::from(self.scale))
            .map_err(|_| AccountDomainError::DecimalOverflow)
    }

    fn from_rust(value: RustDecimal) -> Result<Self, AccountDomainError> {
        let mantissa =
            i64::try_from(value.mantissa()).map_err(|_| AccountDomainError::DecimalOverflow)?;
        let scale = u8::try_from(value.scale()).map_err(|_| AccountDomainError::DecimalOverflow)?;
        Ok(Self::new(mantissa, scale))
    }
}

#[cfg(test)]
mod tests {
    use super::Decimal;
    use crate::domain::AccountDomainError;

    #[test]
    fn checked_arithmetic_aligns_scales_without_losing_precision() {
        assert_eq!(
            Decimal::new(125, 2)
                .checked_add(Decimal::new(2, 0))
                .unwrap(),
            Decimal::new(325, 2)
        );
        assert_eq!(
            Decimal::new(25, 1)
                .checked_mul(Decimal::new(40, 1))
                .unwrap(),
            Decimal::new(1_000, 2)
        );
    }

    #[test]
    fn downscaling_requires_an_exact_value() {
        assert_eq!(
            Decimal::new(101, 2).rescale(1),
            Err(AccountDomainError::InexactRescale { from: 2, to: 1 })
        );
    }

    #[test]
    fn values_with_different_scales_compare_exactly() {
        assert_eq!(
            Decimal::new(1, 0).cmp_value(Decimal::new(10, 1)).unwrap(),
            std::cmp::Ordering::Equal
        );
    }
}
