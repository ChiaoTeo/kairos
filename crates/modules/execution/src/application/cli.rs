//! Standalone, short-lived Execution use cases.

use kairos_primitives::account::{AccountId, SegmentKey};
use serde::{Deserialize, Serialize};

use crate::application::SubmitOrder;
use crate::services::direct::{
    DirectCommandOutcome, DirectExecutionGateway, DirectFill, DirectOrder,
};

#[derive(Clone, Debug, Serialize)]
pub struct CliExecutionContext {
    pub owner: &'static str,
    pub mode: &'static str,
    pub scope: &'static str,
    pub source: &'static str,
    pub account_id: String,
    pub provider: String,
    pub environment: String,
    pub segment: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct CliExecutionOrder {
    pub order_id: String,
    pub remote_order_id: String,
    pub client_order_id: Option<String>,
    pub symbol: String,
    pub side: String,
    pub order_type: String,
    pub status: String,
    pub quantity: String,
    pub filled_quantity: String,
    pub average_fill_price: Option<String>,
    pub occurred_at_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug, Serialize)]
pub struct CliExecutionFill {
    pub fill_id: String,
    pub remote_order_id: String,
    pub symbol: String,
    pub side: String,
    pub price: String,
    pub quantity: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub realized_pnl: Option<String>,
    pub fee: Option<String>,
    pub fee_currency: Option<String>,
    pub executed_at_unix_nanos: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct CliExecutionOutcome {
    pub status: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub order_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub remote_order_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub order_status: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub filled_quantity: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub occurred_at_unix_nanos: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub code: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub participant_request_id: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct CliExecutionOrdersResult {
    #[serde(flatten)]
    pub context: CliExecutionContext,
    pub command: String,
    pub orders: Vec<CliExecutionOrder>,
}

#[derive(Clone, Debug, Serialize)]
pub struct CliExecutionOrderResult {
    #[serde(flatten)]
    pub context: CliExecutionContext,
    pub order: CliExecutionOrder,
}

#[derive(Clone, Debug, Serialize)]
pub struct CliExecutionFillsResult {
    #[serde(flatten)]
    pub context: CliExecutionContext,
    pub fills: Vec<CliExecutionFill>,
}

#[derive(Clone, Debug, Serialize)]
pub struct CliExecutionCommandResult {
    #[serde(flatten)]
    pub context: CliExecutionContext,
    pub command: String,
    pub outcome: CliExecutionOutcome,
}

#[derive(Clone, Debug, Serialize)]
pub struct CliExecutionReplaceResult {
    #[serde(flatten)]
    pub context: CliExecutionContext,
    pub command: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<&'static str>,
    pub cancel: CliExecutionOutcome,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub submit: Option<CliExecutionOutcome>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(untagged)]
pub enum CliExecutionOutput {
    Orders(CliExecutionOrdersResult),
    Order(CliExecutionOrderResult),
    Fills(CliExecutionFillsResult),
    Command(CliExecutionCommandResult),
    Replace(CliExecutionReplaceResult),
}

/// Account-owned facts required to establish one short-lived provider session.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct StandaloneExecutionBinding {
    pub account_id: String,
    pub remote_account_id: String,
    pub provider: String,
    pub environment: String,
    pub segment_key: String,
    pub execution_channel: String,
    pub trading_mode: Option<String>,
    pub credential_id: Option<String>,
    pub credential_role: String,
    pub base_url: String,
    pub host: String,
    pub port: u16,
    pub client_id: i32,
    pub isolated_symbol: Option<String>,
}

/// Execution-owned facade for direct exchange/broker operations.
pub struct CliExecutionApplication {
    binding: StandaloneExecutionBinding,
    gateway: DirectExecutionGateway,
}

impl CliExecutionApplication {
    pub(crate) fn new(
        binding: StandaloneExecutionBinding,
        gateway: DirectExecutionGateway,
    ) -> Result<Self, String> {
        AccountId::new(binding.account_id.clone()).map_err(|error| error.to_string())?;
        SegmentKey::new(binding.segment_key.clone()).map_err(|error| error.to_string())?;
        Ok(Self { binding, gateway })
    }

