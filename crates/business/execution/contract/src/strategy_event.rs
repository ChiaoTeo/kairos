use std::time::{SystemTime, UNIX_EPOCH};

use flatbuffers::FlatBufferBuilder;
use kairos_protocol::generated::kairos::{
    common::v_1::{Decimal64, MessageHeader, MessageHeaderArgs, OrderType, Side},
    execution::v_1 as execution_fb,
};
use kairos_protocol::InstanceIdentity;

use crate::model::{
    ExecutionFill, ExecutionOrder, ExecutionOrderStatus, IntentState, OrderSide,
    OrderType as ContractOrderType,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutionStrategyEvent {
    pub sequence: u64,
    pub occurred_at_unix_nanos: u64,
    pub changes: Vec<ExecutionStrategyChange>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ExecutionStrategyChange {
    Intent(IntentState),
    Order {
        strategy_id: String,
        order: ExecutionOrder,
    },
    Fill {
        strategy_id: String,
        account_id: String,
        intent_id: Option<String>,
        market_id: Option<String>,
        remote_order_id: Option<String>,
        side: OrderSide,
        fill: ExecutionFill,
    },
}

pub struct AeronExecutionEventPublisher {
    publisher: kairos_transport::AeronBytePublisher,
    producer_id: String,
    identity: InstanceIdentity,
}

impl AeronExecutionEventPublisher {
    pub fn connect(
        aeron_dir: Option<&str>,
        channel: &str,
        stream_id: i32,
        producer_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> Result<Self, String> {
        Ok(Self {
            publisher: kairos_transport::AeronBytePublisher::connect(
                aeron_dir, channel, stream_id,
            )?,
            producer_id: producer_id.into(),
            identity,
        })
    }

    pub fn publish(&mut self, event: &ExecutionStrategyEvent) -> Result<(), String> {
        self.publisher.publish(&encode_event(
            &self.producer_id,
            &self.identity,
            event.sequence,
            event.occurred_at_unix_nanos,
            &event.changes,
        )?)
    }
}

fn encode_event(
    producer_id_value: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    occurred_at_unix_nanos: u64,
    changes: &[ExecutionStrategyChange],
) -> Result<Vec<u8>, String> {
    let publish_time_unix_nanos = now_unix_nanos();
    let mut builder = FlatBufferBuilder::new();
    let offsets = changes
        .iter()
        .map(|change| encode_change(&mut builder, change))
        .collect::<Result<Vec<_>, String>>()?;
    let changes = builder.create_vector(&offsets);
    let message_id = builder.create_string(&format!("execution:{sequence}"));
    let stream_id = builder.create_string("execution.events");
    let producer_id = builder.create_string(producer_id_value);
    let workspace_id = non_empty_string(&mut builder, &identity.workspace_id);
    let launch_id = non_empty_string(&mut builder, &identity.launch_id);
    let instance_id = non_empty_string(&mut builder, &identity.instance_id);
    let header = MessageHeader::create(
        &mut builder,
        &MessageHeaderArgs {
            message_id: Some(message_id),
            stream_id: Some(stream_id),
            producer_id: Some(producer_id),
            workspace_id,
            launch_id,
            instance_id,
            sequence,
            event_time_unix_nanos: occurred_at_unix_nanos,
            publish_time_unix_nanos,
        },
    );
    let event = execution_fb::ExecutionEventMessage::create(
        &mut builder,
        &execution_fb::ExecutionEventMessageArgs {
            header: Some(header),
            changes: Some(changes),
            occurred_at_unix_nanos,
        },
    );
    execution_fb::finish_execution_event_message_buffer(&mut builder, event);
    Ok(builder.finished_data().to_vec())
}

fn encode_change<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    change: &ExecutionStrategyChange,
) -> Result<flatbuffers::WIPOffset<execution_fb::ExecutionChange<'a>>, String> {
    match change {
        ExecutionStrategyChange::Intent(state) => {
            let kind = builder.create_string("intent_update");
            let strategy_id = builder.create_string(&state.intent.strategy_id);
            let account_id = state
                .intent
                .account_ids
                .first()
                .map(|value| builder.create_string(value));
            let intent = crate::encoding::encode_intent(builder, state)?;
            Ok(execution_fb::ExecutionChange::create(
                builder,
                &execution_fb::ExecutionChangeArgs {
                    kind: Some(kind),
                    strategy_id: Some(strategy_id),
                    account_id,
                    intent: Some(intent),
                    ..Default::default()
                },
            ))
        }
        ExecutionStrategyChange::Order { strategy_id, order } => {
            let kind = builder.create_string("order_update");
            let strategy_id = builder.create_string(strategy_id);
            let account_id = builder.create_string(&order.account_id);
            let order = encode_order(builder, order, strategy_id)?;
            Ok(execution_fb::ExecutionChange::create(
                builder,
                &execution_fb::ExecutionChangeArgs {
                    kind: Some(kind),
                    strategy_id: Some(strategy_id),
                    account_id: Some(account_id),
                    order: Some(order),
                    ..Default::default()
                },
            ))
        }
        ExecutionStrategyChange::Fill {
            strategy_id,
            account_id,
            intent_id,
            market_id,
            remote_order_id,
            side,
            fill,
        } => {
            let kind = builder.create_string("fill");
            let strategy = builder.create_string(strategy_id);
            let account = builder.create_string(account_id);
            let fill_value = encode_fill(
                builder,
                fill,
                strategy,
                account,
                intent_id.as_deref(),
                market_id.as_deref(),
                remote_order_id.as_deref(),
                *side,
            )?;
            Ok(execution_fb::ExecutionChange::create(
                builder,
                &execution_fb::ExecutionChangeArgs {
                    kind: Some(kind),
                    strategy_id: Some(strategy),
                    account_id: Some(account),
                    fill: Some(fill_value),
                    ..Default::default()
                },
            ))
        }
    }
}

fn encode_order<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    order: &ExecutionOrder,
    strategy_id: flatbuffers::WIPOffset<&'a str>,
) -> Result<flatbuffers::WIPOffset<execution_fb::Order<'a>>, String> {
    let order_id = builder.create_string(&order.order_id);
    let account_id = builder.create_string(&order.account_id);
    let instrument_id = builder.create_string(&order.instrument_id);
    let status = builder.create_string(order_status(order.status));
    let intent_id = order
        .intent_id
        .as_ref()
        .map(|value| builder.create_string(value));
    let market_id = order
        .market_id
        .as_ref()
        .map(|value| builder.create_string(value));
    let remote_order_id = order
        .remote_order_id
        .as_ref()
        .map(|value| builder.create_string(value));
    let reason = (!order.reason.is_empty()).then(|| builder.create_string(&order.reason));
    let quantity = decimal(&order.quantity)?;
    let filled = decimal(&order.filled_quantity)?;
    let remaining = decimal_difference(&order.quantity, &order.filled_quantity)?;
    let limit_price = order.limit_price.as_ref().map(decimal).transpose()?;
    Ok(execution_fb::Order::create(
        builder,
        &execution_fb::OrderArgs {
            order_id: Some(order_id),
            intent_id,
            strategy_id: Some(strategy_id),
            account_id: Some(account_id),
            instrument_id: Some(instrument_id),
            market_id,
            remote_order_id,
            status: Some(status),
            side: if order.side == OrderSide::Buy {
                Side::BUY
            } else {
                Side::SELL
            },
            order_type: if order.order_type == ContractOrderType::Market {
                OrderType::MARKET
            } else {
                OrderType::LIMIT
            },
            quantity: Some(&quantity),
            filled_quantity: Some(&filled),
            remaining_quantity: Some(&remaining),
            limit_price: limit_price.as_ref(),
            average_fill_price: None,
            created_at_unix_nanos: order.submitted_at_unix_nanos,
            updated_at_unix_nanos: order.updated_at_unix_nanos,
            reason,
        },
    ))
}

