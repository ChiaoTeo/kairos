//! Standalone, short-lived Execution use cases.

use kairos_conflux::{
    CommandOutcome, DecimalValue, ExternalOrder, ExternalOrderQuery, OrderEntryOptions,
    OrderEntryRequest, ParticipantInstrumentRef, ParticipantInstrumentTypeRef, ParticipantKind,
    ParticipantRef, TimeInForce,
};
use kairos_primitives::account::{AccountId, SegmentKey};
use kairos_primitives::execution::{OrderId, OrderSide as PrimitiveOrderSide};
use kairos_primitives::reference::{InstrumentId, Symbol};
use serde::{Deserialize, Serialize};

use crate::application::{ExecutionOrderOptions, OrderSide, OrderType, SubmitOrder};
use crate::services::direct::{DirectFill, DirectOrderConnection};

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
    connection: DirectOrderConnection,
}

impl CliExecutionApplication {
    pub(crate) fn new(
        binding: StandaloneExecutionBinding,
        connection: DirectOrderConnection,
    ) -> Result<Self, String> {
        AccountId::new(binding.account_id.clone()).map_err(|error| error.to_string())?;
        SegmentKey::new(binding.segment_key.clone()).map_err(|error| error.to_string())?;
        Ok(Self {
            binding,
            connection,
        })
    }

    pub async fn open_orders(
        &mut self,
        symbol: Option<&str>,
        limit: Option<u32>,
    ) -> Result<CliExecutionOutput, String> {
        let query = self.query(symbol, None, limit)?;
        let orders = self.connection.open_orders(&query).await.map_err(display)?;
        Ok(CliExecutionOutput::Orders(
            self.orders_result("open-orders", orders),
        ))
    }

    pub async fn history(
        &mut self,
        symbol: Option<&str>,
        limit: Option<u32>,
    ) -> Result<CliExecutionOutput, String> {
        let query = self.query(symbol, None, limit)?;
        let orders = self.connection.history(&query).await.map_err(display)?;
        Ok(CliExecutionOutput::Orders(
            self.orders_result("history", orders),
        ))
    }

