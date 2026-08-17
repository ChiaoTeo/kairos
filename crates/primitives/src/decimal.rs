use std::fmt;

use rust_decimal::Decimal as RustDecimal;
use serde::{Deserialize, Serialize};

use crate::DomainTypeError;

/// Domain decimals use an `i64` coefficient, so at most 18 fractional digits
/// can be represented without pretending that additional precision exists.
pub const MAX_DECIMAL_SCALE: u8 = 18;

fn normalize_decimal_parts(
    mut mantissa: i64,
    mut scale: u8,
    type_name: &'static str,
) -> Result<(i64, u8), DomainTypeError> {
    if scale > MAX_DECIMAL_SCALE {
        return Err(DomainTypeError::Invalid {
            type_name,
            reason: "decimal scale exceeds 18 digits",
        });
    }
    if mantissa == 0 {
        return Ok((0, 0));
    }
    while scale > 0 && mantissa % 10 == 0 {
        mantissa /= 10;
        scale -= 1;
    }
    Ok((mantissa, scale))
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Hash)]
pub struct Quantity {
    mantissa: i64,
    scale: u8,
}

/// A signed asset/inventory quantity. Order quantities use `Quantity`; this
/// type is reserved for positions and balance deltas that may cross zero.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Hash)]
pub struct SignedQuantity {
    mantissa: i64,
    scale: u8,
}

impl SignedQuantity {
    pub const ZERO: Self = Self {
        mantissa: 0,
        scale: 0,
    };

    pub fn new(mantissa: i64, scale: u8) -> Result<Self, DomainTypeError> {
        let (mantissa, scale) = normalize_decimal_parts(mantissa, scale, "SignedQuantity")?;
        Ok(Self { mantissa, scale })
    }

    pub const fn mantissa(self) -> i64 {
        self.mantissa
    }

    pub const fn scale(self) -> u8 {
        self.scale
    }
}

impl Quantity {
    pub const ZERO: Self = Self {
        mantissa: 0,
        scale: 0,
    };

    pub fn new(mantissa: i64, scale: u8) -> Result<Self, DomainTypeError> {
        if mantissa < 0 {
            return Err(DomainTypeError::Invalid {
                type_name: "Quantity",
                reason: "negative values are not allowed",
            });
        }
        let (mantissa, scale) = normalize_decimal_parts(mantissa, scale, "Quantity")?;
        Ok(Self { mantissa, scale })
    }

    pub fn positive(mantissa: i64, scale: u8) -> Result<Self, DomainTypeError> {
        let value = Self::new(mantissa, scale)?;
        if value.is_zero() {
            return Err(DomainTypeError::NonPositive {
                type_name: "Quantity",
            });
        }
        Ok(value)
    }

    pub const fn is_zero(self) -> bool {
        self.mantissa == 0
    }

    pub const fn is_positive(self) -> bool {
        self.mantissa > 0
    }

    pub const fn mantissa(self) -> i64 {
        self.mantissa
    }
    pub const fn scale(self) -> u8 {
        self.scale
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash)]
pub struct Price {
    mantissa: i64,
    scale: u8,
}

/// A signed difference between two prices. Unlike `Price`, zero and negative
/// values are valid because a delta describes a relationship, not a quote.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Hash)]
pub struct PriceDelta {
    mantissa: i64,
    scale: u8,
}

impl PriceDelta {
    pub const ZERO: Self = Self {
        mantissa: 0,
        scale: 0,
    };

    pub fn new(mantissa: i64, scale: u8) -> Result<Self, DomainTypeError> {
        let (mantissa, scale) = normalize_decimal_parts(mantissa, scale, "PriceDelta")?;
        Ok(Self { mantissa, scale })
    }

    pub const fn mantissa(self) -> i64 {
        self.mantissa
    }

    pub const fn scale(self) -> u8 {
        self.scale
    }
}

impl Price {
    pub const fn is_positive(self) -> bool {
        self.mantissa > 0
    }

    pub fn new(mantissa: i64, scale: u8) -> Result<Self, DomainTypeError> {
        if mantissa <= 0 {
            return Err(DomainTypeError::NonPositive { type_name: "Price" });
        }
        let (mantissa, scale) = normalize_decimal_parts(mantissa, scale, "Price")?;
        Ok(Self { mantissa, scale })
    }

