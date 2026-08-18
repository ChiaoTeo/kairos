use crate::participants::okx::{OkxCredential, OkxPrivateRestConfig};
use crate::services::participants::okx::rest::RestService;
use crate::services::participants::okx::{
    cancel_request_body, normalize_account, normalize_credential_profile, normalize_market_profile,
    normalize_okx_order, normalize_okx_orders, normalize_order_cancellation,
    normalize_order_submission, order_request_body,
};
use crate::{
    AccountCredentialQuery, AccountMarketProfileQuery, AccountQuery, CommandResult,
    ConnectionDescriptor, ExternalAccountCredentialProfile, ExternalAccountSegment,
    ExternalAccountSnapshot, ExternalMarketProfile, ExternalMarketProfileRequest, ExternalOrder,
    ExternalOrderQuery, IntegrationError, OrderCommand, OrderEntryEvent, OrderEntryRequest,
    OrderQuery,
};

pub struct OkxPrivateRestConnection {
    service: RestService,
    credential: OkxCredential,
}

impl OkxPrivateRestConnection {
    pub fn new(config: OkxPrivateRestConfig) -> Result<Self, IntegrationError> {
        let principal_id = config.credential.principal_id.clone();
        Ok(Self {
            service: RestService::new(config.connection, "private.rest", Some(principal_id))?,
            credential: config.credential,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        self.service.descriptor()
    }

    pub fn endpoint(&self) -> &str {
        self.service.endpoint()
    }

    async fn get(
        &self,
        path: &str,
        query: &[(&str, String)],
    ) -> Result<serde_json::Value, IntegrationError> {
        self.service
            .private_get(&self.credential, path, query)
            .await
    }

    async fn post(&self, path: &str, body: &serde_json::Value) -> CommandResult<serde_json::Value> {
        self.service
            .private_post_command(&self.credential, path, body)
            .await
    }
}

impl AccountQuery for OkxPrivateRestConnection {
    async fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, IntegrationError> {
        let instrument_type = instrument_type_from_segment(segment);
        let query = [("instType", instrument_type.into())];
        let balance = self.get("/api/v5/account/balance", &[]).await?;
        let positions = self.get("/api/v5/account/positions", &query).await?;
        let orders = self.get("/api/v5/trade/orders-pending", &query).await?;
        normalize_account(segment, &balance, &positions, &orders)
            .map_err(IntegrationError::InvalidPayload)
    }
}

impl AccountMarketProfileQuery for OkxPrivateRestConnection {
    async fn fetch_market_profile(
        &mut self,
        request: &ExternalMarketProfileRequest,
    ) -> Result<ExternalMarketProfile, IntegrationError> {
        let fee = self
            .get(
                "/api/v5/account/trade-fee",
                &[
                    ("instType", "SPOT".into()),
                    ("instId", request.source_symbol.to_string()),
                ],
            )
            .await?;
        let config = self.get("/api/v5/account/config", &[]).await?;
        normalize_market_profile(request, &fee, &config).map_err(IntegrationError::InvalidPayload)
    }
}

impl AccountCredentialQuery for OkxPrivateRestConnection {
    async fn inspect_credential(
        &mut self,
    ) -> Result<ExternalAccountCredentialProfile, IntegrationError> {
        let payload = self.get("/api/v5/account/config", &[]).await?;
        normalize_credential_profile(&payload, "unified").map_err(IntegrationError::InvalidPayload)
    }
}

impl OrderCommand for OkxPrivateRestConnection {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        let instrument_type = instrument_type_from_order(request);
        let body = order_request_body(request, trading_mode(request, &instrument_type))?;
        match self.post("/api/v5/trade/order", &body).await? {
            crate::CommandOutcome::Confirmed(payload) => {
                normalize_order_submission(request, &payload)
            }
            crate::CommandOutcome::Rejected(error) => Ok(crate::CommandOutcome::Rejected(error)),
            crate::CommandOutcome::Indeterminate(error) => {
                Ok(crate::CommandOutcome::Indeterminate(error))
            }
        }
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        let body = cancel_request_body(request, remote_order_id)?;
        match self.post("/api/v5/trade/cancel-order", &body).await? {
            crate::CommandOutcome::Confirmed(payload) => {
                normalize_order_cancellation(request, remote_order_id, at_unix_nanos, &payload)
            }
            crate::CommandOutcome::Rejected(error) => Ok(crate::CommandOutcome::Rejected(error)),
            crate::CommandOutcome::Indeterminate(error) => {
                Ok(crate::CommandOutcome::Indeterminate(error))
            }
        }
    }
}

impl OrderQuery for OkxPrivateRestConnection {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let params = order_query_params(query, false)?;
        let payload = self.get("/api/v5/trade/orders-pending", &params).await?;
        bind_orders(self.descriptor(), normalize_okx_orders(&payload))
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let params = order_query_params(query, false)?;
        let payload = self.get("/api/v5/trade/orders-history", &params).await?;
        bind_orders(self.descriptor(), normalize_okx_orders(&payload))
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        let params = order_query_params(query, true)?;
        let payload = self.get("/api/v5/trade/order", &params).await?;
        let Some(row) = payload
            .get("data")
            .and_then(serde_json::Value::as_array)
            .and_then(|values| values.first())
        else {
            return Ok(None);
        };
        let mut order = normalize_okx_order(row).map_err(IntegrationError::InvalidPayload)?;
        order.binding_id = self.descriptor().binding_id.clone();
        Ok(Some(order))
    }
}