    pub async fn open_orders(
        &mut self,
        symbol: Option<&str>,
        limit: Option<u32>,
    ) -> Result<CliExecutionOutput, String> {
        let orders = self
            .gateway
            .open_orders(symbol, limit)
            .await
            .map_err(display)?;
        Ok(CliExecutionOutput::Orders(
            self.orders_result("open-orders", orders),
        ))
    }

    pub async fn history(
        &mut self,
        symbol: Option<&str>,
        limit: Option<u32>,
    ) -> Result<CliExecutionOutput, String> {
        let orders = self.gateway.history(symbol, limit).await.map_err(display)?;
        Ok(CliExecutionOutput::Orders(
            self.orders_result("history", orders),
        ))
    }

    pub async fn order(
        &mut self,
        order_id: &str,
        symbol: Option<&str>,
    ) -> Result<CliExecutionOutput, String> {
        let order = self
            .gateway
            .order(order_id, symbol)
            .await
            .map_err(display)?;
        Ok(CliExecutionOutput::Order(CliExecutionOrderResult {
            context: execution_context(&self.binding),
            order: order_result(&order),
        }))
    }

    pub async fn fills(
        &mut self,
        symbol: Option<&str>,
        order_id: Option<&str>,
        limit: Option<u16>,
    ) -> Result<CliExecutionOutput, String> {
        let fills = self
            .gateway
            .fills(symbol, order_id, limit)
            .await
            .map_err(display)?;
        Ok(CliExecutionOutput::Fills(CliExecutionFillsResult {
            context: execution_context(&self.binding),
            fills: fills.into_iter().map(fill_result).collect(),
        }))
    }

    pub async fn submit(
        &mut self,
        request: SubmitOrder,
        symbol: Option<&str>,
    ) -> Result<CliExecutionOutput, String> {
        self.assert_account(&request)?;
        let outcome = self
            .gateway
            .submit(&request, symbol)
            .await
            .map_err(display)?;
        Ok(CliExecutionOutput::Command(command_result(
            &self.binding,
            "submit",
            outcome,
        )))
    }

    pub async fn cancel(
        &mut self,
        order_id: &str,
        symbol: Option<&str>,
    ) -> Result<CliExecutionOutput, String> {
        let outcome = self
            .gateway
            .cancel(order_id, symbol)
            .await
            .map_err(display)?;
        Ok(CliExecutionOutput::Command(command_result(
            &self.binding,
            "cancel",
            outcome,
        )))
    }

    /// Portable replace semantics: confirm cancel first, then submit the replacement.
    /// An indeterminate/rejected cancel never proceeds to the submit step.
    pub async fn replace(
        &mut self,
        target_order_id: &str,
        replacement: SubmitOrder,
        symbol: Option<&str>,
    ) -> Result<CliExecutionOutput, String> {
        self.assert_account(&replacement)?;
        let canceled = self
            .gateway
            .cancel(target_order_id, symbol)
            .await
            .map_err(display)?;
        if !matches!(canceled, DirectCommandOutcome::Confirmed { .. }) {
            return Ok(CliExecutionOutput::Replace(CliExecutionReplaceResult {
                context: execution_context(&self.binding),
                command: "replace",
                result: Some("replacement_not_submitted"),
                cancel: outcome_result(canceled),
                submit: None,
            }));
        }
        let submitted = self
            .gateway
            .submit(&replacement, symbol)
            .await
            .map_err(display)?;
        Ok(CliExecutionOutput::Replace(CliExecutionReplaceResult {
            context: execution_context(&self.binding),
            command: "replace",
            result: None,
            cancel: outcome_result(canceled),
            submit: Some(outcome_result(submitted)),
        }))
    }