    pub const fn mantissa(self) -> i64 {
        self.mantissa
    }
    pub const fn scale(self) -> u8 {
        self.scale
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Hash)]
pub struct Money {
    mantissa: i64,
    scale: u8,
}

impl Money {
    pub const ZERO: Self = Self {
        mantissa: 0,
        scale: 0,
    };

    pub fn new(mantissa: i64, scale: u8) -> Result<Self, DomainTypeError> {
        let (mantissa, scale) = normalize_decimal_parts(mantissa, scale, "Money")?;
        Ok(Self { mantissa, scale })
    }

    pub const fn mantissa(self) -> i64 {
        self.mantissa
    }

    pub const fn scale(self) -> u8 {
        self.scale
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Hash)]
pub struct Rate {
    mantissa: i64,
    scale: u8,
}

/// A positive rational configuration value used for proportional policies.
#[derive(
    Clone, Copy, Debug, Default, Eq, Ord, PartialEq, PartialOrd, Hash, Serialize, Deserialize,
)]
pub struct Ratio {
    numerator: u64,
    denominator: u64,
}

impl Ratio {
    pub fn new(numerator: u64, denominator: u64) -> Result<Self, DomainTypeError> {
        if numerator == 0 || denominator == 0 {
            return Err(DomainTypeError::NonPositive { type_name: "Ratio" });
        }
        Ok(Self {
            numerator,
            denominator,
        })
    }

    pub const fn numerator(self) -> u64 {
        self.numerator
    }
    pub const fn denominator(self) -> u64 {
        self.denominator
    }

    pub fn apply_to_nonnegative(self, value: i64) -> Result<i64, DomainTypeError> {
        if value < 0 {
            return Err(DomainTypeError::Invalid {
                type_name: "Ratio",
                reason: "cannot be applied to a negative value",
            });
        }
        let result = u128::try_from(value)
            .map_err(|_| DomainTypeError::Invalid {
                type_name: "Ratio",
                reason: "value is too large",
            })?
            .checked_mul(u128::from(self.numerator))
            .ok_or(DomainTypeError::Invalid {
                type_name: "Ratio",
                reason: "arithmetic overflow",
            })?
            / u128::from(self.denominator);
        i64::try_from(result).map_err(|_| DomainTypeError::Invalid {
            type_name: "Ratio",
            reason: "arithmetic overflow",
        })
    }
}

impl Rate {
    pub const ZERO: Self = Self {
        mantissa: 0,
        scale: 0,
    };

    pub fn new(mantissa: i64, scale: u8) -> Result<Self, DomainTypeError> {
        let (mantissa, scale) = normalize_decimal_parts(mantissa, scale, "Rate")?;
        Ok(Self { mantissa, scale })
    }

    pub const fn mantissa(self) -> i64 {
        self.mantissa
    }

    pub const fn scale(self) -> u8 {
        self.scale
    }
}

macro_rules! fixed_decimal_impl {
    ($name:ident) => {
        impl std::str::FromStr for $name {
            type Err = DomainTypeError;

            fn from_str(value: &str) -> Result<Self, Self::Err> {
                let (whole, fraction) = value.split_once('.').unwrap_or((value, ""));
                if whole.is_empty()
                    || fraction.len() > 18
                    || !whole.bytes().all(|byte| byte.is_ascii_digit())
                    || !fraction.bytes().all(|byte| byte.is_ascii_digit())
                {
                    return Err(DomainTypeError::Invalid {
                        type_name: stringify!($name),
                        reason: "expected a non-negative decimal",
                    });
                }
                let scale = u8::try_from(fraction.len()).map_err(|_| DomainTypeError::Invalid {
                    type_name: stringify!($name),
                    reason: "decimal scale is too large",
                })?;
                let digits = format!("{whole}{fraction}");
                let mantissa = digits
                    .parse::<i64>()
                    .map_err(|_| DomainTypeError::Invalid {
                        type_name: stringify!($name),
                        reason: "decimal value is too large",
                    })?;
                Self::new(mantissa, scale)
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                if self.scale == 0 {
                    return write!(f, "{}", self.mantissa);
                }
                let factor = 10_i64.pow(u32::from(self.scale));
                let whole = self.mantissa / factor;
                let fraction = format!(
                    "{:0width$}",
                    self.mantissa % factor,
                    width = usize::from(self.scale)
                );
                write!(f, "{whole}.{fraction}")
            }
        }
    };
}

