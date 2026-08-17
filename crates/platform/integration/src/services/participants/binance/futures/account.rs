use crate::services::participants::binance::ConnectionDomain;
use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::application::capabilities::account_facts::{
    external_instrument_ref, ExternalAccountEvent as AccountEvent,
    ExternalAccountModel as AccountModel, ExternalAccountSegment as AccountSegment,
    ExternalAccountSnapshot as AccountSnapshot, ExternalAccountStatus as AccountStatus,
    ExternalBalance as Balance, ExternalDecimal as DecimalValue, ExternalFillEvent as FillEvent,
    ExternalOpenOrder as OpenOrder, ExternalOrderEvent as OrderEvent,
    ExternalOrderStatus as OrderStatus, ExternalPosition as Position,
};
use crate::application::{
    AccountCredentialInspectionConnection, AccountEventReceive, AccountEventStreamConnection,
    AccountReadConnection, ExternalAccountCredentialProfile, IntegrationError,
};
use crate::services::participants::binance::signing::signed_query;
use crate::services::participants::binance::spot::runtime::{
    BinanceRequestRuntime, QuotaAllocation, RequestPriority,
};
use crate::services::transport::http::{AsyncPublicHttpClient, ExchangeError, PublicHttpClient};
use crate::services::transport::websocket::{SocketEvent, TokioSocket};
use serde_json::Value;

pub struct BinanceFuturesAccountConnection {
    client: BinanceFuturesAccountClient,
}

impl BinanceFuturesAccountConnection {
    pub fn new(
        product: ConnectionDomain,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        if !matches!(
            product,
            ConnectionDomain::UsdMFutures | ConnectionDomain::CoinMFutures
        ) {
            return Err("Binance futures account requires USD-M or Coin-M product".into());
        }
        let client = BinanceFuturesAccountClient::new(product, api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        Ok(Self { client })
    }
}

impl AccountReadConnection for BinanceFuturesAccountConnection {
    fn fetch_account(
        &mut self,
        segment: &AccountSegment,
    ) -> Result<AccountSnapshot, IntegrationError> {
        let account = self
            .client
            .account()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let positions = self
            .client
            .positions()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let orders = self
            .client
            .open_orders()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        normalize_account(segment, &account, &positions, &orders)
            .map_err(IntegrationError::InvalidPayload)
    }
}

impl AccountCredentialInspectionConnection for BinanceFuturesAccountConnection {
    fn inspect_credential(&mut self) -> Result<ExternalAccountCredentialProfile, String> {
        let payload = self.client.account().map_err(|error| error.to_string())?;
        let account_type = payload
            .get("accountType")
            .and_then(Value::as_str)
            .map(str::to_owned);
        let mut permissions = vec!["read".into()];
        if payload
            .get("canTrade")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            permissions.push("trade".into());
        }
        let segment = match self.client.product {
            ConnectionDomain::UsdMFutures => "usd_m_futures",
            ConnectionDomain::CoinMFutures => "coin_m_futures",
            _ => "futures",
        };
        Ok(ExternalAccountCredentialProfile {
            remote_identity: None,
            account_type,
            permissions,
            segments: vec![segment.into()],
            attributes: Default::default(),
        })
    }
}

#[derive(Clone)]
pub(crate) struct BinanceFuturesAccountClient {
    http: PublicHttpClient,
    async_http: AsyncPublicHttpClient,
    product: ConnectionDomain,
    api_key: String,
    secret: String,
    base_url: String,
    clock_offset_millis: Arc<tokio::sync::Mutex<Option<i64>>>,
    runtime: BinanceRequestRuntime,
}

