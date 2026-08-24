//! Execution connected/runtime application facade.
//!
//! Connected CLI entry points use this facade to query the running Execution
//! current or call typed Execution runtime control. Standalone order CLI
//! commands must use `CliExecutionApplication`.

use std::path::PathBuf;

use kairos_execution_contract::{
    CancelOrderRequest, ExecutionClient, ExecutionCommandStatus, ExecutionControlRpcClient,
    ExecutionReconcileResponse, ExecutionRoutesQuery, ExecutionRoutesResponse,
    ReconcileExecutionRequest, ReplaceOrderRequest, SubmitIntentRequest,
};
use kairos_primitives::execution::OrderId;
use kairos_primitives::runtime::InstanceIdentity;
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct ExecutionSnapshotResult {
    pub generation: u64,
    pub event_sequence: u64,
    pub orders: Vec<ExecutionOrderResult>,
    pub intents: Vec<ExecutionIntentResult>,
    pub fills: Vec<ExecutionFillResult>,
    pub events: Vec<ExecutionOrderEventResult>,
    pub unknown_remote_orders: Vec<UnknownRemoteOrderResult>,
    pub commitment_count: usize,
    pub risk_reservation_count: usize,
    pub exchange_event_watermark_unix_nanos: i64,
    pub fill_history_truncated: bool,
    pub order_event_history_truncated: bool,
    pub intent_event_history_truncated: bool,
}

#[derive(Debug, Serialize)]
pub struct ExecutionOrdersResult {
    pub orders: Vec<ExecutionOrderResult>,
}

#[derive(Debug, Serialize)]
pub struct ExecutionEventsResult {
    pub events: Vec<ExecutionOrderEventResult>,
}

#[derive(Debug, Serialize)]
pub struct ExecutionFillsResult {
    pub fills: Vec<ExecutionFillResult>,
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
pub struct ExecutionIntentResult {
    pub intent_id: String,
    pub strategy_id: String,
    pub launch_id: String,
    pub instance_id: String,
    pub intent_type: String,
    pub status: String,
    pub updated_at_unix_nanos: u64,
    pub reason: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct ExecutionFillResult {
    pub fill_id: String,
    pub order_id: String,
    pub intent_id: String,
    pub strategy_id: String,
    pub account_id: String,
    pub segment_key: String,
    pub instrument_id: String,
    pub market_id: String,
    pub remote_order_id: Option<String>,
    pub side: String,
    pub quantity: String,
    pub price: String,
    pub fee: Option<String>,
    pub fee_currency: Option<String>,
    pub occurred_at_unix_nanos: u64,
}

#[derive(Debug, Serialize)]
pub struct ExecutionOrderEventResult {
    pub order_id: String,
    pub intent_id: Option<String>,
    pub plan_id: Option<String>,
    pub leg_id: Option<String>,
    pub status: String,
    pub remote_order_id: Option<String>,
    pub occurred_at_unix_nanos: u64,
    pub reason: Option<String>,
    pub fill_id: Option<String>,
    pub filled_quantity: Option<String>,
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
    Snapshot(ExecutionSnapshotResult),
    Orders(ExecutionOrdersResult),
    UnknownRemoteOrders(UnknownRemoteOrdersResult),
    Order(ExecutionOrderResult),
    Events(ExecutionEventsResult),
    Fills(ExecutionFillsResult),
    Routes(ExecutionRoutesResponse),
    Reconcile(ExecutionReconcileResponse),
    Command(ExecutionCommandStatus),
}

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

    pub fn snapshot(&self) -> Result<ExecutionSnapshotResult, Box<dyn std::error::Error>> {
        self.read_current()
    }

    pub fn orders(
        &self,
        account_id: Option<&str>,
    ) -> Result<ExecutionOrdersResult, Box<dyn std::error::Error>> {
        let current = self.read_current()?;
        Ok(ExecutionOrdersResult {
            orders: filter_orders(current.orders, account_id, None),
        })
    }

