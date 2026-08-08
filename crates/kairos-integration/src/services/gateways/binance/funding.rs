//! Binance Funding Wallet account-read connection.

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::domain::account::{
    ExternalAccountSegment as AccountSegment, ExternalAccountSnapshot as AccountSnapshot,
    ExternalAccountStatus as AccountStatus, ExternalBalance as Balance,
    ExternalDecimal as DecimalValue,
};
use serde_json::Value;

use crate::application::{
    AccountCredentialInspectionConnection, AccountReadConnection, Connection, ConnectionSpec,
    ExternalAccountCredentialProfile, IntegrationError,
};
use crate::domain::{AccessScope, IntegrationCapability, TransportKind};
use crate::services::connections::ManagedConnection;

use super::spot::account::BinanceSpotAccountClient;

pub struct BinanceFundingAccountConnection {
    connection: ManagedConnection,
    client: BinanceSpotAccountClient,
}

impl BinanceFundingAccountConnection {
    pub fn new(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = BinanceSpotAccountClient::new(api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        let spec = ConnectionSpec {
            connection_id: "account.binance.funding.rest".into(),
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: None,
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::AccountRead,
            credential_id: Some("binance".into()),
            asset_type: None,
        };
        Ok(Self {
            connection: ManagedConnection::new(spec, Vec::new())?,
            client,
        })
    }
}

impl Connection for BinanceFundingAccountConnection {
    fn identity(&self) -> &crate::domain::ConnectionIdentity {
        self.connection.identity()
    }
    fn state(&self) -> &crate::domain::ConnectionState {
        self.connection.state()
    }
    fn start(&mut self) -> Result<(), String> {
        self.connection.start()
    }
    fn stop(&mut self) -> Result<(), String> {
        self.connection.stop()
    }
    fn reconnect(&mut self) -> Result<(), String> {
        self.connection.reconnect()
    }
    fn health(&self) -> crate::domain::ConnectionHealth {
        self.connection.health()
    }
}

impl AccountReadConnection for BinanceFundingAccountConnection {
    fn fetch_account(
        &mut self,
        segment: &AccountSegment,
    ) -> Result<AccountSnapshot, IntegrationError> {
        self.start().map_err(IntegrationError::Transport)?;
        let payload = self
            .client
            .signed_post("/sapi/v1/asset/get-funding-asset", BTreeMap::new())
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        normalize_funding(segment, &payload).map_err(IntegrationError::InvalidPayload)
    }
}

impl AccountCredentialInspectionConnection for BinanceFundingAccountConnection {
    fn inspect_credential(&mut self) -> Result<ExternalAccountCredentialProfile, String> {
        self.start()?;
        let payload = self.client.account().map_err(|error| error.to_string())?;
        let mut permissions = vec!["read".to_string()];
        if payload
            .get("canTrade")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            permissions.push("trade".into());
        }
        Ok(ExternalAccountCredentialProfile {
            remote_identity: None,
            account_type: payload
                .get("accountType")
                .and_then(Value::as_str)
                .map(str::to_owned),
            permissions,
            segments: vec!["funding".into()],
            attributes: BTreeMap::new(),
        })
    }
}

fn normalize_funding(segment: &AccountSegment, payload: &Value) -> Result<AccountSnapshot, String> {
    let rows = payload
        .as_array()
        .ok_or_else(|| "Binance funding response must be an array".to_string())?;
    let balances = rows
        .iter()
        .filter_map(|row| {
            let code = row.get("asset").and_then(Value::as_str)?;
            let free = decimal(row.get("free").and_then(Value::as_str).unwrap_or("0")).ok()?;
            let locked = decimal(
                row.get("locked")
                    .or_else(|| row.get("freeze"))
                    .and_then(Value::as_str)
                    .unwrap_or("0"),
            )
            .ok()?;
            let scale = free.scale.max(locked.scale);
            let total = rescale(free, scale)
                .ok()?
                .checked_add(rescale(locked, scale).ok()?)?;
            Some(Ok(Balance {
                asset_id: format!("asset:crypto:{code}"),
                asset_code: code.into(),
                total: DecimalValue::new(total, scale),
                available: Some(free),
                locked: Some(locked),
                ..Default::default()
            }))
        })
        .collect::<Result<Vec<_>, String>>()?;
    Ok(AccountSnapshot {
        segment_key: segment.segment_key.clone(),
        balances,
        collateral: Vec::new(),
        positions: Vec::new(),
        open_orders: Vec::new(),
        status: AccountStatus::Ready,
        observed_at_unix_nanos: now_nanos(),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: None,
        margin_mode: None,
        position_mode: None,
        partial: false,
    })
}

fn decimal(value: &str) -> Result<DecimalValue, String> {
    let negative = value.starts_with('-');
    let unsigned = value.trim_start_matches('-');
    let mut parts = unsigned.split('.');
    let whole = parts.next().unwrap_or("0");
    let fraction = parts.next().unwrap_or("");
    if parts.next().is_some()
        || !whole.chars().all(|c| c.is_ascii_digit())
        || !fraction.chars().all(|c| c.is_ascii_digit())
    {
        return Err(format!("invalid decimal: {value}"));
    }
    let mut mantissa = format!("{whole}{fraction}")
        .parse::<i64>()
        .map_err(|_| format!("decimal overflow: {value}"))?;
    if negative {
        mantissa = -mantissa;
    }
    Ok(DecimalValue::new(mantissa, fraction.len() as u8))
}

fn rescale(value: DecimalValue, scale: u8) -> Result<i64, String> {
    value
        .mantissa
        .checked_mul(10_i64.pow((scale - value.scale) as u32))
        .ok_or_else(|| "decimal rescale overflow".into())
}

fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::normalize_funding;
    use crate::domain::account::{
        ExternalAccountIdentity, ExternalAccountSegment as AccountSegment,
    };

    #[test]
    fn normalizes_funding_wallet_balances() {
        let segment = AccountSegment {
            identity: ExternalAccountIdentity::new("binance", "main").unwrap(),
            segment_key: "funding".into(),
            environment: "live".into(),
            account_model: None,
        };
        let snapshot = normalize_funding(
            &segment,
            &serde_json::json!([{"asset":"USDT","free":"10.25","freeze":"0.75"}]),
        )
        .unwrap();
        assert_eq!(snapshot.balances[0].total.mantissa, 1100);
    }
}
