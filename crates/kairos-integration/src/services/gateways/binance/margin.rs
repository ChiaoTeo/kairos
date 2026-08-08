//! Native Binance cross/isolated margin account REST connections.

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

use crate::application::{
    AccountCredentialInspectionConnection, AccountReadConnection, Connection, ConnectionSpec,
    ExternalAccountCredentialProfile, IntegrationError,
};
use crate::domain::account::{
    ExternalAccountSegment as AccountSegment, ExternalAccountSnapshot as AccountSnapshot,
    ExternalAccountStatus as AccountStatus, ExternalBalance as Balance,
    ExternalDecimal as DecimalValue, ExternalOpenOrder as OpenOrder,
};
use crate::domain::{AccessScope, IntegrationCapability, ProductFamily, TransportKind};
use crate::services::connections::ManagedConnection;

use super::spot::account::BinanceSpotAccountClient;

pub struct BinanceMarginAccountConnection {
    connection: ManagedConnection,
    client: BinanceSpotAccountClient,
    product: ProductFamily,
}

impl BinanceMarginAccountConnection {
    pub fn new(
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        if !matches!(
            product,
            ProductFamily::CrossMargin | ProductFamily::IsolatedMargin
        ) {
            return Err("Binance margin account requires cross or isolated margin".into());
        }
        let client = BinanceSpotAccountClient::new(api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        let connection = ManagedConnection::new(
            ConnectionSpec {
                connection_id: format!("account.binance.{}.rest", product_name(product)),
                route: crate::domain::IntegrationRoute::exchange("binance"),
                product: Some(product),
                access: AccessScope::Private,
                transport: TransportKind::Rest,
                capability: IntegrationCapability::AccountRead,
                credential_id: Some("binance".into()),
                asset_type: None,
            },
            Vec::new(),
        )?;
        Ok(Self {
            connection,
            client,
            product,
        })
    }
}

impl Connection for BinanceMarginAccountConnection {
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

impl AccountReadConnection for BinanceMarginAccountConnection {
    fn fetch_account(
        &mut self,
        segment: &AccountSegment,
    ) -> Result<AccountSnapshot, IntegrationError> {
        self.start().map_err(IntegrationError::Transport)?;
        let (payload, isolated) = match self.product {
            ProductFamily::CrossMargin => (
                self.client
                    .signed_get("/sapi/v1/margin/account", BTreeMap::new())
                    .map_err(|error| IntegrationError::Transport(error.to_string()))?,
                false,
            ),
            ProductFamily::IsolatedMargin => (
                self.client
                    .signed_get("/sapi/v1/margin/isolated/account", BTreeMap::new())
                    .map_err(|error| IntegrationError::Transport(error.to_string()))?,
                true,
            ),
            _ => unreachable!(),
        };
        let orders = self
            .client
            .signed_get("/sapi/v1/margin/openOrders", BTreeMap::new())
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        normalize(segment, &payload, &orders, isolated).map_err(IntegrationError::InvalidPayload)
    }
}

impl AccountCredentialInspectionConnection for BinanceMarginAccountConnection {
    fn inspect_credential(&mut self) -> Result<ExternalAccountCredentialProfile, String> {
        self.start()?;
        let payload = self.client.account().map_err(|error| error.to_string())?;
        let mut permissions: Vec<String> = payload
            .get("permissions")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect();
        if !permissions
            .iter()
            .any(|value| value.eq_ignore_ascii_case("read"))
        {
            permissions.push("read".into());
        }
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
            segments: vec![product_name(self.product).into()],
            attributes: BTreeMap::new(),
        })
    }
}

