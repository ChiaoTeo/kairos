//! Binance Simple Earn capability.

use std::collections::BTreeMap;

use serde_json::Value;

use crate::application::{
    Connection, ConnectionSpec, EarnActionResult, EarnConnection, EarnPosition, EarnProduct,
    EarnProductType, EarnRedeemRequest, EarnReward, EarnSubscribeRequest,
};
use crate::domain::{AccessScope, IntegrationCapability, ProductFamily, TransportKind};
use crate::services::connections::ManagedConnection;

use super::spot::account::BinanceSpotAccountClient;

pub struct BinanceSimpleEarnConnection {
    connection: ManagedConnection,
    client: BinanceSpotAccountClient,
}

impl BinanceSimpleEarnConnection {
    pub fn new(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = BinanceSpotAccountClient::new(api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        let spec = ConnectionSpec {
            connection_id: "account.binance.earn.rest".into(),
            provider: "binance".into(),
            product: Some(ProductFamily::Earn),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::Earn,
            credential_id: Some("binance".into()),
            asset_type: None,
        };
        Ok(Self {
            connection: ManagedConnection::new(spec, Vec::new())?,
            client,
        })
    }

    fn query(&self, path: &str, params: BTreeMap<String, String>) -> Result<Value, String> {
        self.client
            .signed_get(path, params)
            .map_err(|error| error.to_string())
    }

    fn action(
        &self,
        path: &str,
        params: BTreeMap<String, String>,
    ) -> Result<EarnActionResult, String> {
        let payload = self
            .client
            .signed_post(path, params)
            .map_err(|error| error.to_string())?;
        let success = payload
            .get("success")
            .and_then(Value::as_bool)
            .unwrap_or(true);
        Ok(EarnActionResult {
            accepted: success,
            action_id: payload
                .get("positionId")
                .or_else(|| payload.get("purchaseId"))
                .and_then(value_string),
            status: if success { "accepted" } else { "rejected" }.into(),
            reason: payload
                .get("msg")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .into(),
        })
    }
}

impl Connection for BinanceSimpleEarnConnection {
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

impl EarnConnection for BinanceSimpleEarnConnection {
    fn products(
        &mut self,
        asset: Option<&str>,
        product_type: Option<EarnProductType>,
    ) -> Result<Vec<EarnProduct>, String> {
        self.start()?;
        let mut params = BTreeMap::new();
        if let Some(asset) = asset {
            params.insert("asset".into(), asset.into());
        }
        if let Some(product_type) = product_type {
            params.insert(
                "productType".into(),
                product_type_name(product_type).to_ascii_uppercase(),
            );
        }
        let payload = self.query("/sapi/v1/simple-earn/products", params)?;
        Ok(payload
            .get("rows")
            .and_then(Value::as_array)
            .map(|rows| rows.iter().filter_map(normalize_product).collect())
            .unwrap_or_default())
    }

    fn positions(&mut self, asset: Option<&str>) -> Result<Vec<EarnPosition>, String> {
        self.start()?;
        let mut params = BTreeMap::new();
        if let Some(asset) = asset {
            params.insert("asset".into(), asset.into());
        }
        let payload = self.query("/sapi/v1/simple-earn/positions", params)?;
        Ok(payload
            .get("rows")
            .and_then(Value::as_array)
            .map(|rows| rows.iter().filter_map(normalize_position).collect())
            .unwrap_or_default())
    }

    fn rewards(&mut self, asset: Option<&str>) -> Result<Vec<EarnReward>, String> {
        self.start()?;
        let mut params = BTreeMap::new();
        if let Some(asset) = asset {
            params.insert("asset".into(), asset.into());
        }
        let payload = self.query("/sapi/v1/simple-earn/rewardsRecord", params)?;
        Ok(payload
            .get("rows")
            .and_then(Value::as_array)
            .map(|rows| rows.iter().filter_map(normalize_reward).collect())
            .unwrap_or_default())
    }