impl BinanceFuturesAccountClient {
    pub(crate) fn new(
        product: ConnectionDomain,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, ExchangeError> {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into().trim_end_matches('/').to_string();
        if api_key.trim().is_empty() || secret.trim().is_empty() {
            return Err(ExchangeError::Authentication(
                "Binance futures credentials are required".into(),
            ));
        }
        if base_url.is_empty() {
            return Err(ExchangeError::InvalidRequest(
                "Binance futures base URL is required".into(),
            ));
        }
        let http = PublicHttpClient::new("kairos-integration/binance-futures-account")?;
        let runtime = BinanceRequestRuntime::new(
            http.clone(),
            QuotaAllocation {
                request_weight_per_minute: 6_000,
                cancel_reserve_weight: 100,
            },
        )?;
        Self::from_runtime(runtime, product, api_key, secret, base_url)
    }

    pub(crate) fn from_runtime(
        runtime: BinanceRequestRuntime,
        product: ConnectionDomain,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, ExchangeError> {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into().trim_end_matches('/').to_string();
        if api_key.trim().is_empty() || secret.trim().is_empty() {
            return Err(ExchangeError::Authentication(
                "Binance futures credentials are required".into(),
            ));
        }
        if base_url.is_empty() {
            return Err(ExchangeError::InvalidRequest(
                "Binance futures base URL is required".into(),
            ));
        }
        Ok(Self {
            http: runtime.http(),
            async_http: runtime.async_http(),
            product,
            api_key,
            secret,
            base_url,
            clock_offset_millis: Arc::new(tokio::sync::Mutex::new(None)),
            runtime,
        })
    }

    fn signed_get(&self, path: &str) -> Result<Value, ExchangeError> {
        self.signed_request(path, BTreeMap::new(), RequestMethod::Get)
    }

    pub(super) async fn submit_order_async(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request_async(self.order_path(), params, RequestMethod::Post)
            .await
    }

    pub(super) async fn cancel_order_async(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request_async(self.order_path(), params, RequestMethod::Delete)
            .await
    }

    pub(super) fn listen_key(&self) -> Result<String, ExchangeError> {
        let path = match self.product {
            ConnectionDomain::UsdMFutures => "/fapi/v1/listenKey",
            ConnectionDomain::CoinMFutures => "/dapi/v1/listenKey",
            _ => unreachable!(),
        };
        let endpoint = format!("{}{path}", self.base_url);
        self.http
            .post_json_with_headers_and_query(
                &endpoint,
                &[],
                &[("X-MBX-APIKEY", self.api_key.clone())],
            )?
            .get("listenKey")
            .and_then(Value::as_str)
            .map(str::to_owned)
            .ok_or_else(|| {
                ExchangeError::InvalidRequest("Binance futures listen key is missing".into())
            })
    }

    pub(super) async fn listen_key_async(&self) -> Result<String, ExchangeError> {
        self.runtime.acquire(1, RequestPriority::Background)?;
        let path = match self.product {
            ConnectionDomain::UsdMFutures => "/fapi/v1/listenKey",
            ConnectionDomain::CoinMFutures => "/dapi/v1/listenKey",
            _ => unreachable!(),
        };
        let endpoint = format!("{}{path}", self.base_url);
        self.async_http
            .post_json_response_with_headers_and_query(
                &endpoint,
                &[],
                &[("X-MBX-APIKEY", self.api_key.clone())],
            )
            .await?
            .body
            .get("listenKey")
            .and_then(Value::as_str)
            .map(str::to_owned)
            .ok_or_else(|| {
                ExchangeError::InvalidRequest("Binance futures listen key is missing".into())
            })
    }

    pub(super) async fn keepalive_listen_key_async(
        &self,
        listen_key: &str,
    ) -> Result<(), ExchangeError> {
        self.runtime.acquire(1, RequestPriority::Background)?;
        let path = match self.product {
            ConnectionDomain::UsdMFutures => "/fapi/v1/listenKey",
            ConnectionDomain::CoinMFutures => "/dapi/v1/listenKey",
            _ => unreachable!(),
        };
        let endpoint = format!("{}{path}", self.base_url);
        self.async_http
            .put_query_json_response_with_headers_and_query(
                &endpoint,
                &[("listenKey", listen_key.to_owned())],
                &[("X-MBX-APIKEY", self.api_key.clone())],
            )
            .await
            .map(|_| ())
    }

    fn order_path(&self) -> &'static str {
        match self.product {
            ConnectionDomain::UsdMFutures => "/fapi/v1/order",
            ConnectionDomain::CoinMFutures => "/dapi/v1/order",
            _ => unreachable!(),
        }
    }

    fn signed_request(
        &self,
        path: &str,
        mut params: BTreeMap<String, String>,
        method: RequestMethod,
    ) -> Result<Value, ExchangeError> {
        params.insert("timestamp".into(), now_millis().to_string());
        params.insert("recvWindow".into(), "10000".into());
        let signed = signed_query(&self.secret, params)?;
        let endpoint = format!("{}{path}", self.base_url);
        let mut query = url::form_urlencoded::parse(signed.query.as_bytes())
            .map(|(key, value)| (key.into_owned(), value.into_owned()))
            .collect::<Vec<_>>();
        query.push(("signature".into(), signed.signature));
        let refs = query
            .iter()
            .map(|(key, value)| (key.as_str(), value.clone()))
            .collect::<Vec<_>>();
        let headers = [("X-MBX-APIKEY", self.api_key.clone())];
        match method {
            RequestMethod::Get => self
                .http
                .get_json_with_headers_and_query(&endpoint, &refs, &headers),
            RequestMethod::Post => self
                .http
                .post_json_with_headers_and_query(&endpoint, &refs, &headers),
            RequestMethod::Delete => self
                .http
                .delete_json_with_headers_and_query(&endpoint, &refs, &headers),
        }
    }

    async fn signed_request_async(
        &self,
        path: &str,
        params: BTreeMap<String, String>,
        method: RequestMethod,
    ) -> Result<Value, ExchangeError> {
        for attempt in 0..2 {
            self.runtime.acquire(
                1,
                match method {
                    RequestMethod::Get => RequestPriority::Reconciliation,
                    RequestMethod::Post => RequestPriority::NewOrder,
                    RequestMethod::Delete => RequestPriority::Cancel,
                },
            )?;
            let mut signed_params = params.clone();
            signed_params.insert(
                "timestamp".into(),
                self.signed_timestamp_async().await?.to_string(),
            );
            signed_params.insert("recvWindow".into(), "5000".into());
            let signed = signed_query(&self.secret, signed_params)?;
            let endpoint = format!("{}{path}", self.base_url);
            let mut query = url::form_urlencoded::parse(signed.query.as_bytes())
                .map(|(key, value)| (key.into_owned(), value.into_owned()))
                .collect::<Vec<_>>();
            query.push(("signature".into(), signed.signature));
            let refs = query
                .iter()
                .map(|(key, value)| (key.as_str(), value.clone()))
                .collect::<Vec<_>>();
            let headers = [("X-MBX-APIKEY", self.api_key.clone())];
            let result = match method {
                RequestMethod::Get => self
                    .async_http
                    .get_json_response_with_headers_and_query(&endpoint, &refs, &headers)
                    .await
                    .map(|response| response.body),
                RequestMethod::Post => self
                    .async_http
                    .post_json_response_with_headers_and_query(&endpoint, &refs, &headers)
                    .await
                    .map(|response| response.body),
                RequestMethod::Delete => self
                    .async_http
                    .delete_json_response_with_headers_and_query(&endpoint, &refs, &headers)
                    .await
                    .map(|response| response.body),
            };
            match result {
                Ok(value) => return Ok(value),
                Err(error)
                    if attempt == 0
                        && matches!(method, RequestMethod::Get)
                        && is_timestamp_rejection(&error) =>
                {
                    *self.clock_offset_millis.lock().await = None;
                }
                Err(error) => return Err(error),
            }
        }
        unreachable!("bounded Binance Futures timestamp retry loop")
    }

    async fn signed_timestamp_async(&self) -> Result<u64, ExchangeError> {
        let mut offset = self.clock_offset_millis.lock().await;
        if let Some(offset) = *offset {
            return apply_clock_offset(now_millis(), offset);
        }
        let started = now_millis();
        self.runtime.acquire(1, RequestPriority::Background)?;
        let path = match self.product {
            ConnectionDomain::UsdMFutures => "/fapi/v1/time",
            ConnectionDomain::CoinMFutures => "/dapi/v1/time",
            _ => unreachable!(),
        };
        let payload = self
            .async_http
            .get_json(&format!("{}{path}", self.base_url))
            .await
            .map_err(|error| ExchangeError::Preflight(error.to_string()))?;
        let completed = now_millis();
        let server_time = payload
            .get("serverTime")
            .and_then(Value::as_u64)
            .ok_or_else(|| {
                ExchangeError::Preflight("Binance Futures serverTime is missing".into())
            })?;
        let midpoint = started.saturating_add(completed.saturating_sub(started) / 2);
        let calibrated = i64::try_from(server_time)
            .unwrap_or(i64::MAX)
            .saturating_sub(i64::try_from(midpoint).unwrap_or(i64::MAX));
        *offset = Some(calibrated);
        apply_clock_offset(now_millis(), calibrated)
    }

    fn account(&self) -> Result<Value, ExchangeError> {
        self.signed_get(match self.product {
            ConnectionDomain::UsdMFutures => "/fapi/v2/account",
            ConnectionDomain::CoinMFutures => "/dapi/v1/account",
            _ => unreachable!(),
        })
    }

    pub(crate) async fn account_async(&self) -> Result<Value, ExchangeError> {
        self.signed_request_async(
            match self.product {
                ConnectionDomain::UsdMFutures => "/fapi/v2/account",
                ConnectionDomain::CoinMFutures => "/dapi/v1/account",
                _ => unreachable!(),
            },
            BTreeMap::new(),
            RequestMethod::Get,
        )
        .await
    }

    fn positions(&self) -> Result<Value, ExchangeError> {
        self.signed_get(match self.product {
            ConnectionDomain::UsdMFutures => "/fapi/v2/positionRisk",
            ConnectionDomain::CoinMFutures => "/dapi/v1/positionRisk",
            _ => unreachable!(),
        })
    }

    pub(crate) async fn positions_async(&self) -> Result<Value, ExchangeError> {
        self.signed_request_async(
            match self.product {
                ConnectionDomain::UsdMFutures => "/fapi/v2/positionRisk",
                ConnectionDomain::CoinMFutures => "/dapi/v1/positionRisk",
                _ => unreachable!(),
            },
            BTreeMap::new(),
            RequestMethod::Get,
        )
        .await
    }

    fn open_orders(&self) -> Result<Value, ExchangeError> {
        self.signed_request(
            match self.product {
                ConnectionDomain::UsdMFutures => "/fapi/v1/openOrders",
                ConnectionDomain::CoinMFutures => "/dapi/v1/openOrders",
                _ => unreachable!(),
            },
            BTreeMap::new(),
            RequestMethod::Get,
        )
    }

    pub(crate) async fn open_orders_async(&self) -> Result<Value, ExchangeError> {
        self.query_open_orders_async(BTreeMap::new()).await
    }

    pub(crate) fn query_open_orders(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request(
            match self.product {
                ConnectionDomain::UsdMFutures => "/fapi/v1/openOrders",
                ConnectionDomain::CoinMFutures => "/dapi/v1/openOrders",
                _ => unreachable!(),
            },
            params,
            RequestMethod::Get,
        )
    }
    pub(crate) async fn query_open_orders_async(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request_async(
            match self.product {
                ConnectionDomain::UsdMFutures => "/fapi/v1/openOrders",
                ConnectionDomain::CoinMFutures => "/dapi/v1/openOrders",
                _ => unreachable!(),
            },
            params,
            RequestMethod::Get,
        )
        .await
    }
    pub(crate) fn query_history(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request(
            match self.product {
                ConnectionDomain::UsdMFutures => "/fapi/v1/allOrders",
                ConnectionDomain::CoinMFutures => "/dapi/v1/allOrders",
                _ => unreachable!(),
            },
            params,
            RequestMethod::Get,
        )
    }
    pub(crate) async fn query_history_async(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request_async(
            match self.product {
                ConnectionDomain::UsdMFutures => "/fapi/v1/allOrders",
                ConnectionDomain::CoinMFutures => "/dapi/v1/allOrders",
                _ => unreachable!(),
            },
            params,
            RequestMethod::Get,
        )
        .await
    }
    pub(crate) fn query_detail(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request(self.order_path(), params, RequestMethod::Get)
    }
    pub(crate) async fn query_detail_async(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request_async(self.order_path(), params, RequestMethod::Get)
            .await
    }
}

pub(super) enum RequestMethod {
    Get,
    Post,
    Delete,
}

fn apply_clock_offset(local_millis: u64, offset_millis: i64) -> Result<u64, ExchangeError> {
    if offset_millis >= 0 {
        Ok(local_millis.saturating_add(offset_millis as u64))
    } else {
        local_millis
            .checked_sub(offset_millis.unsigned_abs())
            .ok_or_else(|| ExchangeError::Preflight("invalid provider clock offset".into()))
    }
}

fn is_timestamp_rejection(error: &ExchangeError) -> bool {
    match error {
        ExchangeError::Http { body, .. } => {
            serde_json::from_str::<Value>(body)
                .ok()
                .and_then(|value| value.get("code").and_then(Value::as_i64))
                == Some(-1021)
        }
        _ => false,
    }
}

pub(crate) fn normalize_account(
    segment: &AccountSegment,
    account: &Value,
    positions: &Value,
    orders: &Value,
) -> Result<AccountSnapshot, String> {
    let assets = account
        .get("assets")
        .and_then(Value::as_array)
        .ok_or_else(|| "Binance futures account assets is missing".to_string())?;
    let balances = assets
        .iter()
        .map(|item| {
            let code = item
                .get("asset")
                .and_then(Value::as_str)
                .ok_or_else(|| "Binance futures asset is missing".to_string())?;
            let wallet = required_decimal_field(item, "walletBalance")?;
            let available = required_decimal_field(item, "availableBalance").ok();
            Ok(Balance {
                asset_id: kairos_primitives::AssetId::new(format!("asset:crypto:{code}"))?,
                asset_code: kairos_primitives::Currency::new(code)?,
                total: wallet,
                available,
                ..Default::default()
            })
        })
        .collect::<Result<Vec<_>, String>>()?;

    let rows = positions
        .as_array()
        .ok_or_else(|| "Binance futures positions is not an array".to_string())?;
    let positions = rows
        .iter()
        .filter_map(|item| {
            let symbol = item.get("symbol")?.as_str()?;
            let quantity = required_decimal_field(item, "positionAmt").ok()?;
            if quantity.mantissa == 0 {
                return None;
            }
            let provider_instrument = external_instrument_ref(
                crate::domain::ParticipantKind::Exchange,
                "binance",
                "binance-futures",
                symbol,
            )
            .ok()?;
            Some(Ok(Position {
                provider_instrument,
                position_side: item
                    .get("positionSide")
                    .and_then(Value::as_str)
                    .unwrap_or("BOTH")
                    .parse()
                    .ok()?,
                quantity,
                average_price: required_decimal_field(item, "entryPrice").ok(),
                mark_price: required_decimal_field(item, "markPrice").ok(),
                unrealized_pnl: required_decimal_field(item, "unRealizedProfit").ok(),
                ..Default::default()
            }))
        })
        .collect::<Result<Vec<_>, String>>()?;

    let open_orders = orders
        .as_array()
        .ok_or_else(|| "Binance futures open orders is not an array".to_string())?
        .iter()
        .map(|value| normalize_open_order(value, "binance-futures"))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(AccountSnapshot {
        segment_key: segment.segment_key.clone(),
        balances: balances.clone(),
        collateral: balances,
        positions,
        open_orders,
        status: AccountStatus::Ready,
        observed_at_unix_nanos: now_nanos().into(),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: Some(AccountModel::Contract),
        margin_mode: None,
        position_mode: None,
        partial: false,
    })
}

fn normalize_open_order(value: &Value, product: &str) -> Result<OpenOrder, String> {
    let remote_order_id = value
        .get("orderId")
        .map(value_as_string)
        .ok_or_else(|| "Binance futures open order id is missing".to_string())?;
    let symbol = value
        .get("symbol")
        .and_then(Value::as_str)
        .ok_or_else(|| "Binance futures open order symbol is missing".to_string())?;
    let provider_instrument = external_instrument_ref(
        crate::domain::ParticipantKind::Exchange,
        "binance",
        product,
        symbol,
    )?;
    let local_order_id = value
        .get("clientOrderId")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or(&remote_order_id);
    Ok(OpenOrder {
        order_id: kairos_primitives::OrderId::new(local_order_id)?,
        remote_order_id: Some(kairos_primitives::RemoteOrderId::new(remote_order_id)?),
        provider_instrument,
        side: crate::application::capabilities::execution_facts::normalize_order_side(
            value
                .get("side")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        ),
        quantity: required_decimal_field(value, "origQty")?,
        filled_quantity: required_decimal_field(value, "executedQty")?,
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

fn required_decimal_field(value: &Value, field: &str) -> Result<DecimalValue, String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("Binance futures field is missing: {field}"))
        .and_then(decimal)
}

fn decimal(value: &str) -> Result<DecimalValue, String> {
    DecimalValue::parse(value)
}

fn product_name(product: ConnectionDomain) -> &'static str {
    match product {
        ConnectionDomain::UsdMFutures => "usd-m-futures",
        ConnectionDomain::CoinMFutures => "coin-m-futures",
        _ => "futures",
    }
}

fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

/// Binance Futures user-data stream kept beside the REST account gateway so
/// both capabilities share the same signed listen-key client.
pub struct BinanceFuturesAccountStreamConnection {
    state: crate::domain::ConnectionState,
    client: BinanceFuturesAccountClient,
    endpoint: String,
    socket: Option<TokioSocket>,
    segment_key: String,
}

impl BinanceFuturesAccountStreamConnection {
    pub fn new(
        product: ConnectionDomain,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
        websocket_endpoint: impl Into<String>,
        segment_key: impl Into<String>,
    ) -> Result<Self, String> {
        if !matches!(
            product,
            ConnectionDomain::UsdMFutures | ConnectionDomain::CoinMFutures
        ) {
            return Err("Binance futures account stream requires USD-M or Coin-M".into());
        }
        let client = BinanceFuturesAccountClient::new(product, api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        let endpoint = websocket_endpoint.into().trim_end_matches('/').to_string();
        if !(endpoint.starts_with("wss://") || endpoint.starts_with("ws://")) {
            return Err(
                "Binance futures account stream endpoint must start with ws:// or wss://".into(),
            );
        }
        let segment_key = segment_key.into();
        if segment_key.trim().is_empty() {
            return Err("Binance futures account stream segment key is required".into());
        }
        Ok(Self {
            state: crate::domain::ConnectionState::new(super::super::descriptor(
                format!("account.binance.{}.private-stream", product_name(product)),
                product.as_str(),
            )?),
            client,
            endpoint,
            socket: None,
            segment_key,
        })
    }

    fn open(&mut self) -> Result<(), String> {
        let listen_key = self
            .client
            .listen_key()
            .map_err(|error| error.to_string())?;
        self.socket = Some(TokioSocket::connect(format!(
            "{}/ws/{listen_key}",
            self.endpoint
        ))?);
        Ok(())
    }
}

impl AccountEventStreamConnection for BinanceFuturesAccountStreamConnection {
    fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        if self.state.lifecycle == crate::domain::ConnectionLifecycle::Ready {
            return Ok(());
        }
        if let Err(error) = self.open() {
            self.state.mark_failed(error.clone());
            return Err(IntegrationError::Transport(error));
        }
        self.state.mark_ready(true);
        Ok(())
    }

    fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.socket.take();
        self.state.mark_stopped();
        Ok(())
    }

    fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.socket.take();
        match self.open() {
            Ok(()) => {
                self.state.mark_reconnected(true);
                Ok(())
            }
            Err(error) => {
                self.state.mark_failed(error.clone());
                Err(IntegrationError::Transport(error))
            }
        }
    }

    fn channel_health(&self) -> crate::domain::ConnectionHealth {
        self.state.health()
    }

    fn recv_account_event(
        &mut self,
        timeout: std::time::Duration,
    ) -> Result<AccountEventReceive, IntegrationError> {
        self.connect_channel()?;
        let event = self
            .socket
            .as_ref()
            .ok_or(IntegrationError::NotReady)?
            .recv_timeout(timeout)
            .map_err(IntegrationError::Transport)?;
        let Some(event) = event else {
            return Ok(AccountEventReceive::Idle);
        };
        let text = match event {
            SocketEvent::Message(tokio_tungstenite::tungstenite::Message::Text(text)) => text,
            SocketEvent::Message(tokio_tungstenite::tungstenite::Message::Ping(payload)) => {
                self.socket
                    .as_ref()
                    .ok_or(IntegrationError::NotReady)?
                    .send_pong(payload.to_vec())
                    .map_err(IntegrationError::Transport)?;
                return Ok(AccountEventReceive::Idle);
            }
            SocketEvent::Message(_) => return Ok(AccountEventReceive::Idle),
            SocketEvent::Error(error) => return Err(IntegrationError::Transport(error)),
            SocketEvent::Backpressure => {
                return Err(IntegrationError::Backpressure(
                    "Binance futures account event queue overflowed".into(),
                ))
            }
        };
        Ok(parse_user_event(&self.segment_key, &text)
            .map_err(IntegrationError::InvalidPayload)?
            .map(AccountEventReceive::Event)
            .unwrap_or(AccountEventReceive::Idle))
    }
}