    fn assert_account(&self, request: &SubmitOrder) -> Result<(), String> {
        if request.account_id.as_str() != self.binding.account_id {
            return Err(format!(
                "request account {} does not match selected account {}",
                request.account_id, self.binding.account_id
            ));
        }
        if request.segment_key.as_str() != self.binding.segment_key {
            return Err(format!(
                "request segment {} does not match selected segment {}",
                request.segment_key, self.binding.segment_key
            ));
        }
        Ok(())
    }

    fn orders_result(&self, command: &str, orders: Vec<DirectOrder>) -> CliExecutionOrdersResult {
        CliExecutionOrdersResult {
            context: execution_context(&self.binding),
            command: command.to_owned(),
            orders: orders.iter().map(order_result).collect(),
        }
    }
}

fn execution_context(binding: &StandaloneExecutionBinding) -> CliExecutionContext {
    CliExecutionContext {
        owner: "execution",
        mode: "standalone",
        scope: "direct-provider",
        source: "provider",
        account_id: binding.account_id.clone(),
        provider: binding.provider.clone(),
        environment: binding.environment.clone(),
        segment: binding.segment_key.clone(),
    }
}

fn order_result(order: &DirectOrder) -> CliExecutionOrder {
    CliExecutionOrder {
        order_id: order.order_id.clone(),
        remote_order_id: order.remote_order_id.clone(),
        client_order_id: order.client_order_id.clone(),
        symbol: order.symbol.clone(),
        side: order.side.clone(),
        order_type: order.order_type.clone(),
        status: order.status.clone(),
        quantity: order.quantity.clone(),
        filled_quantity: order.filled_quantity.clone(),
        average_fill_price: order.average_fill_price.clone(),
        occurred_at_unix_nanos: order.occurred_at_unix_nanos,
    }
}

fn fill_result(fill: DirectFill) -> CliExecutionFill {
    CliExecutionFill {
        fill_id: fill.fill_id,
        remote_order_id: fill.remote_order_id,
        symbol: fill.symbol,
        side: fill.side,
        price: fill.price,
        quantity: fill.quantity,
        realized_pnl: fill.realized_pnl,
        fee: fill.fee,
        fee_currency: fill.fee_currency,
        executed_at_unix_nanos: fill.executed_at_unix_nanos,
    }
}

fn command_result(
    binding: &StandaloneExecutionBinding,
    command: &str,
    outcome: DirectCommandOutcome,
) -> CliExecutionCommandResult {
    CliExecutionCommandResult {
        context: execution_context(binding),
        command: command.to_owned(),
        outcome: outcome_result(outcome),
    }
}

fn outcome_result(outcome: DirectCommandOutcome) -> CliExecutionOutcome {
    match outcome {
        DirectCommandOutcome::Confirmed {
            order_id,
            remote_order_id,
            order_status,
            filled_quantity,
            occurred_at_unix_nanos,
            reason,
        } => CliExecutionOutcome {
            status: "confirmed",
            order_id: Some(order_id),
            remote_order_id,
            order_status: Some(order_status),
            filled_quantity,
            occurred_at_unix_nanos: Some(occurred_at_unix_nanos),
            reason: Some(reason),
            code: None,
            message: None,
            participant_request_id: None,
        },
        DirectCommandOutcome::Rejected {
            code,
            message,
            participant_request_id,
        } => CliExecutionOutcome {
            status: "rejected",
            order_id: None,
            remote_order_id: None,
            order_status: None,
            filled_quantity: None,
            occurred_at_unix_nanos: None,
            reason: None,
            code,
            message: Some(message),
            participant_request_id,
        },
        DirectCommandOutcome::Indeterminate {
            message,
            participant_request_id,
        } => CliExecutionOutcome {
            status: "indeterminate",
            order_id: None,
            remote_order_id: None,
            order_status: None,
            filled_quantity: None,
            occurred_at_unix_nanos: None,
            reason: None,
            code: None,
            message: Some(message),
            participant_request_id,
        },
    }
}

fn display(error: impl std::fmt::Display) -> String {
    error.to_string()
}