fn normalize(
    segment: &AccountSegment,
    payload: &Value,
    orders: &Value,
    isolated: bool,
) -> Result<AccountSnapshot, String> {
    let rows = if isolated {
        payload
            .get("assets")
            .and_then(Value::as_array)
            .ok_or_else(|| "Binance isolated margin assets are missing".to_string())?
            .iter()
            .flat_map(|asset| {
                [asset.get("baseAsset"), asset.get("quoteAsset")]
                    .into_iter()
                    .flatten()
                    .collect::<Vec<_>>()
            })
            .collect::<Vec<_>>()
    } else {
        payload
            .get("userAssets")
            .and_then(Value::as_array)
            .ok_or_else(|| "Binance cross margin userAssets are missing".to_string())?
            .iter()
            .collect::<Vec<_>>()
    };
    let balances = rows
        .into_iter()
        .map(|row| {
            let code = row
                .get("asset")
                .and_then(Value::as_str)
                .ok_or_else(|| "Binance margin asset is missing".to_string())?;
            let free = decimal(row.get("free").and_then(Value::as_str).unwrap_or("0"))?;
            let locked = decimal(row.get("locked").and_then(Value::as_str).unwrap_or("0"))?;
            let borrowed = decimal(row.get("borrowed").and_then(Value::as_str).unwrap_or("0"))?;
            let interest = decimal(row.get("interest").and_then(Value::as_str).unwrap_or("0"))?;
            let scale = free.scale.max(locked.scale);
            let total = DecimalValue::new(
                rescale(free, scale)?
                    .checked_add(rescale(locked, scale)?)
                    .ok_or("margin balance overflow")?,
                scale,
            );
            Ok(Balance {
                asset_id: format!("asset:crypto:{code}"),
                asset_code: code.into(),
                total,
                available: Some(free),
                locked: Some(locked),
                borrowed: Some(borrowed),
                interest: Some(interest),
            })
        })
        .collect::<Result<Vec<_>, String>>()?;
    let open_orders = orders
        .as_array()
        .ok_or_else(|| "Binance margin open orders are missing".to_string())?
        .iter()
        .filter_map(|row| {
            Some(OpenOrder {
                order_id: row.get("clientOrderId")?.as_str()?.into(),
                venue_order_id: row
                    .get("orderId")
                    .and_then(Value::as_i64)
                    .map(|v| v.to_string()),
                instrument_id: format!(
                    "instrument:crypto:{}",
                    row.get("symbol")?.as_str()?.to_ascii_uppercase()
                ),
                side: row.get("side")?.as_str()?.to_ascii_lowercase(),
                quantity: decimal(row.get("origQty").and_then(Value::as_str).unwrap_or("0"))
                    .ok()?,
                filled_quantity: decimal(
                    row.get("executedQty")
                        .and_then(Value::as_str)
                        .unwrap_or("0"),
                )
                .ok()?,
                status: row.get("status")?.as_str()?.to_ascii_lowercase(),
            })
        })
        .collect();
    Ok(AccountSnapshot {
        segment_key: segment.segment_key.clone(),
        balances,
        collateral: Vec::new(),
        positions: Vec::new(),
        open_orders,
        status: AccountStatus::Ready,
        observed_at_unix_nanos: now_nanos(),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: Some(crate::domain::ExternalAccountModel::Margin),
        margin_mode: None,
        position_mode: None,
        partial: false,
    })
}

fn product_name(product: ProductFamily) -> &'static str {
    match product {
        ProductFamily::CrossMargin => "cross-margin",
        ProductFamily::IsolatedMargin => "isolated-margin",
        _ => "margin",
    }
}

fn decimal(value: &str) -> Result<DecimalValue, String> {
    let negative = value.starts_with('-');
    let unsigned = value.trim_start_matches('-');
    let (whole, fraction) = unsigned.split_once('.').unwrap_or((unsigned, ""));
    let mantissa = format!("{whole}{fraction}")
        .parse::<i64>()
        .map_err(|_| format!("invalid Binance margin decimal: {value}"))?;
    Ok(DecimalValue::new(
        if negative { -mantissa } else { mantissa },
        fraction.len() as u8,
    ))
}

fn rescale(value: DecimalValue, scale: u8) -> Result<i64, String> {
    if value.scale > scale {
        return Err("decimal scale cannot shrink".into());
    }
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
    use super::*;
    use crate::domain::account::ExternalAccountIdentity;

    fn segment(key: &str) -> AccountSegment {
        AccountSegment {
            identity: ExternalAccountIdentity::new("binance", "margin").unwrap(),
            segment_key: key.into(),
            environment: "paper".into(),
            account_model: Some("margin".into()),
        }
    }

    #[test]
    fn normalizes_cross_margin_assets_and_orders() {
        let snapshot = normalize(
            &segment("cross_margin"),
            &serde_json::json!({"userAssets":[{"asset":"USDT","free":"10.5","locked":"1","borrowed":"2","interest":"0.1"}]}),
            &serde_json::json!([{"clientOrderId":"client-1","orderId":7,"symbol":"BTCUSDT","side":"BUY","origQty":"1","executedQty":"0","status":"NEW"}]),
            false,
        )
        .unwrap();
        assert_eq!(snapshot.balances[0].asset_code, "USDT");
        assert_eq!(
            snapshot.balances[0].borrowed.unwrap(),
            DecimalValue::new(2, 0)
        );
        assert_eq!(snapshot.open_orders[0].venue_order_id.as_deref(), Some("7"));
    }

    #[test]
    fn normalizes_isolated_margin_base_and_quote_assets() {
        let snapshot = normalize(
            &segment("isolated_margin"),
            &serde_json::json!({"assets":[{"symbol":"BTCUSDT","baseAsset":{"asset":"BTC","free":"1","locked":"0","borrowed":"0","interest":"0"},"quoteAsset":{"asset":"USDT","free":"100","locked":"0","borrowed":"5","interest":"0.2"}}]}),
            &serde_json::json!([]),
            true,
        )
        .unwrap();
        assert_eq!(snapshot.balances.len(), 2);
        assert_eq!(
            snapshot.account_model,
            Some(crate::domain::ExternalAccountModel::Margin)
        );
    }
}
