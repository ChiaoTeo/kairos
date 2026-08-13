use crate::model::{
    ExecutionOrderStatus, ExecutionSnapshot, IntentState, IntentStatus, OrderSide, OrderType,
};
use flatbuffers::FlatBufferBuilder;
use kairos_protocol::generated::kairos::{
    common::v_1::{
        Decimal64, OrderType as FbOrderType, Side as FbSide, SnapshotHeader, SnapshotHeaderArgs,
    },
    execution::v_1 as execution_fb,
    intent::v_1 as intent_fb,
};
use kairos_transport::SharedSnapshotWriter;
use std::path::Path;

pub struct SharedExecutionSnapshotPublisher {
    writer: SharedSnapshotWriter,
    actor_id: String,
}

impl SharedExecutionSnapshotPublisher {
    pub fn create(
        path: impl AsRef<Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            writer: SharedSnapshotWriter::create(path, slot_size).map_err(|e| e.to_string())?,
            actor_id: actor_id.into(),
        })
    }
    pub fn publish(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        self.writer
            .publish(
                snapshot.generation,
                &encode_orders(snapshot, &self.actor_id)?,
            )
            .map_err(|e| e.to_string())
    }
}

pub struct SharedIntentSnapshotPublisher {
    writer: SharedSnapshotWriter,
    actor_id: String,
}

impl SharedIntentSnapshotPublisher {
    pub fn create(
        path: impl AsRef<Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            writer: SharedSnapshotWriter::create(path, slot_size).map_err(|e| e.to_string())?,
            actor_id: actor_id.into(),
        })
    }
    pub fn publish(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        self.writer
            .publish(
                snapshot.generation,
                &encode_intents(snapshot, &self.actor_id)?,
            )
            .map_err(|e| e.to_string())
    }
}

fn encode_orders(snapshot: &ExecutionSnapshot, actor_id: &str) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let orders = snapshot
        .orders
        .iter()
        .map(|order| {
            let (quantity_mantissa, quantity_scale) = order.quantity.parts()?;
            let (filled_mantissa, filled_scale) = order.filled_quantity.parts()?;
            let (quantity_mantissa, filled_mantissa, quantity_scale) = align_decimal_parts(
                quantity_mantissa,
                quantity_scale,
                filled_mantissa,
                filled_scale,
            )?;
            let order_id = builder.create_string(&order.order_id);
            let account_id = builder.create_string(&order.account_id);
            let instrument_id = builder.create_string(&order.instrument_id);
            let status = builder.create_string(order_status_name(order.status));
            let intent_id = order.intent_id.as_ref().map(|v| builder.create_string(v));
            let market_id = order.market_id.as_ref().map(|v| builder.create_string(v));
            let remote_order_id = order
                .remote_order_id
                .as_ref()
                .map(|v| builder.create_string(v));
            let reason = (!order.reason.is_empty()).then(|| builder.create_string(&order.reason));
            let quantity = Decimal64::new(quantity_mantissa, quantity_scale);
            let filled = Decimal64::new(filled_mantissa, quantity_scale);
            let remaining_mantissa = quantity_mantissa
                .checked_sub(filled_mantissa)
                .filter(|value| *value >= 0)
                .ok_or_else(|| "filled quantity exceeds order quantity".to_string())?;
            let remaining = Decimal64::new(remaining_mantissa, quantity_scale);
            let limit = order
                .limit_price
                .as_ref()
                .map(|value| value.parts().map(|(m, s)| Decimal64::new(m, s)))
                .transpose()?;
            Ok(execution_fb::Order::create(
                &mut builder,
                &execution_fb::OrderArgs {
                    order_id: Some(order_id),
                    intent_id,
                    strategy_id: None,
                    account_id: Some(account_id),
                    instrument_id: Some(instrument_id),
                    market_id,
                    remote_order_id,
                    status: Some(status),
                    side: if order.side == OrderSide::Buy {
                        FbSide::BUY
                    } else {
                        FbSide::SELL
                    },
                    order_type: if order.order_type == OrderType::Market {
                        FbOrderType::MARKET
                    } else {
                        FbOrderType::LIMIT
                    },
                    quantity: Some(&quantity),
                    filled_quantity: Some(&filled),
                    remaining_quantity: Some(&remaining),
                    limit_price: limit.as_ref(),
                    average_fill_price: None,
                    created_at_unix_nanos: order.submitted_at_unix_nanos,
                    updated_at_unix_nanos: order.updated_at_unix_nanos,
                    reason,
                },
            ))
        })
        .collect::<Result<Vec<_>, String>>()?;
    let vector = builder.create_vector(&orders);
    let payload = execution_fb::Orders::create(
        &mut builder,
        &execution_fb::OrdersArgs {
            total_count: snapshot.orders.len() as u64,
            active_count: snapshot
                .orders
                .iter()
                .filter(|o| !o.status.terminal())
                .count() as u64,
            terminal_count: snapshot
                .orders
                .iter()
                .filter(|o| o.status.terminal())
                .count() as u64,
            orders: Some(vector),
        },
    );
    let header = make_header(
        &mut builder,
        actor_id,
        "execution.orders",
        "execution.events",
        snapshot,
    );
    let root = execution_fb::OrdersSnapshot::create(
        &mut builder,
        &execution_fb::OrdersSnapshotArgs {
            header: Some(header),
            payload: Some(payload),
        },
    );
    execution_fb::finish_orders_snapshot_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}

