//! Binance Spot private account REST adapter.

use std::collections::BTreeMap;
use std::sync::{Arc, RwLock};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::application::capabilities::account_facts::{
    canonical_account_identity, ExternalAccountModel as AccountModel,
    ExternalAccountSegment as AccountSegment, ExternalAccountSnapshot as AccountSnapshot,
    ExternalAccountStatus as AccountStatus, ExternalBalance as Balance,
    ExternalDecimal as DecimalValue, ExternalOpenOrder as OpenOrder,
};
use secrecy::{ExposeSecret, SecretString};
use serde_json::Value;

use crate::application::{
    AccountCredentialInspectionConnection, AccountMarketProfileConnection, AccountReadConnection,
    ExternalAccountCredentialProfile, ExternalMarketProfile as AccountMarketProfile,
    ExternalMarketProfileRequest as AccountMarketProfileRequest, IntegrationError,
};
use crate::services::participants::binance::signing::signed_query;
use crate::services::participants::binance::spot::runtime::{
    BinanceSpotProviderRuntime, QuotaAllocation, RequestPriority,
};
use crate::services::transport::http::{ExchangeError, PublicHttpClient};

pub struct BinanceSpotAccountConnection {
    client: BinanceSpotAccountClient,
}

pub struct BinanceSpotAccountMarketProfileConnection {
    client: BinanceSpotAccountClient,
}