fixed_decimal_impl!(Quantity);
fixed_decimal_impl!(Price);

macro_rules! signed_fixed_decimal_impl {
    ($name:ident) => {
        impl std::str::FromStr for $name {
            type Err = DomainTypeError;

            fn from_str(value: &str) -> Result<Self, Self::Err> {
                let (negative, unsigned) = value
                    .strip_prefix('-')
                    .map_or((false, value), |value| (true, value));
                let (whole, fraction) = unsigned.split_once('.').unwrap_or((unsigned, ""));
                if whole.is_empty()
                    || fraction.len() > 18
                    || !whole.bytes().all(|byte| byte.is_ascii_digit())
                    || !fraction.bytes().all(|byte| byte.is_ascii_digit())
                {
                    return Err(DomainTypeError::Invalid {
                        type_name: stringify!($name),
                        reason: "expected a signed decimal",
                    });
                }
                let scale = u8::try_from(fraction.len()).map_err(|_| DomainTypeError::Invalid {
                    type_name: stringify!($name),
                    reason: "decimal scale is too large",
                })?;
                let digits = format!("{whole}{fraction}");
                let magnitude = digits
                    .parse::<i128>()
                    .map_err(|_| DomainTypeError::Invalid {
                        type_name: stringify!($name),
                        reason: "decimal value is too large",
                    })?;
                let mantissa = if negative { -magnitude } else { magnitude };
                let mantissa = i64::try_from(mantissa).map_err(|_| DomainTypeError::Invalid {
                    type_name: stringify!($name),
                    reason: "decimal value is too large",
                })?;
                Self::new(mantissa, scale)
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                let mantissa = i128::from(self.mantissa());
                let negative = mantissa < 0;
                let magnitude = mantissa.abs();
                if self.scale() == 0 {
                    return write!(f, "{}{magnitude}", if negative { "-" } else { "" });
                }
                let factor = 10_i128.pow(u32::from(self.scale()));
                let whole = magnitude / factor;
                let fraction = format!(
                    "{:0width$}",
                    magnitude % factor,
                    width = usize::from(self.scale())
                );
                write!(f, "{}{whole}.{fraction}", if negative { "-" } else { "" })
            }
        }
    };
}

signed_fixed_decimal_impl!(Money);
signed_fixed_decimal_impl!(Rate);
signed_fixed_decimal_impl!(PriceDelta);

signed_fixed_decimal_impl!(SignedQuantity);

macro_rules! decimal_value_semantics {
    ($name:ident) => {
        impl Serialize for $name {
            fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
            where
                S: serde::Serializer,
            {
                serializer.collect_str(self)
            }
        }

        impl<'de> Deserialize<'de> for $name {
            fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
            where
                D: serde::Deserializer<'de>,
            {
                let value = String::deserialize(deserializer)?;
                value.parse().map_err(serde::de::Error::custom)
            }
        }

        impl Ord for $name {
            fn cmp(&self, other: &Self) -> std::cmp::Ordering {
                decimal_value(self.mantissa, self.scale)
                    .expect("domain decimal invariant")
                    .cmp(
                        &decimal_value(other.mantissa, other.scale)
                            .expect("domain decimal invariant"),
                    )
            }
        }

        impl PartialOrd for $name {
            fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
                Some(self.cmp(other))
            }
        }
    };
}

decimal_value_semantics!(Quantity);
decimal_value_semantics!(SignedQuantity);
decimal_value_semantics!(Price);
decimal_value_semantics!(PriceDelta);
decimal_value_semantics!(Money);
decimal_value_semantics!(Rate);

fn decimal_value(mantissa: i64, scale: u8) -> Result<RustDecimal, DomainTypeError> {
    RustDecimal::try_new(mantissa, u32::from(scale)).map_err(|_| DomainTypeError::Invalid {
        type_name: "fixed decimal",
        reason: "decimal value is too large",
    })
}