    pub async fn order(
        &mut self,
        order_id: &str,
        symbol: Option<&str>,
    ) -> Result<CliExecutionOutput, String> {
        let order = self.find_order(order_id, symbol).await?;
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
            .connection
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
        let provider_request = self.provider_request(&request, symbol)?;
        let outcome = self
            .connection
            .submit(&provider_request)
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
        let order = self.find_order(order_id, symbol).await?;
        let request = self.request_from_external(&order)?;
        let outcome = self
            .connection
            .cancel(&request, order.remote_order_id.as_str(), now_unix_nanos())
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
        let target = self.find_order(target_order_id, symbol).await?;
        let cancel_request = self.request_from_external(&target)?;
        let canceled = self
            .connection
            .cancel(
                &cancel_request,
                target.remote_order_id.as_str(),
                now_unix_nanos(),
            )
            .await
            .map_err(display)?;
        if !matches!(canceled, CommandOutcome::Confirmed(_)) {
            return Ok(CliExecutionOutput::Replace(CliExecutionReplaceResult {
                context: execution_context(&self.binding),
                command: "replace",
                result: Some("replacement_not_submitted"),
                cancel: outcome_result(canceled),
                submit: None,
            }));
        }
        let provider_request = self.provider_request(&replacement, symbol)?;
        let submitted = self
            .connection
            .submit(&provider_request)
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

    fn query(
        &self,
        symbol: Option<&str>,
        order_id: Option<&str>,
        limit: Option<u32>,
    ) -> Result<ExternalOrderQuery, String> {
        Ok(ExternalOrderQuery {
            symbol: symbol.map(Symbol::new).transpose().map_err(display)?,
            instrument_type: Some(ParticipantInstrumentTypeRef::new(
                self.binding.execution_channel.clone(),
            )?),
            order_id: order_id.map(OrderId::new).transpose().map_err(display)?,
            limit,
            since_unix_nanos: None,
        })
    }

    async fn find_order(
        &mut self,
        id: &str,
        symbol: Option<&str>,
    ) -> Result<ExternalOrder, String> {
        let query = self.query(symbol, None, Some(100))?;
        let matches = |order: &ExternalOrder| {
            order.order_id.as_str() == id
                || order.remote_order_id.as_str() == id
                || order
                    .client_order_id
                    .as_ref()
                    .is_some_and(|value| value.as_str() == id)
        };
        if let Some(order) = self
            .connection
            .open_orders(&query)
            .await
            .map_err(display)?
            .into_iter()
            .find(matches)
        {
            return Ok(order);
        }
        if symbol.is_some() {
            if let Some(order) = self
                .connection
                .history(&query)
                .await
                .map_err(display)?
                .into_iter()
                .find(matches)
            {
                return Ok(order);
            }
        }
        let query = self.query(symbol, Some(id), Some(1))?;
        self.connection
            .order(&query)
            .await
            .map_err(display)?
            .ok_or_else(|| format!("provider order not found: {id}"))
    }

    fn provider_request(
        &self,
        request: &SubmitOrder,
        symbol: Option<&str>,
    ) -> Result<OrderEntryRequest, String> {
        let source_symbol = symbol.unwrap_or(request.instrument_id.as_str());
        let mut options = provider_options(&request.options)?;
        if options.wallet_type.is_none() {
            options.wallet_type = self.binding.trading_mode.clone();
        }
        Ok(OrderEntryRequest {
            order_id: request.order_id.clone(),
            intent_id: request.intent_id.clone(),
            submitted_at_unix_nanos: request
                .submitted_at_unix_nanos
                .unwrap_or_else(|| now_unix_nanos().into()),
            account_id: request.account_id.clone(),
            segment_key: request.segment_key.clone(),
            instrument_id: request.instrument_id.clone(),
            market_id: request.market_id.clone(),
            participant_instrument: self.participant_instrument(source_symbol)?,
            side: match request.side {
                OrderSide::Buy => PrimitiveOrderSide::Buy,
                OrderSide::Sell => PrimitiveOrderSide::Sell,
            },
            quantity: DecimalValue::new(request.quantity.mantissa(), request.quantity.scale()),
            order_type: match request.order_type {
                OrderType::Market => kairos_conflux::OrderType::Market,
                OrderType::Limit => kairos_conflux::OrderType::Limit,
            },
            limit_price: request
                .limit_price
                .map(|value| DecimalValue::new(value.mantissa(), value.scale())),
            options,
        })
    }

    fn request_from_external(&self, order: &ExternalOrder) -> Result<OrderEntryRequest, String> {
        Ok(OrderEntryRequest {
            order_id: order.order_id.clone(),
            intent_id: None,
            submitted_at_unix_nanos: order
                .occurred_at_unix_nanos
                .unwrap_or_else(|| now_unix_nanos().into()),
            account_id: AccountId::new(self.binding.account_id.clone()).map_err(display)?,
            segment_key: SegmentKey::new(self.binding.segment_key.clone()).map_err(display)?,
            instrument_id: InstrumentId::new(order.symbol.to_string()).map_err(display)?,
            market_id: None,
            participant_instrument: self.participant_instrument(order.symbol.as_str())?,
            side: order.side,
            quantity: order.quantity,
            order_type: order.order_type,
            limit_price: order.average_fill_price,
            options: OrderEntryOptions::default(),
        })
    }

    fn participant_instrument(&self, symbol: &str) -> Result<ParticipantInstrumentRef, String> {
        ParticipantInstrumentRef::new(
            ParticipantRef::new(
                if self.binding.provider.eq_ignore_ascii_case("ibkr") {
                    ParticipantKind::Broker
                } else {
                    ParticipantKind::Exchange
                },
                self.binding.provider.clone(),
            )?,
            Some(ParticipantInstrumentTypeRef::new(
                self.binding.execution_channel.clone(),
            )?),
            symbol,
        )
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

    fn orders_result(&self, command: &str, orders: Vec<ExternalOrder>) -> CliExecutionOrdersResult {
        CliExecutionOrdersResult {
            context: execution_context(&self.binding),
            command: command.to_owned(),
            orders: orders.iter().map(order_result).collect(),
        }
    }
}

fn provider_options(options: &ExecutionOrderOptions) -> Result<OrderEntryOptions, String> {
    Ok(OrderEntryOptions {
        time_in_force: options
            .time_in_force
            .as_deref()
            .map(parse_tif)
            .transpose()?,
        reduce_only: options.reduce_only,
        post_only: options.post_only,
        position_side: options.position_side.clone(),
        quote_asset: options.quote_asset.clone(),
        wallet_type: options.wallet_type.clone(),
        trading_session: options.trading_session.clone(),
        tokenize: options.tokenize,
    })
}

fn parse_tif(value: &str) -> Result<TimeInForce, String> {
    match value.trim().to_ascii_lowercase().replace('_', "-").as_str() {
        "gtc" | "good-til-canceled" => Ok(TimeInForce::GoodTilCanceled),
        "ioc" | "immediate-or-cancel" => Ok(TimeInForce::ImmediateOrCancel),
        "fok" | "fill-or-kill" => Ok(TimeInForce::FillOrKill),
        "day" => Ok(TimeInForce::Day),
        value => Err(format!("unsupported time in force: {value}")),
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

fn order_result(order: &ExternalOrder) -> CliExecutionOrder {
    CliExecutionOrder {
        order_id: order.order_id.to_string(),
        remote_order_id: order.remote_order_id.to_string(),
        client_order_id: order.client_order_id.as_ref().map(ToString::to_string),
        symbol: order.symbol.to_string(),
        side: format!("{:?}", order.side).to_ascii_lowercase(),
        order_type: format!("{:?}", order.order_type).to_ascii_lowercase(),
        status: format!("{:?}", order.status).to_ascii_lowercase(),
        quantity: decimal(order.quantity),
        filled_quantity: decimal(order.filled_quantity),
        average_fill_price: order.average_fill_price.map(decimal),
        occurred_at_unix_nanos: order.occurred_at_unix_nanos.map(|value| value.get()),
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
    outcome: CommandOutcome<kairos_conflux::OrderEntryEvent>,
) -> CliExecutionCommandResult {
    CliExecutionCommandResult {
        context: execution_context(binding),
        command: command.to_owned(),
        outcome: outcome_result(outcome),
    }
}

fn outcome_result(outcome: CommandOutcome<kairos_conflux::OrderEntryEvent>) -> CliExecutionOutcome {
    match outcome {
        CommandOutcome::Confirmed(event) => CliExecutionOutcome {
            status: "confirmed",
            order_id: Some(event.order_id.to_string()),
            remote_order_id: event.remote_order_id.map(|value| value.to_string()),
            order_status: Some(format!("{:?}", event.status).to_ascii_lowercase()),
            filled_quantity: event.filled_quantity.map(decimal),
            occurred_at_unix_nanos: Some(event.occurred_at_unix_nanos.get()),
            reason: Some(event.reason),
            code: None,
            message: None,
            participant_request_id: None,
        },
        CommandOutcome::Rejected(value) => CliExecutionOutcome {
            status: "rejected",
            order_id: None,
            remote_order_id: None,
            order_status: None,
            filled_quantity: None,
            occurred_at_unix_nanos: None,
            reason: None,
            code: value.code,
            message: Some(value.message),
            participant_request_id: value.participant_request_id,
        },
        CommandOutcome::Indeterminate(value) => CliExecutionOutcome {
            status: "indeterminate",
            order_id: None,
            remote_order_id: None,
            order_status: None,
            filled_quantity: None,
            occurred_at_unix_nanos: None,
            reason: None,
            code: None,
            message: Some(value.message),
            participant_request_id: value.participant_request_id,
        },
    }
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u64::MAX as u128) as u64
}

fn display(error: impl std::fmt::Display) -> String {
    error.to_string()
}

fn decimal(value: DecimalValue) -> String {
    value
        .format_fixed()
        .unwrap_or_else(|_| format!("{}e-{}", value.mantissa, value.scale))
}