impl BinanceSpotAccountMarketProfileConnection {
    pub fn new(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = BinanceSpotAccountClient::new(api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        Ok(Self { client })
    }
}

impl AccountMarketProfileConnection for BinanceSpotAccountMarketProfileConnection {
    fn fetch_market_profile(
        &mut self,
        request: &AccountMarketProfileRequest,
    ) -> Result<AccountMarketProfile, IntegrationError> {
        if request.source_symbol.as_str().trim().is_empty() {
            return Err(IntegrationError::InvalidPayload(
                "Binance market profile symbol is required".into(),
            ));
        }
        let fee_payload = self
            .client
            .trade_fee(request.source_symbol.as_str())
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let account_payload = self
            .client
            .account()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let burn_payload = self
            .client
            .bnb_burn_status()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        normalize_market_profile(request, &fee_payload, &account_payload, &burn_payload)
            .map_err(IntegrationError::InvalidPayload)
    }
}

impl BinanceSpotAccountConnection {
    pub fn new(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = BinanceSpotAccountClient::new(api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        Ok(Self { client })
    }
}

impl AccountReadConnection for BinanceSpotAccountConnection {
    fn fetch_account(
        &mut self,
        segment: &AccountSegment,
    ) -> Result<AccountSnapshot, IntegrationError> {
        let payload = self
            .client
            .account()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let orders = self
            .client
            .open_orders()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        normalize_account(segment, &payload, &orders).map_err(IntegrationError::InvalidPayload)
    }
}

impl AccountCredentialInspectionConnection for BinanceSpotAccountConnection {
    fn inspect_credential(&mut self) -> Result<ExternalAccountCredentialProfile, String> {
        let payload = self.client.account().map_err(|error| error.to_string())?;
        let mut permissions: Vec<String> = payload
            .get("permissions")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect();
        if !permissions.iter().any(|value| value == "read") {
            permissions.push("read".into());
        }
        if payload
            .get("canTrade")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            permissions.push("trade".into());
        }
        let account_type = payload
            .get("accountType")
            .and_then(Value::as_str)
            .map(str::to_owned);
        let mut attributes = std::collections::BTreeMap::new();
        if let Some(value) = account_type.clone() {
            attributes.insert("account_type".into(), value);
        }
        Ok(ExternalAccountCredentialProfile {
            remote_identity: None,
            account_type,
            permissions,
            segments: vec!["spot".into()],
            attributes,
        })
    }
}

#[derive(Clone)]
pub(crate) struct BinanceSpotAccountClient {
    runtime: BinanceSpotProviderRuntime,
    credentials: Arc<RwLock<PrincipalCredentials>>,
    base_url: String,
}

struct PrincipalCredentials {
    generation: u64,
    api_key: SecretString,
    secret: SecretString,
}

impl BinanceSpotAccountClient {
    pub(crate) fn new(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, ExchangeError> {
        Self::from_http(
            PublicHttpClient::new("kairos-integration/binance-spot-account")?,
            api_key,
            secret,
            base_url,
        )
    }

    pub(crate) fn from_http(
        http: PublicHttpClient,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, ExchangeError> {
        let runtime = BinanceSpotProviderRuntime::new(
            http,
            QuotaAllocation {
                request_weight_per_minute: 6_000,
                cancel_reserve_weight: 100,
            },
        )?;
        Self::from_runtime(runtime, api_key, secret, base_url)
    }

    pub(crate) fn from_runtime(
        runtime: BinanceSpotProviderRuntime,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, ExchangeError> {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into().trim_end_matches('/').to_string();
        if api_key.trim().is_empty() || secret.trim().is_empty() {
            return Err(ExchangeError::Authentication(
                "Binance Spot account credentials are required".into(),
            ));
        }
        if base_url.is_empty() {
            return Err(ExchangeError::InvalidRequest(
                "Binance Spot account base URL is required".into(),
            ));
        }
        Ok(Self {
            runtime,
            credentials: Arc::new(RwLock::new(PrincipalCredentials {
                generation: 1,
                api_key: SecretString::from(api_key),
                secret: SecretString::from(secret),
            })),
            base_url,
        })
    }

    #[cfg(test)]
    pub(crate) fn shares_http_worker_with(&self, other: &Self) -> bool {
        self.runtime.shares_http_worker_with(&other.runtime)
    }

    pub(crate) fn credential_generation(&self) -> Result<u64, ExchangeError> {
        self.credentials
            .read()
            .map(|credentials| credentials.generation)
            .map_err(|_| ExchangeError::Connection("Binance credential lock is poisoned".into()))
    }

    pub(crate) fn rotate_credentials(
        &self,
        api_key: impl Into<String>,
        secret: impl Into<String>,
    ) -> Result<u64, ExchangeError> {
        let api_key = api_key.into();
        let secret = secret.into();
        if api_key.trim().is_empty() || secret.trim().is_empty() {
            return Err(ExchangeError::Authentication(
                "Binance Spot account credentials are required".into(),
            ));
        }
        let mut credentials = self
            .credentials
            .write()
            .map_err(|_| ExchangeError::Connection("Binance credential lock is poisoned".into()))?;
        credentials.api_key = SecretString::from(api_key);
        credentials.secret = SecretString::from(secret);
        credentials.generation = credentials.generation.saturating_add(1);
        Ok(credentials.generation)
    }

    fn credential_snapshot(&self) -> Result<(u64, String, String), ExchangeError> {
        self.credentials
            .read()
            .map(|credentials| {
                (
                    credentials.generation,
                    credentials.api_key.expose_secret().to_owned(),
                    credentials.secret.expose_secret().to_owned(),
                )
            })
            .map_err(|_| ExchangeError::Connection("Binance credential lock is poisoned".into()))
    }

    fn signed_context(&self) -> Result<(u64, u64, String, String), ExchangeError> {
        self.runtime.ensure_clock_synchronized(&self.base_url)?;
        let (generation, api_key, secret) = self.credential_snapshot()?;
        Ok((self.runtime.now_millis()?, generation, api_key, secret))
    }

    async fn signed_context_async(&self) -> Result<(u64, u64, String, String), ExchangeError> {
        self.runtime
            .ensure_clock_synchronized_async(&self.base_url)
            .await?;
        let (generation, api_key, secret) = self.credential_snapshot()?;
        Ok((self.runtime.now_millis()?, generation, api_key, secret))
    }

    pub(crate) fn account(&self) -> Result<Value, ExchangeError> {
        self.runtime.acquire(20, RequestPriority::Reconciliation)?;
        self.signed_request("/api/v3/account", BTreeMap::new(), RequestMethod::Get)
    }

    pub(crate) async fn account_async(&self) -> Result<Value, ExchangeError> {
        self.runtime.acquire(20, RequestPriority::Reconciliation)?;
        self.signed_request_async("/api/v3/account", BTreeMap::new(), RequestMethod::Get)
            .await
    }

    fn open_orders(&self) -> Result<Value, ExchangeError> {
        self.query_open_orders(BTreeMap::new())
    }

    pub(crate) async fn open_orders_async(&self) -> Result<Value, ExchangeError> {
        self.query_open_orders_async(BTreeMap::new()).await
    }

    pub(crate) fn query_open_orders(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        let weight = if params.contains_key("symbol") { 6 } else { 80 };
        self.runtime
            .acquire(weight, RequestPriority::Reconciliation)?;
        self.signed_request("/api/v3/openOrders", params, RequestMethod::Get)
    }
    pub(crate) async fn query_open_orders_async(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        let weight = if params.contains_key("symbol") { 6 } else { 80 };
        self.runtime
            .acquire(weight, RequestPriority::Reconciliation)?;
        self.signed_request_async("/api/v3/openOrders", params, RequestMethod::Get)
            .await
    }
    pub(crate) fn query_history(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.runtime.acquire(20, RequestPriority::Reconciliation)?;
        self.signed_request("/api/v3/allOrders", params, RequestMethod::Get)
    }
    pub(crate) async fn query_history_async(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.runtime.acquire(20, RequestPriority::Reconciliation)?;
        self.signed_request_async("/api/v3/allOrders", params, RequestMethod::Get)
            .await
    }
    pub(crate) fn query_detail(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.runtime.acquire(4, RequestPriority::Reconciliation)?;
        self.signed_request("/api/v3/order", params, RequestMethod::Get)
    }
    pub(crate) async fn query_detail_async(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.runtime.acquire(4, RequestPriority::Reconciliation)?;
        self.signed_request_async("/api/v3/order", params, RequestMethod::Get)
            .await
    }

    fn trade_fee(&self, symbol: &str) -> Result<Value, ExchangeError> {
        self.runtime.acquire(1, RequestPriority::Background)?;
        let mut params = BTreeMap::new();
        params.insert("symbol".into(), symbol.to_ascii_uppercase());
        self.signed_request("/sapi/v1/asset/tradeFee", params, RequestMethod::Get)
    }

    pub(crate) async fn trade_fee_async(&self, symbol: &str) -> Result<Value, ExchangeError> {
        self.runtime.acquire(1, RequestPriority::Background)?;
        let mut params = BTreeMap::new();
        params.insert("symbol".into(), symbol.to_ascii_uppercase());
        self.signed_request_async("/sapi/v1/asset/tradeFee", params, RequestMethod::Get)
            .await
    }

    fn bnb_burn_status(&self) -> Result<Value, ExchangeError> {
        self.runtime.acquire(1, RequestPriority::Background)?;
        self.signed_request("/sapi/v1/bnbBurn", BTreeMap::new(), RequestMethod::Get)
    }

    pub(crate) async fn bnb_burn_status_async(&self) -> Result<Value, ExchangeError> {
        self.runtime.acquire(1, RequestPriority::Background)?;
        self.signed_request_async("/sapi/v1/bnbBurn", BTreeMap::new(), RequestMethod::Get)
            .await
    }

    pub(super) fn submit_order(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.runtime.acquire_new_order()?;
        self.signed_request("/api/v3/order", params, RequestMethod::Post)
    }

    pub(super) async fn submit_order_async(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.runtime.acquire_new_order()?;
        self.signed_request_async("/api/v3/order", params, RequestMethod::Post)
            .await
    }

    pub(super) fn cancel_order(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.runtime.acquire(1, RequestPriority::Cancel)?;
        self.signed_request("/api/v3/order", params, RequestMethod::Delete)
    }

    pub(super) async fn cancel_order_async(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.runtime.acquire(1, RequestPriority::Cancel)?;
        self.signed_request_async("/api/v3/order", params, RequestMethod::Delete)
            .await
    }

    pub(super) fn listen_key(&self) -> Result<String, ExchangeError> {
        let (_, api_key, _) = self.credential_snapshot()?;
        let endpoint = format!("{}/api/v3/userDataStream", self.base_url);
        let payload = self.runtime.http().post_json_with_headers(
            &endpoint,
            &[("X-MBX-APIKEY", api_key)],
            &Value::Object(Default::default()),
        )?;
        payload
            .get("listenKey")
            .and_then(Value::as_str)
            .map(str::to_owned)
            .ok_or_else(|| ExchangeError::InvalidRequest("Binance listen key is missing".into()))
    }

    pub(super) fn user_data_subscription_request(
        &self,
        request_id: &str,
    ) -> Result<(String, u64), ExchangeError> {
        self.runtime.acquire(2, RequestPriority::Background)?;
        let (timestamp, generation, api_key, secret) = self.signed_context()?;
        let params = BTreeMap::from([
            ("apiKey".to_string(), api_key.clone()),
            ("recvWindow".to_string(), "5000".to_string()),
            ("timestamp".to_string(), timestamp.to_string()),
        ]);
        let signature = signed_query(&secret, params)?;
        let payload = serde_json::to_string(&serde_json::json!({
            "id": request_id,
            "method": "userDataStream.subscribe.signature",
            "params": {
                "apiKey": api_key,
                "recvWindow": 5000,
                "timestamp": timestamp,
                "signature": signature.signature,
            }
        }))
        .map_err(ExchangeError::Response)?;
        Ok((payload, generation))
    }

    pub(super) async fn user_data_subscription_request_async(
        &self,
        request_id: &str,
    ) -> Result<(String, u64), ExchangeError> {
        self.runtime.acquire(2, RequestPriority::Background)?;
        let (timestamp, generation, api_key, secret) = self.signed_context_async().await?;
        let params = BTreeMap::from([
            ("apiKey".to_string(), api_key.clone()),
            ("recvWindow".to_string(), "5000".to_string()),
            ("timestamp".to_string(), timestamp.to_string()),
        ]);
        let signature = signed_query(&secret, params)?;
        let payload = serde_json::to_string(&serde_json::json!({
            "id": request_id,
            "method": "userDataStream.subscribe.signature",
            "params": {
                "apiKey": api_key,
                "recvWindow": 5000,
                "timestamp": timestamp,
                "signature": signature.signature,
            }
        }))
        .map_err(ExchangeError::Response)?;
        Ok((payload, generation))
    }

    pub(crate) fn signed_post(
        &self,
        path: &str,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.runtime.acquire(1, RequestPriority::Background)?;
        self.signed_request(path, params, RequestMethod::Post)
    }

    pub(crate) async fn signed_post_async(
        &self,
        path: &str,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.runtime.acquire(1, RequestPriority::Background)?;
        self.signed_request_async(path, params, RequestMethod::Post)
            .await
    }

    pub(crate) fn signed_post_query(
        &self,
        path: &str,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.runtime.acquire(1, RequestPriority::Background)?;
        self.signed_request(path, params, RequestMethod::PostQuery)
    }

    pub(crate) async fn signed_post_query_async(
        &self,
        path: &str,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.runtime.acquire(1, RequestPriority::Background)?;
        self.signed_request_async(path, params, RequestMethod::PostQuery)
            .await
    }

    pub(crate) fn signed_delete(
        &self,
        path: &str,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.runtime.acquire(1, RequestPriority::Cancel)?;
        self.signed_request(path, params, RequestMethod::Delete)
    }

    pub(crate) fn margin_listen_key(
        &self,
        isolated_symbol: Option<&str>,
    ) -> Result<String, ExchangeError> {
        let (_, api_key, _) = self.credential_snapshot()?;
        let endpoint = if let Some(symbol) = isolated_symbol {
            let query = url::form_urlencoded::Serializer::new(String::new())
                .append_pair("symbol", &symbol.to_ascii_uppercase())
                .finish();
            format!("{}/sapi/v1/userDataStream/isolated?{query}", self.base_url)
        } else {
            format!("{}/sapi/v1/userDataStream", self.base_url)
        };
        let payload = self.runtime.http().post_json_with_headers(
            &endpoint,
            &[("X-MBX-APIKEY", api_key)],
            &Value::Object(Default::default()),
        )?;
        payload
            .get("listenKey")
            .and_then(Value::as_str)
            .map(str::to_owned)
            .ok_or_else(|| {
                ExchangeError::InvalidRequest("Binance margin listen key is missing".into())
            })
    }

    pub(crate) fn signed_get(
        &self,
        path: &str,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.runtime.acquire(1, RequestPriority::Background)?;
        self.signed_request(path, params, RequestMethod::Get)
    }

    pub(crate) async fn signed_get_async(
        &self,
        path: &str,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.runtime.acquire(1, RequestPriority::Background)?;
        self.signed_request_async(path, params, RequestMethod::Get)
            .await
    }

    fn signed_request(
        &self,
        path: &str,
        mut params: BTreeMap<String, String>,
        method: RequestMethod,
    ) -> Result<Value, ExchangeError> {
        let (timestamp, _, api_key, secret) = self.signed_context()?;
        params.insert("timestamp".into(), timestamp.to_string());
        params.insert("recvWindow".into(), "5000".into());
        let signed = signed_query(&secret, params)?;
        let endpoint = format!("{}{}", self.base_url, path);
        let mut query = url::form_urlencoded::parse(signed.query.as_bytes())
            .map(|(key, value)| (key.into_owned(), value.into_owned()))
            .collect::<Vec<_>>();
        query.push(("signature".into(), signed.signature));
        let refs = query
            .iter()
            .map(|(key, value)| (key.as_str(), value.clone()))
            .collect::<Vec<_>>();
        let headers = [("X-MBX-APIKEY", api_key)];
        let http = self.runtime.http();
        let response = match method {
            RequestMethod::Get => {
                http.get_json_response_with_headers_and_query(&endpoint, &refs, &headers)
            }
            RequestMethod::Post => {
                http.post_json_response_with_headers_and_query(&endpoint, &refs, &headers)
            }
            RequestMethod::PostQuery => {
                http.post_query_json_response_with_headers_and_query(&endpoint, &refs, &headers)
            }
            RequestMethod::Delete => {
                http.delete_json_response_with_headers_and_query(&endpoint, &refs, &headers)
            }
        }?;
        self.runtime.observe_response(&response);
        Ok(response.body)
    }

    async fn signed_request_async(
        &self,
        path: &str,
        mut params: BTreeMap<String, String>,
        method: RequestMethod,
    ) -> Result<Value, ExchangeError> {
        let (timestamp, _, api_key, secret) = self.signed_context_async().await?;
        params.insert("timestamp".into(), timestamp.to_string());
        params.insert("recvWindow".into(), "5000".into());
        let signed = signed_query(&secret, params)?;
        let endpoint = format!("{}{}", self.base_url, path);
        let mut query = url::form_urlencoded::parse(signed.query.as_bytes())
            .map(|(key, value)| (key.into_owned(), value.into_owned()))
            .collect::<Vec<_>>();
        query.push(("signature".into(), signed.signature));
        let refs = query
            .iter()
            .map(|(key, value)| (key.as_str(), value.clone()))
            .collect::<Vec<_>>();
        let headers = [("X-MBX-APIKEY", api_key)];
        let http = self.runtime.async_http();
        let response = match method {
            RequestMethod::Get => {
                http.get_json_response_with_headers_and_query(&endpoint, &refs, &headers)
                    .await
            }
            RequestMethod::Post => {
                http.post_json_response_with_headers_and_query(&endpoint, &refs, &headers)
                    .await
            }
            RequestMethod::PostQuery => {
                http.post_query_json_response_with_headers_and_query(&endpoint, &refs, &headers)
                    .await
            }
            RequestMethod::Delete => {
                http.delete_json_response_with_headers_and_query(&endpoint, &refs, &headers)
                    .await
            }
        }?;
        self.runtime.observe_response(&response);
        Ok(response.body)
    }
}

pub(super) enum RequestMethod {
    Get,
    Post,
    PostQuery,
    Delete,
}

pub(crate) fn normalize_account(
    segment: &AccountSegment,
    payload: &Value,
    orders: &Value,
) -> Result<AccountSnapshot, String> {
    let balances = payload
        .get("balances")
        .and_then(Value::as_array)
        .ok_or_else(|| "Binance account balances is missing".to_string())?;
    let mut result = Vec::new();
    for item in balances {
        let code = item
            .get("asset")
            .and_then(Value::as_str)
            .ok_or_else(|| "Binance account balance asset is missing".to_string())?;
        let free = decimal(
            item.get("free")
                .and_then(Value::as_str)
                .ok_or_else(|| "Binance account balance free is missing".to_string())?,
        )?;
        let locked = decimal(
            item.get("locked")
                .and_then(Value::as_str)
                .ok_or_else(|| "Binance account balance locked is missing".to_string())?,
        )?;
        let scale = free.scale.max(locked.scale);
        let total = DecimalValue::new(
            rescale(free, scale)?
                .checked_add(rescale(locked, scale)?)
                .ok_or_else(|| "balance quantity overflow".to_string())?,
            scale,
        );
        result.push(Balance {
            asset_id: kairos_domain_types::AssetId::new(format!("asset:crypto:{code}"))?,
            asset_code: kairos_domain_types::Currency::new(code)?,
            total,
            available: Some(free),
            locked: Some(locked),
            ..Default::default()
        });
    }
    let open_orders = orders
        .as_array()
        .ok_or_else(|| "Binance open orders is not an array".to_string())?
        .iter()
        .map(|value| normalize_open_order(value, "binance-spot"))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(AccountSnapshot {
        segment_key: segment.segment_key.clone(),
        balances: result,
        collateral: Vec::new(),
        positions: Vec::new(),
        open_orders,
        status: AccountStatus::Ready,
        observed_at_unix_nanos: now_nanos().into(),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: segment
            .account_model
            .as_deref()
            .and_then(AccountModel::parse),
        margin_mode: None,
        position_mode: None,
        partial: false,
    })
}

pub(crate) fn normalize_market_profile(
    request: &AccountMarketProfileRequest,
    fee_payload: &Value,
    account_payload: &Value,
    burn_payload: &Value,
) -> Result<AccountMarketProfile, String> {
    let row = fee_payload
        .as_array()
        .and_then(|rows| rows.first())
        .ok_or_else(|| "Binance trade fee response is missing".to_string())?;
    let maker_fee = decimal_field(row, "maker")
        .or_else(|| decimal_field(row, "makerCommission"))
        .ok_or_else(|| "Binance maker fee is missing".to_string())?;
    let taker_fee = decimal_field(row, "taker")
        .or_else(|| decimal_field(row, "takerCommission"))
        .ok_or_else(|| "Binance taker fee is missing".to_string())?;
    let burn_enabled = burn_payload
        .get("spotBNBBurn")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let account_model = account_payload
        .get("accountType")
        .and_then(Value::as_str)
        .and_then(AccountModel::parse);
    Ok(AccountMarketProfile {
        account_id: request.account_id.clone(),
        segment_key: request.segment_key.clone(),
        market_id: request.market_id.clone(),
        account_model,
        margin_mode: None,
        position_mode: None,
        maker_fee: Some(maker_fee),
        taker_fee: Some(taker_fee),
        fee_currency: Some(kairos_domain_types::Currency::new("BNB").expect("static fee currency")),
        fee_discount: burn_enabled.then_some(DecimalValue::new(25, 2)),
        fee_tier: burn_enabled.then(|| "bnb_burn".into()),
        source: "binance.spot".into(),
        observed_at_unix_nanos: now_nanos().into(),
    })
}

fn decimal_field(value: &Value, field: &str) -> Option<DecimalValue> {
    value
        .get(field)
        .and_then(Value::as_str)
        .and_then(|value| decimal(value).ok())
}

fn normalize_open_order(value: &Value, product: &str) -> Result<OpenOrder, String> {
    let remote_order_id = value
        .get("orderId")
        .map(value_as_string)
        .ok_or_else(|| "Binance open order id is missing".to_string())?;
    let symbol = value
        .get("symbol")
        .and_then(Value::as_str)
        .ok_or_else(|| "Binance open order symbol is missing".to_string())?;
    let (instrument_id, _) = canonical_account_identity(product, symbol)?;
    let local_order_id = value
        .get("clientOrderId")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or(&remote_order_id);
    Ok(OpenOrder {
        order_id: kairos_domain_types::OrderId::new(local_order_id)?,
        remote_order_id: Some(kairos_domain_types::RemoteOrderId::new(remote_order_id)?),
        instrument_id,
        side: crate::application::capabilities::execution_facts::normalize_order_side(
            value
                .get("side")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        ),
        quantity: decimal(value.get("origQty").and_then(Value::as_str).unwrap_or("0"))?,
        filled_quantity: decimal(
            value
                .get("executedQty")
                .and_then(Value::as_str)
                .unwrap_or("0"),
        )?,
        status: crate::application::capabilities::execution_facts::normalize_order_status(
            value
                .get("status")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        ),
    })
}

fn value_as_string(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}

fn rescale(value: DecimalValue, scale: u8) -> Result<i64, String> {
    let mut mantissa = value.mantissa;
    for _ in value.scale..scale {
        mantissa = mantissa
            .checked_mul(10)
            .ok_or_else(|| "decimal rescale overflow".to_string())?;
    }
    Ok(mantissa)
}

fn decimal(value: &str) -> Result<DecimalValue, String> {
    DecimalValue::parse(value)
}

fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::{normalize_account, normalize_market_profile, BinanceSpotAccountClient};
    use crate::application::capabilities::account_facts::{
        ExternalAccountIdentity, ExternalAccountSegment as AccountSegment,
    };
    use crate::application::ExternalMarketProfileRequest as AccountMarketProfileRequest;

    #[test]
    fn normalizes_private_balances_without_vendor_payloads() {
        let segment = AccountSegment {
            identity: ExternalAccountIdentity::new("binance", "main").unwrap(),
            segment_key: kairos_domain_types::SegmentKey::new("spot").unwrap(),
            environment: "live".into(),
            account_model: None,
        };
        let value =
            serde_json::json!({"balances":[{"asset":"USDT","free":"10.25","locked":"0.75"}]});
        let result = normalize_account(
            &segment,
            &value,
            &serde_json::json!([{"orderId":7,"clientOrderId":"local-7","symbol":"BTCUSDT","side":"BUY","origQty":"1.5","executedQty":"0.5","status":"NEW"}]),
        )
        .unwrap();
        assert_eq!(result.balances[0].asset_id, "asset:crypto:USDT");
        assert_eq!(result.balances[0].total.mantissa, 1100);
        assert_eq!(result.balances[0].total.scale, 2);
        assert_eq!(result.open_orders[0].order_id, "local-7");
        assert_eq!(result.open_orders[0].filled_quantity.mantissa, 5);
    }

    #[test]
    fn normalizes_spot_market_fee_and_bnb_discount_profile() {
        let request = AccountMarketProfileRequest {
            account_id: kairos_domain_types::AccountId::new("main").unwrap(),
            segment_key: kairos_domain_types::SegmentKey::new("spot").unwrap(),
            market_id: kairos_domain_types::MarketId::new("market:binance:BTCUSDT").unwrap(),
            source_symbol: kairos_domain_types::Symbol::new("BTCUSDT").unwrap(),
        };
        let result = normalize_market_profile(
            &request,
            &serde_json::json!([{"symbol":"BTCUSDT","maker":"0.001","taker":"0.0012"}]),
            &serde_json::json!({"accountType":"SPOT"}),
            &serde_json::json!({"spotBNBBurn":true}),
        )
        .unwrap();
        assert_eq!(
            result.account_model,
            Some(crate::application::capabilities::account_facts::ExternalAccountModel::NoMargin)
        );
        assert_eq!(result.fee_discount.unwrap().mantissa, 25);
        assert_eq!(result.fee_tier.as_deref(), Some("bnb_burn"));
    }

    #[test]
    fn cloned_principal_client_shares_clock_and_rotated_credentials() {
        let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let address = listener.local_addr().unwrap();
        let server_time = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_millis() as u64
            + 2_000;
        let server = std::thread::spawn(move || {
            use std::io::{Read, Write};

            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 2_048];
            let read = stream.read(&mut request).unwrap();
            assert!(String::from_utf8_lossy(&request[..read]).starts_with("GET /api/v3/time "));
            let body = serde_json::json!({"serverTime": server_time}).to_string();
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            )
            .unwrap();
        });

        let client =
            BinanceSpotAccountClient::new("old-key", "old-secret", format!("http://{address}"))
                .unwrap();
        let clone = client.clone();
        let (first_timestamp, first_generation, first_key, _) = client.signed_context().unwrap();
        let (second_timestamp, second_generation, second_key, _) = clone.signed_context().unwrap();
        assert!(first_timestamp >= server_time.saturating_sub(100));
        assert_eq!(first_generation, 1);
        assert_eq!(second_generation, 1);
        assert_eq!(first_key, "old-key");
        assert_eq!(second_key, "old-key");
        assert!(second_timestamp >= first_timestamp);

        assert_eq!(
            clone.rotate_credentials("new-key", "new-secret").unwrap(),
            2
        );
        let (generation, key, secret) = client.credential_snapshot().unwrap();
        assert_eq!(generation, 2);
        assert_eq!(key, "new-key");
        assert_eq!(secret, "new-secret");
        server.join().unwrap();
    }
}