fn decimal_parts(value: RustDecimal) -> Result<(i64, u8), DomainTypeError> {
    let mantissa = i64::try_from(value.mantissa()).map_err(|_| DomainTypeError::Invalid {
        type_name: "fixed decimal",
        reason: "decimal value is too large",
    })?;
    let scale = u8::try_from(value.scale()).map_err(|_| DomainTypeError::Invalid {
        type_name: "fixed decimal",
        reason: "decimal scale is too large",
    })?;
    Ok((mantissa, scale))
}

impl Quantity {
    pub fn checked_add(self, other: Self) -> Result<Self, DomainTypeError> {
        let (mantissa, scale) = decimal_parts(
            decimal_value(self.mantissa, self.scale)?
                .checked_add(decimal_value(other.mantissa, other.scale)?)
                .ok_or(DomainTypeError::Invalid {
                    type_name: "Quantity",
                    reason: "arithmetic overflow",
                })?,
        )?;
        Self::new(mantissa, scale)
    }

    pub fn checked_sub(self, other: Self) -> Result<Self, DomainTypeError> {
        let (mantissa, scale) = decimal_parts(
            decimal_value(self.mantissa, self.scale)?
                .checked_sub(decimal_value(other.mantissa, other.scale)?)
                .ok_or(DomainTypeError::Invalid {
                    type_name: "Quantity",
                    reason: "arithmetic overflow",
                })?,
        )?;
        Self::new(mantissa, scale)
    }

    pub fn cmp_value(self, other: Self) -> Result<std::cmp::Ordering, DomainTypeError> {
        Ok(decimal_value(self.mantissa, self.scale)?
            .cmp(&decimal_value(other.mantissa, other.scale)?))
    }

    pub fn is_multiple_of(self, increment: Self) -> Result<bool, DomainTypeError> {
        if increment.is_zero() {
            return Err(DomainTypeError::Invalid {
                type_name: "Quantity",
                reason: "increment cannot be zero",
            });
        }
        Ok((decimal_value(self.mantissa, self.scale)?
            % decimal_value(increment.mantissa, increment.scale)?)
        .is_zero())
    }

    pub fn checked_mul(self, price: Price) -> Result<Money, DomainTypeError> {
        let value = decimal_value(self.mantissa, self.scale)?
            .checked_mul(decimal_value(price.mantissa, price.scale)?)
            .ok_or(DomainTypeError::Invalid {
                type_name: "Money",
                reason: "arithmetic overflow",
            })?;
        let (mantissa, scale) = decimal_parts(value)?;
        Money::new(mantissa, scale)
    }
}

impl SignedQuantity {
    pub fn is_zero(self) -> bool {
        self.mantissa == 0
    }
    pub fn is_positive(self) -> bool {
        self.mantissa > 0
    }
    pub fn is_negative(self) -> bool {
        self.mantissa < 0
    }

    pub fn checked_neg(self) -> Result<Self, DomainTypeError> {
        self.mantissa
            .checked_neg()
            .ok_or(DomainTypeError::Invalid {
                type_name: "SignedQuantity",
                reason: "arithmetic overflow",
            })
            .and_then(|mantissa| Self::new(mantissa, self.scale))
    }

    pub fn checked_add(self, other: Self) -> Result<Self, DomainTypeError> {
        let value = decimal_value(self.mantissa, self.scale)?
            .checked_add(decimal_value(other.mantissa, other.scale)?)
            .ok_or(DomainTypeError::Invalid {
                type_name: "SignedQuantity",
                reason: "arithmetic overflow",
            })?;
        let (mantissa, scale) = decimal_parts(value)?;
        Self::new(mantissa, scale)
    }

    pub fn checked_sub(self, other: Self) -> Result<Self, DomainTypeError> {
        self.checked_add(other.checked_neg()?)
    }

    pub fn cmp_value(self, other: Self) -> Result<std::cmp::Ordering, DomainTypeError> {
        Ok(decimal_value(self.mantissa, self.scale)?
            .cmp(&decimal_value(other.mantissa, other.scale)?))
    }

