//! Execution connected/runtime application facade.
//!
//! Connected CLI entry points use this facade to query the running Execution
//! projection or call typed Execution runtime control. Standalone order CLI
//! commands must use `CliExecutionApplication`.

use std::path::PathBuf;

use kairos_execution_contract::{
    CancelOrderRequest, ExecutionClient, ExecutionCommandStatus, ExecutionControlRpcClient,
    ExecutionReconcileResponse, ExecutionRoutesQuery, ExecutionRoutesResponse,
    ReconcileExecutionRequest, ReplaceOrderRequest, SubmitIntentRequest,
};
use kairos_primitives::execution::OrderId;
use kairos_primitives::runtime::InstanceIdentity;
use serde_json::Value;

pub struct ConnectedExecutionApplication {
    client: ExecutionClient,
    identity: InstanceIdentity,
    workspace_id: String,
    launch_id: String,
    instance_id: String,
}

impl ConnectedExecutionApplication {
    pub fn connect(
        socket: PathBuf,
        view_root: PathBuf,
        identity: InstanceIdentity,
        workspace_id: String,
        launch_id: String,
        instance_id: String,
    ) -> Result<Self, Box<dyn std::error::Error>> {
        let mut system = kairos_conflux::ConfluxSystem::new();
        system.install_execution_connection("execution", socket, Some(view_root))?;
        let client = system
            .execution_client("execution")
            .ok_or("managed Execution client is missing: execution")?;
        Ok(Self {
            client,
            identity,
            workspace_id,
            launch_id,
            instance_id,
        })
    }

    pub fn connect_control(
        socket: PathBuf,
        identity: InstanceIdentity,
        workspace_id: String,
        launch_id: String,
        instance_id: String,
    ) -> Result<Self, Box<dyn std::error::Error>> {
        let mut system = kairos_conflux::ConfluxSystem::new();
        system.install_execution_connection("execution", socket, None)?;
        let client = system
            .execution_client("execution")
            .ok_or("managed Execution client is missing: execution")?;
        Ok(Self {
            client,
            identity,
            workspace_id,
            launch_id,
            instance_id,
        })
    }

    pub fn snapshot(&self) -> Result<Value, Box<dyn std::error::Error>> {
        let projection = self.current_projection()?;
        Ok(serde_json::json!({
            "generation": projection.generation,
            "event_sequence": projection.event_sequence,
            "orders": projection.orders,
            "intents": projection.intents,
            "fills": projection.fills,
            "events": projection.events,
            "unknown_remote_orders": projection.unknown_remote_orders,
            "commitment_count": projection.commitment_count,
            "risk_reservation_count": projection.risk_reservation_count,
            "exchange_event_watermark_unix_nanos": projection.exchange_event_watermark_unix_nanos,
            "fill_history_truncated": projection.fill_history_truncated,
            "order_event_history_truncated": projection.order_event_history_truncated,
            "intent_event_history_truncated": projection.intent_event_history_truncated,
        }))
    }

    pub fn orders(&self, account_id: Option<&str>) -> Result<Value, Box<dyn std::error::Error>> {
        let projection = self.current_projection()?;
        Ok(serde_json::json!({
            "orders": filter_orders(projection.orders, account_id, None)
        }))
    }

    pub fn open_orders(
        &self,
        account_id: Option<&str>,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let projection = self.current_projection()?;
        Ok(serde_json::json!({
            "orders": filter_orders(projection.orders, account_id, Some(false))
        }))
    }

    pub fn history(&self, account_id: Option<&str>) -> Result<Value, Box<dyn std::error::Error>> {
        let projection = self.current_projection()?;
        Ok(serde_json::json!({
            "orders": filter_orders(projection.orders, account_id, Some(true))
        }))
    }

    pub fn unknown_remote_orders(&self) -> Result<Value, Box<dyn std::error::Error>> {
        let projection = self.current_projection()?;
        Ok(serde_json::json!({
            "orders": projection.unknown_remote_orders
        }))
    }

    pub fn order_status(&self, order_id: &str) -> Result<Value, Box<dyn std::error::Error>> {
        let projection = self.current_projection()?;
        projection
            .orders
            .into_iter()
            .find(|value| value["order_id"] == order_id)
            .ok_or_else(|| format!("unknown order: {order_id}").into())
    }

    pub fn events(&self, order_id: Option<&str>) -> Result<Value, Box<dyn std::error::Error>> {
        let projection = self.current_projection()?;
        Ok(serde_json::json!({
            "events": filter_events(projection.events, order_id, None, None, None)
        }))
    }

    pub fn trace(&self, order_id: &str) -> Result<Value, Box<dyn std::error::Error>> {
        let projection = self.current_projection()?;
        Ok(serde_json::json!({
            "events": filter_events(projection.events, Some(order_id), None, None, None)
        }))
    }