pub(super) fn parse_user_event(
    segment_key: &str,
    text: &str,
) -> Result<Option<AccountEvent>, String> {
    let value: Value = serde_json::from_str(text).map_err(|error| error.to_string())?;
    match value.get("e").and_then(Value::as_str).unwrap_or_default() {
        "ORDER_TRADE_UPDATE" => {
            let row = value
                .get("o")
                .ok_or_else(|| "Binance futures order event is missing".to_string())?;
            let order_id = row
                .get("c")
                .and_then(Value::as_str)
                .ok_or_else(|| "Binance futures client order id is missing".to_string())?;
            let status = match row.get("X").and_then(Value::as_str).unwrap_or_default() {
                "NEW" => OrderStatus::Acknowledged,
                "PARTIALLY_FILLED" => OrderStatus::PartiallyFilled,
                "FILLED" => OrderStatus::Filled,
                "CANCELED" => OrderStatus::Canceled,
                "REJECTED" => OrderStatus::Rejected,
                "EXPIRED" | "EXPIRED_IN_MATCH" => OrderStatus::Expired,
                _ => OrderStatus::Unknown,
            };
            let occurred_at_unix_nanos =
                value.get("E").and_then(Value::as_u64).unwrap_or_default() * 1_000_000;
            let mut events = vec![AccountEvent::Order(OrderEvent {
                order_id: kairos_primitives::OrderId::new(order_id)?,
                status,
                remote_order_id: row
                    .get("i")
                    .map(value_string)
                    .map(kairos_primitives::RemoteOrderId::new)
                    .transpose()?,
                filled_quantity: row
                    .get("z")
                    .and_then(Value::as_str)
                    .map(decimal)
                    .transpose()?,
                occurred_at_unix_nanos: occurred_at_unix_nanos.into(),
                reason: row
                    .get("r")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .into(),
            })];
            if let Some(quantity) = row
                .get("l")
                .and_then(Value::as_str)
                .filter(|value| *value != "0" && !value.is_empty())
            {
                let symbol = row
                    .get("s")
                    .and_then(Value::as_str)
                    .ok_or_else(|| "Binance futures fill symbol is missing".to_string())?;
                let price = row
                    .get("L")
                    .and_then(Value::as_str)
                    .ok_or_else(|| "Binance futures fill price is missing".to_string())?;
                let provider_instrument = external_instrument_ref(
                    crate::domain::ParticipantKind::Exchange,
                    "binance",
                    "binance-futures",
                    symbol,
                )?;
                events.push(AccountEvent::Fill(FillEvent {
                    fill_id: kairos_primitives::FillId::new(
                        row.get("t")
                            .map(value_string)
                            .filter(|value| value != "-1")
                            .unwrap_or_else(|| format!("{order_id}:{occurred_at_unix_nanos}")),
                    )?,
                    order_id: kairos_primitives::OrderId::new(order_id)?,
                    segment_key: kairos_primitives::SegmentKey::new(segment_key)?,
                    provider_instrument,
                    side: row.get("S").and_then(Value::as_str).unwrap_or("BUY").into(),
                    quantity: decimal(quantity)?,
                    price: decimal(price)?,
                    fee_asset: row
                        .get("N")
                        .and_then(Value::as_str)
                        .map(kairos_primitives::Currency::new)
                        .transpose()?,
                    fee_amount: row
                        .get("n")
                        .and_then(Value::as_str)
                        .filter(|value| *value != "0" && !value.is_empty())
                        .map(decimal)
                        .transpose()?,
                    occurred_at_unix_nanos: occurred_at_unix_nanos.into(),
                }));
            }
            Ok(Some(AccountEvent::Batch(events)))
        }
        "ACCOUNT_UPDATE" => {
            let data = value
                .get("a")
                .ok_or_else(|| "Binance futures account event is missing".to_string())?;
            let balances: Vec<Balance> = data
                .get("B")
                .and_then(Value::as_array)
                .map_or(&[][..], Vec::as_slice)
                .iter()
                .filter_map(|row| {
                    let code = row.get("a").and_then(Value::as_str)?;
                    let total =
                        decimal(row.get("wb").and_then(Value::as_str).unwrap_or("0")).ok()?;
                    Some(Balance {
                        asset_id: kairos_primitives::AssetId::new(format!("asset:crypto:{code}"))
                            .ok()?,
                        asset_code: kairos_primitives::Currency::new(code).ok()?,
                        total,
                        available: stream_decimal_field(row, "cw"),
                        ..Default::default()
                    })
                })
                .collect();
            let positions = data
                .get("P")
                .and_then(Value::as_array)
                .map_or(&[][..], Vec::as_slice)
                .iter()
                .filter_map(|row| {
                    let symbol = row.get("s").and_then(Value::as_str)?;
                    let quantity =
                        decimal(row.get("pa").and_then(Value::as_str).unwrap_or("0")).ok()?;
                    let provider_instrument = external_instrument_ref(
                        crate::domain::ParticipantKind::Exchange,
                        "binance",
                        "binance-futures",
                        symbol,
                    )
                    .ok()?;
                    Some(Position {
                        provider_instrument,
                        position_side: row
                            .get("ps")
                            .and_then(Value::as_str)
                            .unwrap_or("BOTH")
                            .parse()
                            .ok()?,
                        quantity,
                        average_price: stream_decimal_field(row, "ep"),
                        unrealized_pnl: stream_decimal_field(row, "up"),
                        ..Default::default()
                    })
                })
                .collect();
            Ok(Some(AccountEvent::Snapshot(AccountSnapshot {
                segment_key: kairos_primitives::SegmentKey::new(segment_key)?,
                balances: balances.clone(),
                collateral: balances,
                positions,
                open_orders: Vec::new(),
                status: AccountStatus::Ready,
                observed_at_unix_nanos: (value
                    .get("E")
                    .and_then(Value::as_u64)
                    .unwrap_or_default()
                    * 1_000_000)
                    .into(),
                equity: None,
                initial_equity: None,
                net_profit: None,
                account_model: None,
                margin_mode: None,
                position_mode: None,
                partial: true,
            })))
        }
        _ => Ok(None),
    }
}

