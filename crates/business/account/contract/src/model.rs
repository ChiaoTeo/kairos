//! Public Account snapshot models.

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Decimal {
    pub mantissa: i64,
    pub scale: u8,
}

#[cfg(test)]
mod decimal_tests {
    use super::Decimal;

    #[test]
    fn json_decimal_is_a_string_and_rejects_the_retired_object_shape() {
        let value = serde_json::from_str::<Decimal>("\"42110.50\"").unwrap();
        assert_eq!((value.mantissa, value.scale), (4_211_050, 2));
        assert_eq!(serde_json::to_string(&value).unwrap(), "\"42110.50\"");
        assert!(serde_json::from_str::<Decimal>(r#"{"mantissa":4211050,"scale":2}"#).is_err());
    }
}

pub(crate) fn parse_decimal(value: &str) -> Result<(i64, u8), String> {
    let (negative, unsigned) = value
        .strip_prefix('-')
        .map_or((false, value), |value| (true, value));
    let (whole, fraction) = unsigned.split_once('.').unwrap_or((unsigned, ""));
    if whole.is_empty()
        || fraction.len() > 18
        || !whole.bytes().all(|byte| byte.is_ascii_digit())
        || !fraction.bytes().all(|byte| byte.is_ascii_digit())
    {
        return Err("expected a decimal string with at most 18 fractional digits".into());
    }
    let magnitude = format!("{whole}{fraction}")
        .parse::<i128>()
        .map_err(|_| "decimal value is too large")?;
    let mantissa = i64::try_from(if negative { -magnitude } else { magnitude })
        .map_err(|_| "decimal value is too large")?;
    Ok((mantissa, fraction.len() as u8))
}

pub(crate) fn format_decimal(mantissa: i64, scale: u8) -> Result<String, String> {
    if scale > 18 {
        return Err("decimal scale exceeds 18 digits".into());
    }
    let negative = mantissa < 0;
    let magnitude = i128::from(mantissa).abs();
    if scale == 0 {
        return Ok(format!("{}{magnitude}", if negative { "-" } else { "" }));
    }
    let factor = 10_i128.pow(u32::from(scale));
    Ok(format!(
        "{}{whole}.{fraction:0width$}",
        if negative { "-" } else { "" },
        whole = magnitude / factor,
        fraction = magnitude % factor,
        width = usize::from(scale)
    ))
}

impl serde::Serialize for Decimal {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        serializer.serialize_str(
            &format_decimal(self.mantissa, self.scale).map_err(serde::ser::Error::custom)?,
        )
    }
}

impl<'de> serde::Deserialize<'de> for Decimal {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let value = <String as serde::Deserialize>::deserialize(deserializer)?;
        let (mantissa, scale) = parse_decimal(&value).map_err(serde::de::Error::custom)?;
        Ok(Self { mantissa, scale })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AccountModel {
    NoMargin,
    Margin,
    Contract,
    ContractUnified,
    Unified,
    PortfolioMargin,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AccountStatus {
    #[default]
    Unknown,
    Ready,
    Reconciling,
    TypeMismatch,
    Suspended,
    Unavailable,
}

impl AccountStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Unknown => "unknown",
            Self::Ready => "ready",
            Self::Reconciling => "reconciling",
            Self::TypeMismatch => "type_mismatch",
            Self::Suspended => "suspended",
            Self::Unavailable => "unavailable",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarginMode {
    Cross,
    Isolated,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PositionMode {
    OneWay,
    Hedge,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Balance {
    pub asset_id: String,
    pub asset_code: String,
    pub total: Decimal,
    pub available: Option<Decimal>,
    pub locked: Option<Decimal>,
    pub borrowed: Option<Decimal>,
    pub interest: Option<Decimal>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Position {
    pub instrument_id: String,
    pub market_id: Option<String>,
    pub quantity: Decimal,
    pub average_price: Option<Decimal>,
    pub mark_price: Option<Decimal>,
    pub unrealized_pnl: Option<Decimal>,
    pub realized_pnl: Option<Decimal>,
    pub updated_at_unix_nanos: u64,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct OpenOrder {
    pub order_id: String,
    pub remote_order_id: Option<String>,
    pub instrument_id: String,
    pub side: String,
    pub quantity: Decimal,
    pub filled_quantity: Decimal,
    pub status: String,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct AccountProjection {
    pub account_id: String,
    pub segment_key: String,
    pub environment: String,
    pub broker: String,
    pub configured_account_model: Option<String>,
    pub observed_account_model: Option<AccountModel>,
    pub status: AccountStatus,
    pub stale: bool,
    pub observed_at_unix_nanos: u64,
    pub generation: u64,
    pub event_sequence: u64,
    pub equity: Option<Decimal>,
    pub initial_equity: Option<Decimal>,
    pub net_profit: Option<Decimal>,
    pub margin_mode: Option<MarginMode>,
    pub position_mode: Option<PositionMode>,
    pub balances: Vec<Balance>,
    pub collateral: Vec<Balance>,
    pub positions: Vec<Position>,
    pub open_orders: Vec<OpenOrder>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct AccountsSnapshot {
    pub actor_id: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub accounts: Vec<AccountProjection>,
}