    pub fn audit(
        &self,
        order_id: Option<&str>,
        remote_order_id: Option<&str>,
        status: Option<&str>,
        limit: Option<u32>,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let projection = self.current_projection()?;
        Ok(serde_json::json!({
            "events": filter_events(projection.events, order_id, remote_order_id, status, limit)
        }))
    }

    pub fn fills(&self, order_id: Option<&str>) -> Result<Value, Box<dyn std::error::Error>> {
        let projection = self.current_projection()?;
        Ok(serde_json::json!({
            "fills": projection.fills.into_iter().filter(|value| {
                order_id.is_none_or(|expected| value["order_id"] == expected)
            }).collect::<Vec<_>>()
        }))
    }

    pub async fn routes(
        &self,
        query: ExecutionRoutesQuery,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let response: ExecutionRoutesResponse =
            ExecutionControlRpcClient::routes(&self.client.control(), query).await?;
        Ok(serde_json::to_value(response)?)
    }

    pub async fn reconcile(
        &self,
        request: ReconcileExecutionRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let response: ExecutionReconcileResponse =
            ExecutionControlRpcClient::reconcile(&self.client.control(), request).await?;
        Ok(serde_json::to_value(response)?)
    }

    pub async fn submit_intent(
        &self,
        request: SubmitIntentRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let response: ExecutionCommandStatus =
            ExecutionControlRpcClient::submit_intent(&self.client.control(), request).await?;
        Ok(serde_json::to_value(response)?)
    }

    pub async fn cancel_order(
        &self,
        order_id: OrderId,
        request: CancelOrderRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let response: ExecutionCommandStatus =
            ExecutionControlRpcClient::cancel_order(&self.client.control(), order_id, request)
                .await?;
        Ok(serde_json::to_value(response)?)
    }

    pub async fn replace_order(
        &self,
        order_id: OrderId,
        request: ReplaceOrderRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let response: ExecutionCommandStatus =
            ExecutionControlRpcClient::replace_order(&self.client.control(), order_id, request)
                .await?;
        Ok(serde_json::to_value(response)?)
    }