fn value_string(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}
fn stream_decimal_field(value: &Value, field: &str) -> Option<DecimalValue> {
    value
        .get(field)
        .and_then(Value::as_str)
        .and_then(|value| decimal(value).ok())
}

#[cfg(test)]
mod tests {
    use super::normalize_account;
    use crate::application::capabilities::account_facts::{
        ExternalAccountIdentity, ExternalAccountSegment as AccountSegment,
    };

    #[test]
    fn normalizes_futures_assets_and_non_zero_positions() {
        let segment = AccountSegment {
            identity: ExternalAccountIdentity::new("binance", "main").unwrap(),
            segment_key: kairos_primitives::SegmentKey::new("usd_m_futures").unwrap(),
            environment: "live".into(),
            account_model: Some("contract".into()),
        };
        let account = serde_json::json!({"assets":[{"asset":"USDT","walletBalance":"100.5","availableBalance":"90.25"}]});
        let positions = serde_json::json!([
            {"symbol":"BTCUSDT","positionSide":"LONG","positionAmt":"0.25","entryPrice":"60000","markPrice":"61000","unRealizedProfit":"250"},
            {"symbol":"BTCUSDT","positionSide":"SHORT","positionAmt":"-0.10","entryPrice":"62000","markPrice":"61000","unRealizedProfit":"100"},
            {"symbol":"ETHUSDT","positionSide":"BOTH","positionAmt":"0"}
        ]);
        let result = normalize_account(
            &segment,
            &account,
            &positions,
            &serde_json::json!([{"orderId":8,"clientOrderId":"future-8","symbol":"BTCUSDT","side":"SELL","origQty":"2","executedQty":"0","status":"NEW"}]),
        )
        .unwrap();
        assert_eq!(result.balances[0].asset_code, "USDT");
        assert_eq!(result.positions.len(), 2);
        assert_eq!(result.positions[0].quantity.mantissa, 25);
        assert_eq!(
            result.positions[0].position_side,
            kairos_primitives::PositionSide::Long
        );
        assert_eq!(
            result.positions[1].position_side,
            kairos_primitives::PositionSide::Short
        );
        assert_eq!(result.open_orders[0].order_id, "future-8");
    }
}
