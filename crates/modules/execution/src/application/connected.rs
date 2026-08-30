//! Execution connected/runtime application facade.
//!
//! Connected CLI entry points use this facade to query the running Execution
//! current or call typed Execution runtime control. Standalone order CLI
//! commands must use `CliExecutionApplication`.

use kairos_execution_contract::{
    CancelOrderRequest, ExecutionClient, ExecutionCommandStatus, ExecutionControlRpcClient,
    ExecutionOrderAuditQuery, ExecutionOrderAuditResponse, ExecutionReconcileResponse,
    ExecutionRoutesQuery, ExecutionRoutesResponse, ReconcileExecutionRequest, ReplaceOrderRequest,
    SubmitIntentRequest,
};
use kairos_primitives::execution::OrderId;
use kairos_primitives::runtime::InstanceIdentity;
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct ExecutionOrdersResult {
    pub orders: Vec<ExecutionOrderResult>,
}

#[derive(Debug, Serialize)]
pub struct UnknownRemoteOrdersResult {
    pub orders: Vec<UnknownRemoteOrderResult>,
}

#[derive(Debug, Serialize)]
pub struct ExecutionOrderResult {
    pub order_id: String,
    pub intent_id: String,
    pub plan_id: String,
    pub leg_id: String,
    pub strategy_id: String,
    pub account_id: String,
    pub segment_key: String,
    pub instrument_id: String,
    pub market_id: String,
    pub execution_route_id: String,
    pub remote_order_id: Option<String>,
    pub side: String,
    pub order_type: String,
    pub quantity: String,
    pub filled_quantity: String,
    pub limit_price: Option<String>,
    pub status: String,
    pub terminal: bool,
    pub submitted_at_unix_nanos: Option<u64>,
    pub updated_at_unix_nanos: u64,
    pub reason: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct UnknownRemoteOrderResult {
    pub remote_order_id: String,
    pub symbol: String,
    pub status: String,
    pub execution_id: Option<String>,
    pub fill_quantity: Option<String>,
    pub fill_price: Option<String>,
    pub fee_currency: Option<String>,
    pub fee_amount: Option<String>,
    pub first_seen_at_unix_nanos: u64,
    pub last_seen_at_unix_nanos: u64,
    pub resolution: String,
    pub reason: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum ConnectedExecutionOutput {
    Orders(ExecutionOrdersResult),
    UnknownRemoteOrders(UnknownRemoteOrdersResult),
    Order(ExecutionOrderResult),
    Audit(ExecutionOrderAuditResponse),
    Routes(ExecutionRoutesResponse),
    Reconcile(ExecutionReconcileResponse),
    Command(ExecutionCommandStatus),
}

pub struct ConnectedExecutionApplication {
    client: ExecutionClient,
    identity: InstanceIdentity,
}

impl ConnectedExecutionApplication {
    pub(crate) const fn new(client: ExecutionClient, identity: InstanceIdentity) -> Self {
        Self { client, identity }
    }

    pub fn active_orders(
        &self,
        account_id: Option<&str>,
    ) -> Result<ExecutionOrdersResult, Box<dyn std::error::Error>> {
        let current = self.read_orders()?;
        Ok(ExecutionOrdersResult {
            orders: filter_orders(current, account_id),
        })
    }

    pub fn unknown_remote_orders(
        &self,
    ) -> Result<UnknownRemoteOrdersResult, Box<dyn std::error::Error>> {
        let current = self.client.indexed_current(&self.identity)?;
        current.ensure_ready()?;
        Ok(UnknownRemoteOrdersResult {
            orders: current
                .unknown_remote_orders()?
                .iter()
                .map(|value| {
                    value
                        .unknown_remote_order()
                        .map(|root| unknown_remote_result(root.state()))
                })
                .collect::<Result<Vec<_>, _>>()?,
        })
    }

    pub fn active_order(
        &self,
        order_id: &str,
    ) -> Result<ExecutionOrderResult, Box<dyn std::error::Error>> {
        let current = self.client.indexed_current(&self.identity)?;
        current
            .with_order(order_id, |value| {
                value.map(|root| order_result(root.state())).ok_or_else(|| {
                    kairos_execution_contract::ContractError::Invalid(format!(
                        "unknown order: {order_id}"
                    ))
                })
            })
            .map_err(Into::into)
    }

    pub async fn routes(
        &self,
        query: ExecutionRoutesQuery,
    ) -> Result<ExecutionRoutesResponse, Box<dyn std::error::Error>> {
        let response: ExecutionRoutesResponse =
            ExecutionControlRpcClient::routes(&self.client.control(), query).await?;
        Ok(response)
    }

    pub async fn order_audit(
        &self,
        query: ExecutionOrderAuditQuery,
    ) -> Result<ExecutionOrderAuditResponse, Box<dyn std::error::Error>> {
        let response =
            ExecutionControlRpcClient::order_audit(&self.client.control(), query).await?;
        Ok(response)
    }

    pub async fn reconcile(
        &self,
        request: ReconcileExecutionRequest,
    ) -> Result<ExecutionReconcileResponse, Box<dyn std::error::Error>> {
        let response: ExecutionReconcileResponse =
            ExecutionControlRpcClient::reconcile(&self.client.control(), request).await?;
        Ok(response)
    }

    pub async fn submit_intent(
        &self,
        request: SubmitIntentRequest,
    ) -> Result<ExecutionCommandStatus, Box<dyn std::error::Error>> {
        let response: ExecutionCommandStatus =
            ExecutionControlRpcClient::submit_intent(&self.client.control(), request).await?;
        Ok(response)
    }

    pub async fn cancel_order(
        &self,
        order_id: OrderId,
        request: CancelOrderRequest,
    ) -> Result<ExecutionCommandStatus, Box<dyn std::error::Error>> {
        let response: ExecutionCommandStatus =
            ExecutionControlRpcClient::cancel_order(&self.client.control(), order_id, request)
                .await?;
        Ok(response)
    }

    pub async fn replace_order(
        &self,
        order_id: OrderId,
        request: ReplaceOrderRequest,
    ) -> Result<ExecutionCommandStatus, Box<dyn std::error::Error>> {
        let response: ExecutionCommandStatus =
            ExecutionControlRpcClient::replace_order(&self.client.control(), order_id, request)
                .await?;
        Ok(response)
    }

    fn read_orders(&self) -> Result<Vec<ExecutionOrderResult>, Box<dyn std::error::Error>> {
        let current = self.client.indexed_current(&self.identity)?;
        current.ensure_ready()?;
        Ok(current
            .orders()?
            .iter()
            .map(|value| value.order().map(|root| order_result(root.state())))
            .collect::<Result<Vec<_>, _>>()?)
    }
}

fn order_result(
    value: kairos_protocol::generated::kairos::execution::v_2::OrderState<'_>,
) -> ExecutionOrderResult {
    use kairos_protocol::generated::kairos::execution::v_2 as fb;
    ExecutionOrderResult {
        order_id: value.order_id().to_owned(),
        intent_id: value.intent_id().to_owned(),
        plan_id: value.plan_id().to_owned(),
        leg_id: value.leg_id().to_owned(),
        strategy_id: value.strategy_id().to_owned(),
        account_id: value.account_id().to_owned(),
        segment_key: value.segment_key().to_owned(),
        instrument_id: value.instrument_id().to_owned(),
        market_id: value.market_id().to_owned(),
        execution_route_id: value.execution_route_id().to_owned(),
        remote_order_id: value.remote_order_id().map(str::to_owned),
        side: enum_name(value.side().variant_name()),
        order_type: enum_name(value.order_type().variant_name()),
        quantity: decimal_string(value.quantity()),
        filled_quantity: decimal_string(value.filled_quantity()),
        limit_price: value.limit_price().map(decimal_string),
        status: enum_name(value.lifecycle().variant_name()),
        terminal: matches!(
            value.lifecycle(),
            fb::OrderLifecycle::FILLED
                | fb::OrderLifecycle::CANCELED
                | fb::OrderLifecycle::REJECTED
                | fb::OrderLifecycle::EXPIRED
                | fb::OrderLifecycle::FAILED
        ),
        submitted_at_unix_nanos: value.submitted_at_unix_nanos(),
        updated_at_unix_nanos: value.updated_at_unix_nanos(),
        reason: value.reason().map(str::to_owned),
    }
}

fn unknown_remote_result(
    value: kairos_protocol::generated::kairos::execution::v_2::UnknownRemoteOrderState<'_>,
) -> UnknownRemoteOrderResult {
    UnknownRemoteOrderResult {
        remote_order_id: value.remote_order_id().to_owned(),
        symbol: value.symbol().to_owned(),
        status: enum_name(value.lifecycle().variant_name()),
        execution_id: value.execution_id().map(str::to_owned),
        fill_quantity: value.fill_quantity().map(decimal_string),
        fill_price: value.fill_price().map(decimal_string),
        fee_currency: value.fee_currency().map(str::to_owned),
        fee_amount: value.fee_amount().map(decimal_string),
        first_seen_at_unix_nanos: value.first_seen_at_unix_nanos(),
        last_seen_at_unix_nanos: value.last_seen_at_unix_nanos(),
        resolution: value.resolution().to_owned(),
        reason: value.reason().map(str::to_owned),
    }
}

fn decimal_string(value: &kairos_protocol::generated::kairos::common::v_2::Decimal64) -> String {
    let scale = value.scale() as usize;
    let negative = value.mantissa() < 0;
    let digits = i128::from(value.mantissa()).abs().to_string();
    if scale == 0 {
        return format!("{}{digits}", if negative { "-" } else { "" });
    }
    let padded = format!("{:0>width$}", digits, width = scale + 1);
    let split = padded.len() - scale;
    format!(
        "{}{}.{}",
        if negative { "-" } else { "" },
        &padded[..split],
        &padded[split..]
    )
}

fn enum_name(value: Option<&str>) -> String {
    value.unwrap_or("UNSPECIFIED").to_ascii_lowercase()
}

fn filter_orders(
    values: Vec<ExecutionOrderResult>,
    account_id: Option<&str>,
) -> Vec<ExecutionOrderResult> {
    values
        .into_iter()
        .filter(|value| account_id.is_none_or(|expected| value.account_id == expected))
        .collect()
}