    pub fn checked_mul(self, price: Price) -> Result<Money, DomainTypeError> {
        let value = decimal_value(self.mantissa, self.scale)?
            .checked_mul(decimal_value(price.mantissa, price.scale)?)
            .ok_or(DomainTypeError::Invalid {
                type_name: "Money",
                reason: "arithmetic overflow",
            })?;
        let (mantissa, scale) = decimal_parts(value)?;
        Money::new(mantissa, scale)
    }
}

impl Price {
    pub fn is_multiple_of(self, increment: Self) -> Result<bool, DomainTypeError> {
        Ok((decimal_value(self.mantissa, self.scale)?
            % decimal_value(increment.mantissa, increment.scale)?)
        .is_zero())
    }

    pub fn checked_sub(self, other: Self) -> Result<PriceDelta, DomainTypeError> {
        let value = decimal_value(self.mantissa, self.scale)?
            .checked_sub(decimal_value(other.mantissa, other.scale)?)
            .ok_or(DomainTypeError::Invalid {
                type_name: "PriceDelta",
                reason: "arithmetic overflow",
            })?;
        let (mantissa, scale) = decimal_parts(value)?;
        PriceDelta::new(mantissa, scale)
    }

    pub fn checked_mul(self, quantity: Quantity) -> Result<Money, DomainTypeError> {
        quantity.checked_mul(self)
    }

    pub fn checked_div(self, quantity: Quantity) -> Result<Self, DomainTypeError> {
        if quantity.is_zero() {
            return Err(DomainTypeError::Invalid {
                type_name: "Price",
                reason: "division by zero",
            });
        }
        let value = decimal_value(self.mantissa, self.scale)?
            .checked_div(decimal_value(quantity.mantissa(), quantity.scale())?)
            .ok_or(DomainTypeError::Invalid {
                type_name: "Price",
                reason: "arithmetic overflow",
            })?;
        let (mantissa, scale) = decimal_parts(value)?;
        Self::new(mantissa, scale)
    }
}

impl PriceDelta {
    pub fn checked_mul(self, quantity: SignedQuantity) -> Result<Money, DomainTypeError> {
        let value = decimal_value(self.mantissa, self.scale)?
            .checked_mul(decimal_value(quantity.mantissa, quantity.scale)?)
            .ok_or(DomainTypeError::Invalid {
                type_name: "Money",
                reason: "arithmetic overflow",
            })?;
        let (mantissa, scale) = decimal_parts(value)?;
        Money::new(mantissa, scale)
    }
}

impl Money {
    pub fn is_zero(self) -> bool {
        self.mantissa == 0
    }
    pub fn is_negative(self) -> bool {
        self.mantissa < 0
    }
    pub fn checked_neg(self) -> Result<Self, DomainTypeError> {
        self.mantissa
            .checked_neg()
            .ok_or(DomainTypeError::Invalid {
                type_name: "Money",
                reason: "arithmetic overflow",
            })
            .and_then(|mantissa| Self::new(mantissa, self.scale))
    }
    pub fn checked_add(self, other: Self) -> Result<Self, DomainTypeError> {
        let value = decimal_value(self.mantissa, self.scale)?
            .checked_add(decimal_value(other.mantissa, other.scale)?)
            .ok_or(DomainTypeError::Invalid {
                type_name: "Money",
                reason: "arithmetic overflow",
            })?;
        let (mantissa, scale) = decimal_parts(value)?;
        Self::new(mantissa, scale)
    }

    pub fn checked_sub(self, other: Self) -> Result<Self, DomainTypeError> {
        self.checked_add(other.checked_neg()?)
    }

    pub fn checked_div(self, quantity: SignedQuantity) -> Result<Price, DomainTypeError> {
        if quantity.is_zero() {
            return Err(DomainTypeError::Invalid {
                type_name: "Price",
                reason: "division by zero",
            });
        }
        let value = decimal_value(self.mantissa, self.scale)?
            .checked_div(decimal_value(quantity.mantissa, quantity.scale)?)
            .ok_or(DomainTypeError::Invalid {
                type_name: "Price",
                reason: "arithmetic overflow",
            })?;
        let (mantissa, scale) = decimal_parts(value)?;
        Price::new(mantissa, scale)
    }
}