fn encode_intents(snapshot: &ExecutionSnapshot, actor_id: &str) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let intents = snapshot
        .intents
        .iter()
        .map(|state| encode_intent(&mut builder, state))
        .collect::<Result<Vec<_>, String>>()?;
    let vector = builder.create_vector(&intents);
    let payload = intent_fb::Intents::create(
        &mut builder,
        &intent_fb::IntentsArgs {
            total_count: snapshot.intents.len() as u64,
            active_count: snapshot
                .intents
                .iter()
                .filter(|s| !intent_terminal(s.status))
                .count() as u64,
            intents: Some(vector),
        },
    );
    let header = make_header(
        &mut builder,
        actor_id,
        "execution.intents",
        "execution.intent-events",
        snapshot,
    );
    let root = intent_fb::IntentSnapshot::create(
        &mut builder,
        &intent_fb::IntentSnapshotArgs {
            header: Some(header),
            payload: Some(payload),
        },
    );
    intent_fb::finish_intent_snapshot_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}

fn encode_intent<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    state: &IntentState,
) -> Result<flatbuffers::WIPOffset<intent_fb::Intent<'a>>, String> {
    let intent = &state.intent;
    let intent_id = builder.create_string(&intent.intent_id);
    let strategy_id = builder.create_string(&intent.strategy_id);
    let launch_id = builder.create_string(&intent.launch_id);
    let instance_id = builder.create_string(&intent.instance_id);
    let instrument_id = builder.create_string(&intent.instrument_id);
    let status = builder.create_string(intent_status_name(state.status));
    let market_id = intent.market_id.as_ref().map(|v| builder.create_string(v));
    let account_segment = builder.create_string(&intent.segment_key);
    let source_snapshot_id = intent
        .source_snapshot_id
        .as_ref()
        .map(|v| builder.create_string(v));
    let reason = (!state.reason.is_empty()).then(|| builder.create_string(&state.reason));
    let account_ids = intent
        .account_ids
        .iter()
        .map(|v| builder.create_string(v))
        .collect::<Vec<_>>();
    let order_ids = state
        .order_ids
        .iter()
        .map(|v| builder.create_string(v))
        .collect::<Vec<_>>();
    let account_ids = builder.create_vector(&account_ids);
    let order_ids = builder.create_vector(&order_ids);
    let (target_mantissa, target_scale) = intent.target_quantity.parts()?;
    let (completed_mantissa, completed_scale) = state.completed_quantity.parts()?;
    let target = Decimal64::new(target_mantissa, target_scale);
    let completed = Decimal64::new(completed_mantissa, completed_scale);
    Ok(intent_fb::Intent::create(
        builder,
        &intent_fb::IntentArgs {
            intent_id: Some(intent_id),
            strategy_id: Some(strategy_id),
            launch_id: Some(launch_id),
            instance_id: Some(instance_id),
            instrument_id: Some(instrument_id),
            market_id,
            status: Some(status),
            active: !intent_terminal(state.status),
            updated_at_unix_nanos: state.updated_at_unix_nanos,
            account_ids: Some(account_ids),
            account_segment: Some(account_segment),
            order_ids: Some(order_ids),
            target_quantity: Some(&target),
            completed_quantity: Some(&completed),
            source_snapshot_id,
            source_event_sequence: intent.source_event_sequence.unwrap_or_default(),
            reason,
        },
    ))
}

fn make_header<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    actor_id: &str,
    view_key: &str,
    stream_id: &str,
    snapshot: &ExecutionSnapshot,
) -> flatbuffers::WIPOffset<SnapshotHeader<'a>> {
    let now = now_unix_nanos();
    let snapshot_id = builder.create_string(&format!("{view_key}:{}", snapshot.generation));
    let view_key = builder.create_string(view_key);
    let owner_actor_id = builder.create_string(actor_id);
    let event_stream_id = builder.create_string(stream_id);
    SnapshotHeader::create(
        builder,
        &SnapshotHeaderArgs {
            snapshot_id: Some(snapshot_id),
            view_key: Some(view_key),
            owner_actor_id: Some(owner_actor_id),
            event_stream_id: Some(event_stream_id),
            workspace_id: None,
            launch_id: None,
            instance_id: None,
            event_sequence: snapshot.event_sequence,
            version: 1,
            generation: snapshot.generation,
            generated_at_unix_nanos: now,
            as_of_unix_nanos: now,
            complete: true,
        },
    )
}

