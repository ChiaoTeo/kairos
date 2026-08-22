//! Participant-neutral observed trading fee facts.

use kairos_primitives::reference::{Currency, Symbol};

use super::DecimalValue;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalFeeScheduleRequest {
    pub symbol: Symbol,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalFeeComponent {
    pub maker: Option<DecimalValue>,
    pub taker: Option<DecimalValue>,
    pub buyer: Option<DecimalValue>,
    pub seller: Option<DecimalValue>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalFeeDiscount {
    pub enabled_for_account: Option<bool>,
    pub enabled_for_symbol: Option<bool>,
    pub asset: Option<Currency>,
    pub rate: Option<DecimalValue>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalFeeSchedule {
    pub symbol: Symbol,
    pub maker: DecimalValue,
    pub taker: DecimalValue,
    pub buyer: Option<DecimalValue>,
    pub seller: Option<DecimalValue>,
    pub standard: Option<ExternalFeeComponent>,
    pub special: Option<ExternalFeeComponent>,
    pub tax: Option<ExternalFeeComponent>,
    pub discount: Option<ExternalFeeDiscount>,
    pub rpi: Option<DecimalValue>,
}