    pub fn open_orders(
        &self,
        account_id: Option<&str>,
    ) -> Result<ExecutionOrdersResult, Box<dyn std::error::Error>> {
        let current = self.read_current()?;
        Ok(ExecutionOrdersResult {
            orders: filter_orders(current.orders, account_id, Some(false)),
        })
    }

    pub fn history(
        &self,
        account_id: Option<&str>,
    ) -> Result<ExecutionOrdersResult, Box<dyn std::error::Error>> {
        let current = self.read_current()?;
        Ok(ExecutionOrdersResult {
            orders: filter_orders(current.orders, account_id, Some(true)),
        })
    }

    pub fn unknown_remote_orders(
        &self,
    ) -> Result<UnknownRemoteOrdersResult, Box<dyn std::error::Error>> {
        let current = self.read_current()?;
        Ok(UnknownRemoteOrdersResult {
            orders: current.unknown_remote_orders,
        })
    }

    pub fn order_status(
        &self,
        order_id: &str,
    ) -> Result<ExecutionOrderResult, Box<dyn std::error::Error>> {
        let current = self.read_current()?;
        current
            .orders
            .into_iter()
            .find(|value| value.order_id == order_id)
            .ok_or_else(|| format!("unknown order: {order_id}").into())
    }

    pub fn events(
        &self,
        order_id: Option<&str>,
    ) -> Result<ExecutionEventsResult, Box<dyn std::error::Error>> {
        let current = self.read_current()?;
        Ok(ExecutionEventsResult {
            events: filter_events(current.events, order_id, None, None, None),
        })
    }

    pub fn trace(
        &self,
        order_id: &str,
    ) -> Result<ExecutionEventsResult, Box<dyn std::error::Error>> {
        let current = self.read_current()?;
        Ok(ExecutionEventsResult {
            events: filter_events(current.events, Some(order_id), None, None, None),
        })
    }

    pub fn audit(
        &self,
        order_id: Option<&str>,
        remote_order_id: Option<&str>,
        status: Option<&str>,
        limit: Option<u32>,
    ) -> Result<ExecutionEventsResult, Box<dyn std::error::Error>> {
        let current = self.read_current()?;
        Ok(ExecutionEventsResult {
            events: filter_events(current.events, order_id, remote_order_id, status, limit),
        })
    }

    pub fn fills(
        &self,
        order_id: Option<&str>,
    ) -> Result<ExecutionFillsResult, Box<dyn std::error::Error>> {
        let current = self.read_current()?;
        Ok(ExecutionFillsResult {
            fills: current
                .fills
                .into_iter()
                .filter(|value| order_id.is_none_or(|expected| value.order_id == expected))
                .collect(),
        })
    }

