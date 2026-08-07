use std::cmp::Ordering;

use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Amount {
    pub mantissa: i64,
    pub scale: u8,
}

impl Amount {
    pub const ZERO: Self = Self {
        mantissa: 0,
        scale: 0,
    };

    pub fn new(mantissa: i64, scale: u8) -> Result<Self, String> {
        if mantissa < 0 {
            return Err("risk amounts cannot be negative".into());
        }
        Ok(Self { mantissa, scale })
    }

    pub fn checked_add(self, other: Self) -> Result<Self, String> {
        let scale = self.scale.max(other.scale);
        let left = scale_value(self, scale)?;
        let right = scale_value(other, scale)?;
        let value = left
            .checked_add(right)
            .ok_or_else(|| "risk amount overflow".to_string())?;
        i64::try_from(value)
            .map(|mantissa| Self { mantissa, scale })
            .map_err(|_| "risk amount overflow".into())
    }

    pub fn checked_sub(self, other: Self) -> Result<Self, String> {
        let scale = self.scale.max(other.scale);
        let left = scale_value(self, scale)?;
        let right = scale_value(other, scale)?;
        let value = left
            .checked_sub(right)
            .ok_or_else(|| "risk amount underflow".to_string())?;
        if value < 0 {
            return Err("risk amount cannot become negative".into());
        }
        i64::try_from(value)
            .map(|mantissa| Self { mantissa, scale })
            .map_err(|_| "risk amount overflow".into())
    }

    pub fn cmp_value(self, other: Self) -> Ordering {
        let scale = self.scale.max(other.scale);
        scale_value(self, scale)
            .unwrap()
            .cmp(&scale_value(other, scale).unwrap())
    }
}

fn scale_value(value: Amount, scale: u8) -> Result<i128, String> {
    let factor = 10_i128
        .checked_pow((scale - value.scale) as u32)
        .ok_or_else(|| "risk scale overflow".to_string())?;
    (value.mantissa as i128)
        .checked_mul(factor)
        .ok_or_else(|| "risk amount overflow".into())
}

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct BudgetRef {
    pub scope: String,
    pub subject: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Metric {
    Notional,
    Margin,
    GrossExposure,
    NetExposure,
    Turnover,
    OrderRate,
}

impl Metric {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Notional => "notional",
            Self::Margin => "margin",
            Self::GrossExposure => "gross_exposure",
            Self::NetExposure => "net_exposure",
            Self::Turnover => "turnover",
            Self::OrderRate => "order_rate",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Budget {
    pub budget_id: String,
    pub owner_id: String,
    pub reference: BudgetRef,
    pub metric: Metric,
    pub limit: Amount,
    pub used: Amount,
    pub reserved: Amount,
    pub valid_from_unix_nanos: Option<u64>,
    pub valid_until_unix_nanos: Option<u64>,
}

impl Budget {
    pub fn validate(&self) -> Result<(), String> {
        if self.budget_id.trim().is_empty() || self.owner_id.trim().is_empty() {
            return Err("budget_id and owner_id are required".into());
        }
        if self.reference.scope.trim().is_empty() || self.reference.subject.trim().is_empty() {
            return Err("budget scope and subject are required".into());
        }
        if self
            .valid_from_unix_nanos
            .zip(self.valid_until_unix_nanos)
            .is_some_and(|(a, b)| b < a)
        {
            return Err("budget validity interval is inverted".into());
        }
        if self.used.checked_add(self.reserved)?.cmp_value(self.limit) == Ordering::Greater {
            return Err("budget usage exceeds limit".into());
        }
        Ok(())
    }
    pub fn active_at(&self, at: u64) -> bool {
        self.valid_from_unix_nanos.is_none_or(|v| at >= v)
            && self.valid_until_unix_nanos.is_none_or(|v| at < v)
    }
    pub fn available(&self) -> Amount {
        self.limit
            .checked_sub(self.used.checked_add(self.reserved).unwrap())
            .unwrap()
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Usage {
    pub metric: Metric,
    pub amount: Amount,
    pub budgets: Vec<BudgetRef>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReservationStatus {
    Reserved,
    Consumed,
    Released,
    Expired,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReservationAllocation {
    pub budget_id: String,
    pub metric: Metric,
    pub amount: Amount,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Reservation {
    pub reservation_id: String,
    pub request_id: String,
    pub allocations: Vec<ReservationAllocation>,
    pub status: ReservationStatus,
    pub created_at_unix_nanos: u64,
    pub updated_at_unix_nanos: u64,
}