fn intent_terminal(status: IntentStatus) -> bool {
    matches!(
        status,
        IntentStatus::Satisfied
            | IntentStatus::Canceled
            | IntentStatus::Failed
            | IntentStatus::Rejected
            | IntentStatus::Expired
            | IntentStatus::ReconciliationRequired
    )
}
fn intent_status_name(status: IntentStatus) -> &'static str {
    match status {
        IntentStatus::Accepted => "accepted",
        IntentStatus::Planning => "planning",
        IntentStatus::Planned => "planned",
        IntentStatus::Executing => "executing",
        IntentStatus::PartiallyFilled => "partially_filled",
        IntentStatus::CancelRequested => "cancel_requested",
        IntentStatus::Satisfied => "satisfied",
        IntentStatus::Rejected => "rejected",
        IntentStatus::Canceled => "canceled",
        IntentStatus::Expired => "expired",
        IntentStatus::Failed => "failed",
        IntentStatus::Compensating => "compensating",
        IntentStatus::ReconciliationRequired => "reconciliation_required",
    }
}
fn order_status_name(status: ExecutionOrderStatus) -> &'static str {
    match status {
        ExecutionOrderStatus::Pending => "pending",
        ExecutionOrderStatus::Submitting => "submitting",
        ExecutionOrderStatus::Accepted => "accepted",
        ExecutionOrderStatus::PartiallyFilled => "partially_filled",
        ExecutionOrderStatus::Filled => "filled",
        ExecutionOrderStatus::CancelRequested => "cancel_requested",
        ExecutionOrderStatus::Canceled => "canceled",
        ExecutionOrderStatus::Rejected => "rejected",
        ExecutionOrderStatus::Expired => "expired",
        ExecutionOrderStatus::Unknown => "unknown",
        ExecutionOrderStatus::Failed => "failed",
    }
}
fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

fn align_decimal_parts(
    left: i64,
    left_scale: u8,
    right: i64,
    right_scale: u8,
) -> Result<(i64, i64, u8), String> {
    let scale = left_scale.max(right_scale);
    Ok((
        rescale_decimal(left, left_scale, scale)?,
        rescale_decimal(right, right_scale, scale)?,
        scale,
    ))
}

fn rescale_decimal(value: i64, from_scale: u8, to_scale: u8) -> Result<i64, String> {
    let factor = 10_i128
        .checked_pow(u32::from(to_scale.saturating_sub(from_scale)))
        .ok_or_else(|| "decimal scale overflow".to_string())?;
    i64::try_from(i128::from(value) * factor)
        .map_err(|_| "decimal value cannot be represented by Decimal64".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use kairos_transport::SharedSnapshotReader;

    #[test]
    fn publishers_write_verifiable_snapshots() {
        let directory = tempfile::tempdir().unwrap();
        let snapshot = ExecutionSnapshot {
            actor_id: "execution:test".into(),
            generation: 1,
            event_sequence: 1,
            orders: vec![],
            events: vec![],
            fills: vec![],
            intents: vec![],
            intent_events: vec![],
            intent_idempotency: Default::default(),
        };
        let orders_path = directory.path().join("orders.snapshot");
        let mut orders =
            SharedExecutionSnapshotPublisher::create(&orders_path, 1024 * 1024, "execution:test")
                .unwrap();
        orders.publish(&snapshot).unwrap();
        assert_eq!(
            SharedSnapshotReader::open(&orders_path)
                .unwrap()
                .read_payload()
                .unwrap()
                .generation,
            1
        );
        let intents_path = directory.path().join("intents.snapshot");
        let mut intents =
            SharedIntentSnapshotPublisher::create(&intents_path, 1024 * 1024, "execution:test")
                .unwrap();
        intents.publish(&snapshot).unwrap();
        assert_eq!(
            SharedSnapshotReader::open(&intents_path)
                .unwrap()
                .read_payload()
                .unwrap()
                .generation,
            1
        );
    }

    #[test]
    fn order_encoding_aligns_canonical_zero_with_fractional_quantity() {
        let directory = tempfile::tempdir().unwrap();
        let snapshot = ExecutionSnapshot {
            actor_id: "execution:test".into(),
            generation: 1,
            event_sequence: 1,
            orders: vec![crate::model::ExecutionOrder {
                order_id: "order-1".into(),
                plan_id: None,
                leg_id: None,
                intent_id: None,
                account_id: "paper".into(),
                segment_key: "spot".into(),
                instrument_id: "BTCUSDT".into(),
                market_id: None,
                side: OrderSide::Buy,
                order_type: OrderType::Limit,
                quantity: crate::model::Decimal("1.25".into()),
                limit_price: Some(crate::model::Decimal("100.01".into())),
                remote_order_id: None,
                filled_quantity: crate::model::Decimal("0".into()),
                status: ExecutionOrderStatus::Accepted,
                submitted_at_unix_nanos: 1,
                updated_at_unix_nanos: 1,
                reason: String::new(),
            }],
            events: vec![],
            fills: vec![],
            intents: vec![],
            intent_events: vec![],
            intent_idempotency: Default::default(),
        };
        let path = directory.path().join("orders.snapshot");
        let mut publisher =
            SharedExecutionSnapshotPublisher::create(&path, 1024 * 1024, "execution:test").unwrap();

        publisher.publish(&snapshot).unwrap();
    }
}