    fn current_projection(&self) -> Result<ExecutionProjectionJson, Box<dyn std::error::Error>> {
        use kairos_protocol::generated::kairos::common::v_2 as common;
        use kairos_protocol::generated::kairos::execution::v_2 as fb;

        let frame = self.client.current_execution(&self.identity)?.read()?;
        let envelope = frame.envelope_metadata();
        let view = frame.view()?;
        let metadata = view.metadata();
        if metadata.workspace_id() != self.workspace_id
            || metadata.launch_id() != Some(self.launch_id.as_str())
            || metadata.instance_id() != Some(self.instance_id.as_str())
        {
            return Err("Execution mmap identity mismatch".into());
        }
        if metadata.completeness() != common::ViewCompleteness::COMPLETE
            || metadata.generation() != envelope.generation
            || metadata.applied_revision().unwrap_or_default() != envelope.applied_event_sequence
        {
            return Err("Execution mmap is partial or its watermarks differ".into());
        }

        let projection = ExecutionProjectionJson {
            generation: metadata.generation(),
            event_sequence: metadata.applied_revision().unwrap_or_default(),
            orders: view.orders().iter().map(order_json).collect(),
            intents: view.intents().iter().map(intent_json).collect(),
            fills: view.fills().iter().map(fill_json).collect(),
            events: view.order_events().iter().map(order_event_json).collect(),
            unknown_remote_orders: view
                .unknown_remote_orders()
                .iter()
                .map(unknown_remote_json)
                .collect(),
            commitment_count: view.commitments().len(),
            risk_reservation_count: view.risk_reservations().len(),
            exchange_event_watermark_unix_nanos: view.exchange_event_watermark_unix_nanos() as i64,
            fill_history_truncated: view.fill_history_truncated(),
            order_event_history_truncated: view.order_event_history_truncated(),
            intent_event_history_truncated: view.intent_event_history_truncated(),
        };

        fn order_json(value: fb::OrderState<'_>) -> Value {
            serde_json::json!({
                "order_id": value.order_id(),
                "intent_id": value.intent_id(),
                "plan_id": value.plan_id(),
                "leg_id": value.leg_id(),
                "strategy_id": value.strategy_id(),
                "account_id": value.account_id(),
                "segment_key": value.segment_key(),
                "instrument_id": value.instrument_id(),
                "market_id": value.market_id(),
                "execution_route_id": value.execution_route_id(),
                "remote_order_id": value.remote_order_id(),
                "side": value.side().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                "order_type": value.order_type().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                "quantity": decimal_json(value.quantity()),
                "filled_quantity": decimal_json(value.filled_quantity()),
                "limit_price": value.limit_price().map(decimal_json),
                "status": value.lifecycle().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                "terminal": matches!(value.lifecycle(), fb::OrderLifecycle::FILLED | fb::OrderLifecycle::CANCELED | fb::OrderLifecycle::REJECTED | fb::OrderLifecycle::EXPIRED | fb::OrderLifecycle::FAILED),
                "submitted_at_unix_nanos": value.submitted_at_unix_nanos(),
                "updated_at_unix_nanos": value.updated_at_unix_nanos(),
                "reason": value.reason(),
            })
        }

        fn intent_json(value: fb::IntentState<'_>) -> Value {
            let intent = value.intent();
            serde_json::json!({
                "intent_id": intent.intent_id(),
                "strategy_id": intent.strategy_id(),
                "launch_id": intent.launch_id(),
                "instance_id": intent.instance_id(),
                "intent_type": intent.intent_type().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                "status": value.lifecycle().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                "updated_at_unix_nanos": value.updated_at_unix_nanos(),
                "reason": value.reason(),
            })
        }

        fn fill_json(value: fb::Fill<'_>) -> Value {
            serde_json::json!({
                "fill_id": value.fill_id(),
                "order_id": value.order_id(),
                "intent_id": value.intent_id(),
                "strategy_id": value.strategy_id(),
                "account_id": value.account_id(),
                "segment_key": value.segment_key(),
                "instrument_id": value.instrument_id(),
                "market_id": value.market_id(),
                "remote_order_id": value.remote_order_id(),
                "side": value.side().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                "quantity": decimal_json(value.quantity()),
                "price": decimal_json(value.price()),
                "fee": value.fee().map(decimal_json),
                "fee_currency": value.fee_asset_id(),
                "occurred_at_unix_nanos": value.source_filled_at_unix_nanos(),
            })
        }

        fn order_event_json(value: fb::OrderLifecycleEventState<'_>) -> Value {
            serde_json::json!({
                "order_id": value.order_id(),
                "intent_id": value.intent_id(),
                "plan_id": value.plan_id(),
                "leg_id": value.leg_id(),
                "status": value.lifecycle().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                "remote_order_id": value.remote_order_id(),
                "occurred_at_unix_nanos": value.occurred_at_unix_nanos(),
                "reason": value.reason(),
                "fill_id": value.fill_id(),
                "filled_quantity": value.filled_quantity().map(decimal_json),
            })
        }

        fn unknown_remote_json(value: fb::UnknownRemoteOrderState<'_>) -> Value {
            serde_json::json!({
                "remote_order_id": value.remote_order_id(),
                "symbol": value.symbol(),
                "status": value.lifecycle().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                "execution_id": value.execution_id(),
                "fill_quantity": value.fill_quantity().map(decimal_json),
                "fill_price": value.fill_price().map(decimal_json),
                "fee_currency": value.fee_currency(),
                "fee_amount": value.fee_amount().map(decimal_json),
                "first_seen_at_unix_nanos": value.first_seen_at_unix_nanos(),
                "last_seen_at_unix_nanos": value.last_seen_at_unix_nanos(),
                "resolution": value.resolution(),
                "reason": value.reason(),
            })
        }

        fn decimal_json(value: &common::Decimal64) -> String {
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

        Ok(projection)
    }
}

struct ExecutionProjectionJson {
    generation: u64,
    event_sequence: u64,
    orders: Vec<Value>,
    intents: Vec<Value>,
    fills: Vec<Value>,
    events: Vec<Value>,
    unknown_remote_orders: Vec<Value>,
    commitment_count: usize,
    risk_reservation_count: usize,
    exchange_event_watermark_unix_nanos: i64,
    fill_history_truncated: bool,
    order_event_history_truncated: bool,
    intent_event_history_truncated: bool,
}

fn filter_orders(
    values: Vec<Value>,
    account_id: Option<&str>,
    terminal: Option<bool>,
) -> Vec<Value> {
    values
        .into_iter()
        .filter(|value| {
            account_id.is_none_or(|expected| value["account_id"] == expected)
                && terminal.is_none_or(|expected| value["terminal"] == expected)
        })
        .collect()
}

fn filter_events(
    values: Vec<Value>,
    order_id: Option<&str>,
    remote_order_id: Option<&str>,
    status: Option<&str>,
    limit: Option<u32>,
) -> Vec<Value> {
    let mut values = values
        .into_iter()
        .filter(|value| {
            order_id.is_none_or(|expected| value["order_id"] == expected)
                && remote_order_id.is_none_or(|expected| value["remote_order_id"] == expected)
                && status.is_none_or(|expected| value["status"] == expected)
        })
        .collect::<Vec<_>>();
    if let Some(limit) = limit {
        values.truncate(limit as usize);
    }
    values
}