#[allow(clippy::too_many_arguments)]
fn encode_fill<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    fill: &ExecutionFill,
    strategy_id: flatbuffers::WIPOffset<&'a str>,
    account_id: flatbuffers::WIPOffset<&'a str>,
    intent_id_value: Option<&str>,
    market_id_value: Option<&str>,
    remote_order_id_value: Option<&str>,
    side: OrderSide,
) -> Result<flatbuffers::WIPOffset<execution_fb::Fill<'a>>, String> {
    let fill_id = builder.create_string(&fill.fill_id);
    let order_id = builder.create_string(&fill.order_id);
    let instrument_id = builder.create_string(&fill.instrument_id);
    let intent_id = intent_id_value.map(|value| builder.create_string(value));
    let market_id = market_id_value.map(|value| builder.create_string(value));
    let remote_order_id = remote_order_id_value.map(|value| builder.create_string(value));
    let quantity = decimal(&fill.quantity)?;
    let price = decimal(&fill.price)?;
    let fee = decimal(&fill.fee)?;
    Ok(execution_fb::Fill::create(
        builder,
        &execution_fb::FillArgs {
            fill_id: Some(fill_id),
            trade_id: None,
            order_id: Some(order_id),
            intent_id,
            strategy_id: Some(strategy_id),
            account_id: Some(account_id),
            instrument_id: Some(instrument_id),
            market_id,
            remote_order_id,
            side: if side == OrderSide::Buy {
                Side::BUY
            } else {
                Side::SELL
            },
            quantity: Some(&quantity),
            price: Some(&price),
            fee: Some(&fee),
            fee_asset_id: None,
            notional: None,
            occurred_at_unix_nanos: fill.occurred_at_unix_nanos,
        },
    ))
}

fn decimal(value: &crate::model::Decimal) -> Result<Decimal64, String> {
    let (mantissa, scale) = value.parts()?;
    Ok(Decimal64::new(mantissa, scale))
}

fn decimal_difference(
    total: &crate::model::Decimal,
    used: &crate::model::Decimal,
) -> Result<Decimal64, String> {
    let (total_mantissa, total_scale) = total.parts()?;
    let (used_mantissa, used_scale) = used.parts()?;
    let scale = total_scale.max(used_scale);
    let total_mantissa = i128::from(total_mantissa)
        .checked_mul(10_i128.pow(u32::from(scale - total_scale)))
        .ok_or_else(|| "Execution remaining quantity overflowed".to_string())?;
    let used_mantissa = i128::from(used_mantissa)
        .checked_mul(10_i128.pow(u32::from(scale - used_scale)))
        .ok_or_else(|| "Execution remaining quantity overflowed".to_string())?;
    let remaining = total_mantissa
        .checked_sub(used_mantissa)
        .filter(|value| *value >= 0)
        .ok_or_else(|| "filled quantity exceeds order quantity".to_string())?;
    Ok(Decimal64::new(
        i64::try_from(remaining).map_err(|_| "Execution remaining quantity overflowed")?,
        scale,
    ))
}

fn order_status(value: ExecutionOrderStatus) -> &'static str {
    match value {
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

fn non_empty_string<'a, 'b, A: flatbuffers::Allocator + 'a>(
    builder: &'b mut FlatBufferBuilder<'a, A>,
    value: &str,
) -> Option<flatbuffers::WIPOffset<&'a str>> {
    (!value.is_empty()).then(|| builder.create_string(value))
}

fn now_unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .try_into()
        .unwrap_or(u64::MAX)
}
