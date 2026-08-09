//! Public Risk snapshot and event models.

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Amount {
    pub mantissa: i64,
    pub scale: u8,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
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

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Budget {
    pub budget_id: String,
    pub owner_id: String,
    pub metric: Metric,
    pub limit: Amount,
    pub used: Amount,
    pub reserved: Amount,
}

impl Budget {
    pub fn available(&self) -> Amount {
        Amount {
            mantissa: self.limit.mantissa - self.used.mantissa - self.reserved.mantissa,
            scale: self.limit.scale,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReservationStatus {
    Reserved,
    Consumed,
    Released,
    Expired,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ReservationAllocation {
    pub budget_id: String,
    pub metric: Metric,
    pub amount: Amount,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Reservation {
    pub reservation_id: String,
    pub request_id: String,
    pub allocations: Vec<ReservationAllocation>,
    pub status: ReservationStatus,
    pub created_at_unix_nanos: u64,
    pub updated_at_unix_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct RiskSnapshot {
    pub actor_id: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub budgets: Vec<Budget>,
    pub reservations: Vec<Reservation>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum RiskEvent {
    ReservationChanged {
        reservation: Reservation,
        event_sequence: u64,
    },
}