fn order_query_params(
    query: &ExternalOrderQuery,
    detail: bool,
) -> Result<Vec<(&'static str, String)>, IntegrationError> {
    let instrument_type = query
        .instrument_type
        .as_ref()
        .map(|value| value.as_str().to_ascii_uppercase())
        .unwrap_or_else(|| "SPOT".into());
    let mut values = vec![("instType", instrument_type)];
    if let Some(symbol) = &query.symbol {
        values.push(("instId", symbol.to_ascii_uppercase()));
    }
    if detail {
        let order_id = query.order_id.as_ref().ok_or_else(|| {
            IntegrationError::InvalidRequest("OKX order detail requires order id".into())
        })?;
        values.push(("ordId", order_id.to_string()));
    }
    if let Some(limit) = query.limit {
        values.push(("limit", limit.to_string()));
    }
    Ok(values)
}

fn instrument_type_from_order(request: &OrderEntryRequest) -> String {
    request
        .participant_instrument
        .instrument_type
        .as_ref()
        .map(|value| value.as_str().to_ascii_uppercase())
        .unwrap_or_else(|| "SPOT".into())
}

fn instrument_type_from_segment(segment: &ExternalAccountSegment) -> &'static str {
    match segment
        .account_model
        .as_deref()
        .and_then(crate::ExternalAccountModel::parse)
    {
        Some(crate::ExternalAccountModel::Contract)
        | Some(crate::ExternalAccountModel::ContractUnified) => "SWAP",
        Some(crate::ExternalAccountModel::Margin)
        | Some(crate::ExternalAccountModel::Unified)
        | Some(crate::ExternalAccountModel::PortfolioMargin) => "MARGIN",
        _ => "SPOT",
    }
}

fn trading_mode<'a>(request: &'a OrderEntryRequest, instrument_type: &str) -> &'a str {
    request
        .options
        .wallet_type
        .as_deref()
        .unwrap_or(if instrument_type == "SPOT" {
            "cash"
        } else {
            "cross"
        })
}

fn bind_orders(
    descriptor: &ConnectionDescriptor,
    orders: Result<Vec<ExternalOrder>, String>,
) -> Result<Vec<ExternalOrder>, IntegrationError> {
    let binding_id = descriptor.binding_id.clone();
    orders
        .map(|orders| {
            orders
                .into_iter()
                .map(|mut order| {
                    order.binding_id = binding_id.clone();
                    order
                })
                .collect()
        })
        .map_err(IntegrationError::InvalidPayload)
}