    pub async fn routes(
        &self,
        query: ExecutionRoutesQuery,
    ) -> Result<ExecutionRoutesResponse, Box<dyn std::error::Error>> {
        let response: ExecutionRoutesResponse =
            ExecutionControlRpcClient::routes(&self.client.control(), query).await?;
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

    fn read_current(&self) -> Result<ExecutionSnapshotResult, Box<dyn std::error::Error>> {
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

        let current = ExecutionSnapshotResult {
            generation: metadata.generation(),
            event_sequence: metadata.applied_revision().unwrap_or_default(),
            orders: view.orders().iter().map(order_result).collect(),
            intents: view.intents().iter().map(intent_result).collect(),
            fills: view.fills().iter().map(fill_result).collect(),
            events: view.order_events().iter().map(order_event_result).collect(),
            unknown_remote_orders: view
                .unknown_remote_orders()
                .iter()
                .map(unknown_remote_result)
                .collect(),
            commitment_count: view.commitments().len(),
            risk_reservation_count: view.risk_reservations().len(),
            exchange_event_watermark_unix_nanos: view.exchange_event_watermark_unix_nanos() as i64,
            fill_history_truncated: view.fill_history_truncated(),
            order_event_history_truncated: view.order_event_history_truncated(),
            intent_event_history_truncated: view.intent_event_history_truncated(),
        };

        fn order_result(value: fb::OrderState<'_>) -> ExecutionOrderResult {
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

        fn intent_result(value: fb::IntentState<'_>) -> ExecutionIntentResult {
            let intent = value.intent();
            ExecutionIntentResult {
                intent_id: intent.intent_id().to_owned(),
                strategy_id: intent.strategy_id().to_owned(),
                launch_id: intent.launch_id().to_owned(),
                instance_id: intent.instance_id().to_owned(),
                intent_type: enum_name(intent.intent_type().variant_name()),
                status: enum_name(value.lifecycle().variant_name()),
                updated_at_unix_nanos: value.updated_at_unix_nanos(),
                reason: value.reason().map(str::to_owned),
            }
        }

        fn fill_result(value: fb::Fill<'_>) -> ExecutionFillResult {
            ExecutionFillResult {
                fill_id: value.fill_id().to_owned(),
                order_id: value.order_id().to_owned(),
                intent_id: value.intent_id().to_owned(),
                strategy_id: value.strategy_id().to_owned(),
                account_id: value.account_id().to_owned(),
                segment_key: value.segment_key().to_owned(),
                instrument_id: value.instrument_id().to_owned(),
                market_id: value.market_id().to_owned(),
                remote_order_id: value.remote_order_id().map(str::to_owned),
                side: enum_name(value.side().variant_name()),
                quantity: decimal_string(value.quantity()),
                price: decimal_string(value.price()),
                fee: value.fee().map(decimal_string),
                fee_currency: value.fee_asset_id().map(str::to_owned),
                occurred_at_unix_nanos: value.source_filled_at_unix_nanos(),
            }
        }

        fn order_event_result(
            value: fb::OrderLifecycleEventState<'_>,
        ) -> ExecutionOrderEventResult {
            ExecutionOrderEventResult {
                order_id: value.order_id().to_owned(),
                intent_id: value.intent_id().map(str::to_owned),
                plan_id: value.plan_id().map(str::to_owned),
                leg_id: value.leg_id().map(str::to_owned),
                status: enum_name(value.lifecycle().variant_name()),
                remote_order_id: value.remote_order_id().map(str::to_owned),
                occurred_at_unix_nanos: value.occurred_at_unix_nanos(),
                reason: value.reason().map(str::to_owned),
                fill_id: value.fill_id().map(str::to_owned),
                filled_quantity: value.filled_quantity().map(decimal_string),
            }
        }

        fn unknown_remote_result(
            value: fb::UnknownRemoteOrderState<'_>,
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

        fn decimal_string(value: &common::Decimal64) -> String {
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

        Ok(current)
    }
}

fn filter_orders(
    values: Vec<ExecutionOrderResult>,
    account_id: Option<&str>,
    terminal: Option<bool>,
) -> Vec<ExecutionOrderResult> {
    values
        .into_iter()
        .filter(|value| {
            account_id.is_none_or(|expected| value.account_id == expected)
                && terminal.is_none_or(|expected| value.terminal == expected)
        })
        .collect()
}

fn filter_events(
    values: Vec<ExecutionOrderEventResult>,
    order_id: Option<&str>,
    remote_order_id: Option<&str>,
    status: Option<&str>,
    limit: Option<u32>,
) -> Vec<ExecutionOrderEventResult> {
    let mut values = values
        .into_iter()
        .filter(|value| {
            order_id.is_none_or(|expected| value.order_id == expected)
                && remote_order_id
                    .is_none_or(|expected| value.remote_order_id.as_deref() == Some(expected))
                && status.is_none_or(|expected| value.status == expected)
        })
        .collect::<Vec<_>>();
    if let Some(limit) = limit {
        values.truncate(limit as usize);
    }
    values
}
