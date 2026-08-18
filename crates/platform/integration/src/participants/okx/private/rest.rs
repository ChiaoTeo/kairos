use crate::participants::okx::{OkxCredential, OkxPrivateRestConfig};
use crate::services::participants::okx::rest::RestService;
use crate::services::participants::okx::{
    cancel_request_body, normalize_account, normalize_credential_profile, normalize_market_profile,
    normalize_okx_order, normalize_okx_orders, normalize_order_cancellation,
    normalize_order_submission, order_request_body,
};
use crate::{
    AccountCredentialQuery, AccountMarketProfileQuery, AccountQuery, CommandOutcome, CommandResult,
    ConnectionDescriptor, ConnectionKey, ExternalAccountCredentialProfile, ExternalAccountSegment,
    ExternalAccountSnapshot, ExternalMarketProfile, ExternalMarketProfileRequest, ExternalOrder,
    ExternalOrderQuery, IndeterminateCommand, IntegrationError, OrderCommand, OrderEntryEvent,
    OrderEntryRequest, OrderQuery,
};

use super::order::{
    amend_body, batch_acks, cancel_body, one_ack, validate_batch, OkxAmendOrderRequest,
    OkxOrderIdentity, OkxOrderOperationAck,
};
use super::{history, OkxBillRecord, OkxFillRecord, OkxHistoryQuery};

pub struct OkxPrivateRestConnection {
    service: RestService,
    credential: OkxCredential,
}

impl OkxPrivateRestConnection {
    pub fn new(
        connection_key: ConnectionKey,
        config: OkxPrivateRestConfig,
    ) -> Result<Self, IntegrationError> {
        let principal_id = config.credential.principal_id.clone();
        Ok(Self {
            service: RestService::new(
                connection_key,
                config.connection,
                "private.rest",
                Some(principal_id),
            )?,
            credential: config.credential,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        self.service.descriptor()
    }

    pub fn endpoint(&self) -> &str {
        self.service.endpoint()
    }

    pub fn rate_limit_headers(&self) -> std::collections::BTreeMap<String, String> {
        self.service.rate_limit_headers()
    }

    pub fn clock_health(&self) -> Result<crate::ProviderClockHealth, IntegrationError> {
        self.service.clock_health()
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

    pub async fn amend_order(
        &self,
        request: &OkxAmendOrderRequest,
    ) -> CommandResult<OkxOrderOperationAck> {
        let body = amend_body(request)?;
        match self.post("/api/v5/trade/amend-order", &body).await? {
            CommandOutcome::Confirmed(payload) => one_ack(&payload, "amend order"),
            CommandOutcome::Rejected(error) => Ok(CommandOutcome::Rejected(error)),
            CommandOutcome::Indeterminate(error) => Ok(CommandOutcome::Indeterminate(error)),
        }
    }

    pub async fn amend_orders(
        &self,
        requests: &[OkxAmendOrderRequest],
    ) -> CommandResult<Vec<CommandOutcome<OkxOrderOperationAck>>> {
        validate_batch(requests.len(), "amend")?;
        let body = serde_json::Value::Array(
            requests
                .iter()
                .map(amend_body)
                .collect::<Result<Vec<_>, _>>()?,
        );
        match self.post("/api/v5/trade/amend-batch-orders", &body).await? {
            CommandOutcome::Confirmed(payload) => Ok(CommandOutcome::Confirmed(batch_acks(
                &payload,
                requests.len(),
                "batch amend",
            )?)),
            CommandOutcome::Rejected(error) => Ok(CommandOutcome::Rejected(error)),
            CommandOutcome::Indeterminate(error) => Ok(CommandOutcome::Indeterminate(error)),
        }
    }

    pub async fn cancel_orders(
        &self,
        requests: &[OkxOrderIdentity],
    ) -> CommandResult<Vec<CommandOutcome<OkxOrderOperationAck>>> {
        validate_batch(requests.len(), "cancel")?;
        let body = serde_json::Value::Array(
            requests
                .iter()
                .map(cancel_body)
                .collect::<Result<Vec<_>, _>>()?,
        );
        match self
            .post("/api/v5/trade/cancel-batch-orders", &body)
            .await?
        {
            CommandOutcome::Confirmed(payload) => Ok(CommandOutcome::Confirmed(batch_acks(
                &payload,
                requests.len(),
                "batch cancel",
            )?)),
            CommandOutcome::Rejected(error) => Ok(CommandOutcome::Rejected(error)),
            CommandOutcome::Indeterminate(error) => Ok(CommandOutcome::Indeterminate(error)),
        }
    }

    pub async fn submit_orders(
        &self,
        requests: &[OrderEntryRequest],
    ) -> CommandResult<Vec<CommandOutcome<OrderEntryEvent>>> {
        validate_batch(requests.len(), "submit")?;
        let body = serde_json::Value::Array(
            requests
                .iter()
                .map(|request| {
                    let instrument_type = instrument_type_from_order(request);
                    order_request_body(request, trading_mode(request, &instrument_type))
                })
                .collect::<Result<Vec<_>, _>>()?,
        );
        match self.post("/api/v5/trade/batch-orders", &body).await? {
            CommandOutcome::Confirmed(payload) => {
                let rows = payload.get("data").and_then(serde_json::Value::as_array);
                let outcomes = requests
                    .iter()
                    .enumerate()
                    .map(|(index, request)| {
                        let Some(row) = rows.and_then(|rows| rows.get(index)) else {
                            return Ok(CommandOutcome::Indeterminate(
                                IndeterminateCommand::may_have_been_sent(format!(
                                    "OKX batch submit response item {index} is missing"
                                )),
                            ));
                        };
                        normalize_order_submission(
                            request,
                            &serde_json::json!({"data":[row.clone()]}),
                        )
                    })
                    .collect::<Result<Vec<_>, IntegrationError>>()?;
                Ok(CommandOutcome::Confirmed(outcomes))
            }
            CommandOutcome::Rejected(error) => Ok(CommandOutcome::Rejected(error)),
            CommandOutcome::Indeterminate(error) => Ok(CommandOutcome::Indeterminate(error)),
        }
    }

    pub async fn fetch_fills(
        &self,
        query: &OkxHistoryQuery,
    ) -> Result<Vec<OkxFillRecord>, IntegrationError> {
        let payload = self
            .get("/api/v5/trade/fills-history", &query.params()?)
            .await?;
        history::fills(&payload)
    }

    pub async fn fetch_bills(
        &self,
        query: &OkxHistoryQuery,
    ) -> Result<Vec<OkxBillRecord>, IntegrationError> {
        let payload = self
            .get("/api/v5/account/bills-archive", &query.params()?)
            .await?;
        history::bills(&payload)
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
        normalize_okx_orders(&self.descriptor().connection_key, &payload)
            .map_err(IntegrationError::InvalidPayload)
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let params = order_query_params(query, false)?;
        let payload = self.get("/api/v5/trade/orders-history", &params).await?;
        normalize_okx_orders(&self.descriptor().connection_key, &payload)
            .map_err(IntegrationError::InvalidPayload)
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
        normalize_okx_order(&self.descriptor().connection_key, row)
            .map(Some)
            .map_err(IntegrationError::InvalidPayload)
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