    fn subscribe(&mut self, request: &EarnSubscribeRequest) -> Result<EarnActionResult, String> {
        self.start()?;
        let path = match request.product_type {
            EarnProductType::Locked => "/sapi/v1/simple-earn/locked/subscribe",
            EarnProductType::Flexible => "/sapi/v1/simple-earn/flexible/subscribe",
        };
        let mut params = BTreeMap::from([
            (String::from("productId"), request.product_id.clone()),
            (String::from("amount"), request.amount.clone()),
        ]);
        if let Some(auto_renew) = request.auto_renew {
            params.insert("autoSubscribe".into(), auto_renew.to_string());
        }
        self.action(path, params)
    }

    fn redeem(&mut self, request: &EarnRedeemRequest) -> Result<EarnActionResult, String> {
        self.start()?;
        let path = match request.product_type {
            EarnProductType::Locked => "/sapi/v1/simple-earn/locked/redeem",
            EarnProductType::Flexible => "/sapi/v1/simple-earn/flexible/redeem",
        };
        let mut params = BTreeMap::from([(String::from("productId"), request.product_id.clone())]);
        if let Some(amount) = &request.amount {
            params.insert("amount".into(), amount.clone());
        }
        if let Some(destination) = &request.destination_account {
            params.insert("destAccount".into(), destination.clone());
        }
        self.action(path, params)
    }
}

fn normalize_product(value: &Value) -> Option<EarnProduct> {
    Some(EarnProduct {
        product_id: value.get("productId")?.as_str()?.into(),
        asset: value.get("asset")?.as_str()?.into(),
        product_type: product_type(
            value
                .get("productType")
                .and_then(Value::as_str)
                .unwrap_or("flexible"),
        ),
        annual_rate: value
            .get("latestAnnualPercentageRate")
            .map(value_string)
            .unwrap_or_default()
            .unwrap_or_default(),
        min_amount: value
            .get("minAmount")
            .map(value_string)
            .unwrap_or_default()
            .unwrap_or_default(),
        max_amount: value
            .get("maxAmount")
            .map(value_string)
            .unwrap_or_default()
            .unwrap_or_default(),
        status: value
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .into(),
        duration_days: value
            .get("duration")
            .and_then(Value::as_u64)
            .map(|value| value as u32),
    })
}

fn normalize_position(value: &Value) -> Option<EarnPosition> {
    Some(EarnPosition {
        product_id: value.get("productId")?.as_str()?.into(),
        asset: value.get("asset")?.as_str()?.into(),
        amount: value
            .get("totalAmount")
            .map(value_string)
            .unwrap_or_default()
            .unwrap_or_default(),
        rewards: value
            .get("totalRewards")
            .map(value_string)
            .unwrap_or_default()
            .unwrap_or_default(),
        annual_rate: value
            .get("latestAnnualPercentageRate")
            .map(value_string)
            .unwrap_or_default()
            .unwrap_or_default(),
        status: value
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .into(),
        updated_at_unix_millis: value.get("updateTime").and_then(Value::as_u64),
    })
}

fn normalize_reward(value: &Value) -> Option<EarnReward> {
    Some(EarnReward {
        asset: value.get("asset")?.as_str()?.into(),
        amount: value
            .get("rewardsAmount")
            .map(value_string)
            .unwrap_or_default()
            .unwrap_or_default(),
        product_id: value
            .get("productId")
            .and_then(Value::as_str)
            .map(str::to_owned),
        occurred_at_unix_millis: value.get("time").and_then(Value::as_u64),
    })
}

fn product_type(value: &str) -> EarnProductType {
    if value.eq_ignore_ascii_case("locked") {
        EarnProductType::Locked
    } else {
        EarnProductType::Flexible
    }
}
fn product_type_name(value: EarnProductType) -> &'static str {
    match value {
        EarnProductType::Locked => "locked",
        EarnProductType::Flexible => "flexible",
    }
}
fn value_string(value: &Value) -> Option<String> {
    Some(
        value
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| value.to_string()),
    )
}
