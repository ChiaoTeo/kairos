use super::*;

pub(crate) fn encode_business_change(
    actor_id: &str,
    producer_incarnation: u64,
    identity: &InstanceIdentity,
    sequence: u64,
    occurred_at: u64,
    index: usize,
    change: &crate::application::ExecutionBusinessChange,
) -> Result<Vec<Vec<u8>>, String> {
    use crate::application::ExecutionBusinessChange;
    let event_id = format!("execution:{sequence}:{index}");
    let context = EncodeContext::event(
        actor_id,
        producer_incarnation,
        identity.clone(),
        sequence,
        event_id,
    )?;
    match change {
        ExecutionBusinessChange::Intent { state, event } => {
            let mut builder = FlatBufferBuilder::new();
            let metadata = event_metadata(&mut builder, &context, occurred_at);
            let intent_id = builder.create_string(&state.intent.intent_id.to_string());
            let intent = encode_execution_intent(&mut builder, &state.intent)?;
            match state.status {
                crate::application::IntentStatus::Rejected if event.previous_status.is_none() => {
                    let codes = builder.create_vector(&[fb::IntentRejectionCode::UNSPECIFIED]);
                    let detail = builder.create_string(&state.reason);
                    let details = builder.create_vector(&[detail]);
                    let root = fb::IntentRejected::create(
                        &mut builder,
                        &fb::IntentRejectedArgs {
                            metadata: Some(metadata),
                            intent_id: Some(intent_id),
                            codes: Some(codes),
                            details: Some(details),
                            lifecycle: fb::IntentLifecycle::REJECTED,
                            intent: Some(intent),
                        },
                    );
                    fb::finish_intent_rejected_buffer(&mut builder, root);
                },
                crate::application::IntentStatus::Accepted if event.previous_status.is_none() => {
                    let root = fb::IntentAccepted::create(
                        &mut builder,
                        &fb::IntentAcceptedArgs {
                            metadata: Some(metadata),
                            intent_id: Some(intent_id),
                            lifecycle: fb::IntentLifecycle::ACCEPTED,
                            intent: Some(intent),
                        },
                    );
                    fb::finish_intent_accepted_buffer(&mut builder, root);
                },
                _ => {
                    let order_ids = event
                        .order_ids
                        .iter()
                        .map(|value| builder.create_string(value.as_str()))
                        .collect::<Vec<_>>();
                    let order_ids = builder.create_vector(&order_ids);
                    let completed_quantity = decimal(event.completed_quantity);
                    let reason =
                        (!event.reason.is_empty()).then(|| builder.create_string(&event.reason));
                    let dependency_evidence =
                        encode_dependency_evidence(&mut builder, &event.dependency_watermarks);
                    let root = fb::IntentLifecycleChanged::create(
                        &mut builder,
                        &fb::IntentLifecycleChangedArgs {
                            metadata: Some(metadata),
                            intent: Some(intent),
                            previous_lifecycle: event
                                .previous_status
                                .map(intent_lifecycle)
                                .unwrap_or(fb::IntentLifecycle::UNSPECIFIED),
                            lifecycle: intent_lifecycle(event.status),
                            order_ids: Some(order_ids),
                            completed_quantity: Some(&completed_quantity),
                            reason,
                            dependency_evidence: Some(dependency_evidence),
                        },
                    );
                    fb::finish_intent_lifecycle_changed_buffer(&mut builder, root);
                },
            }
            let mut payloads = vec![builder.finished_data().to_vec()];
            if event.previous_status.is_none() {
                if let Some(plan) = state.plan.as_ref() {
                    payloads.push(encode_plan_event(&context, occurred_at, plan)?);
                }
            }
            Ok(payloads)
        },
        ExecutionBusinessChange::Order { order, .. } => {
            Ok(vec![encode_order_event(&context, occurred_at, order)?])
        },
        ExecutionBusinessChange::Fill {
            strategy_id,
            account_id,
            intent_id,
            remote_order_id,
            fill,
            ..
        } => Ok(vec![encode_fill_event(
            &context,
            occurred_at,
            strategy_id,
            account_id,
            intent_id.as_deref(),
            remote_order_id.as_deref(),
            fill,
        )?]),
    }
}

fn encode_order_event(
    context: &EncodeContext,
    occurred_at: u64,
    order: &ExecutionOrder,
) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let metadata = event_metadata(&mut builder, context, occurred_at);
    let order_offset = encode_order_state(&mut builder, order)?;
    macro_rules! finish_order {
        ($root:ident, $args:ident, $finish:ident) => {{
            let reason = (!order.reason.is_empty()).then(|| builder.create_string(&order.reason));
            let root = fb::$root::create(
                &mut builder,
                &fb::$args {
                    metadata: Some(metadata),
                    order: Some(order_offset),
                    reason,
                },
            );
            fb::$finish(&mut builder, root);
        }};
    }
    match order.status {
        ExecutionOrderStatus::Pending | ExecutionOrderStatus::Submitting => {
            let root = fb::OrderSubmitted::create(
                &mut builder,
                &fb::OrderSubmittedArgs {
                    metadata: Some(metadata),
                    order: Some(order_offset),
                },
            );
            fb::finish_order_submitted_buffer(&mut builder, root);
        },
        ExecutionOrderStatus::Accepted
        | ExecutionOrderStatus::PartiallyFilled
        | ExecutionOrderStatus::Filled => {
            let root = fb::OrderAccepted::create(
                &mut builder,
                &fb::OrderAcceptedArgs {
                    metadata: Some(metadata),
                    order: Some(order_offset),
                },
            );
            fb::finish_order_accepted_buffer(&mut builder, root);
        },
        ExecutionOrderStatus::Rejected
        | ExecutionOrderStatus::Unknown
        | ExecutionOrderStatus::Failed => {
            finish_order!(
                OrderRejected,
                OrderRejectedArgs,
                finish_order_rejected_buffer
            );
        },
        ExecutionOrderStatus::Canceled | ExecutionOrderStatus::CancelRequested => {
            finish_order!(
                OrderCanceled,
                OrderCanceledArgs,
                finish_order_canceled_buffer
            );
        },
        ExecutionOrderStatus::Expired => {
            finish_order!(OrderExpired, OrderExpiredArgs, finish_order_expired_buffer);
        },
    }
    Ok(builder.finished_data().to_vec())
}

fn encode_plan_event(
    context: &EncodeContext,
    occurred_at: u64,
    plan: &crate::domain::ExecutionPlan,
) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let mut plan_context = context.clone();
    let event_id = plan_context
        .event_id
        .as_ref()
        .ok_or_else(|| "plan event requires an event id".to_owned())?;
    plan_context.common.event_id = Some(
        kairos_primitives::runtime::EventId::new(format!("{event_id}:plan"))
            .map_err(|error| error.to_string())?,
    );
    let metadata = event_metadata(&mut builder, &plan_context, occurred_at);
    let plan_offset = encode_plan(&mut builder, plan)?;
    let root = fb::PlanCreated::create(
        &mut builder,
        &fb::PlanCreatedArgs {
            metadata: Some(metadata),
            plan: Some(plan_offset),
        },
    );
    fb::finish_plan_created_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}

fn encode_fill_event(
    context: &EncodeContext,
    occurred_at: u64,
    strategy_id: &str,
    account_id: &str,
    change_intent_id: Option<&str>,
    remote_order_id: Option<&str>,
    fill: &crate::domain::ExecutionFill,
) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let metadata = event_metadata(&mut builder, context, occurred_at);
    let fill_id = builder.create_string(&fill.fill_id.to_string());
    let order_id = builder.create_string(&fill.order_id.to_string());
    let intent_id_value = change_intent_id
        .map(str::to_owned)
        .or_else(|| fill.intent_id.as_ref().map(ToString::to_string))
        .unwrap_or_default();
    let intent_id = builder.create_string(&intent_id_value);
    let plan_id = builder.create_string(
        &fill
            .plan_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let leg_id = builder.create_string(
        &fill
            .leg_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let instrument_id = builder.create_string(&fill.instrument_id.to_string());
    let market_id = builder.create_string(
        &fill
            .execution_market_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let quantity = decimal(fill.quantity);
    let price = decimal(fill.price);
    let fee = decimal(fill.fee);
    let strategy_id = builder.create_string(strategy_id);
    let account_id = builder.create_string(account_id);
    let segment_key = builder.create_string("");
    let execution_route_id = builder.create_string("");
    let remote_order_id = fill
        .remote_order_id
        .as_ref()
        .map(|value| builder.create_string(value.as_str()))
        .or_else(|| remote_order_id.map(|value| builder.create_string(value)));
    let reported_broker_id = fill
        .reported_broker_id
        .as_ref()
        .map(|value| builder.create_string(value));
    let execution_channel = fill
        .execution_channel
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    let order_entry_symbol = fill
        .order_entry_symbol
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    let fee_asset_id = fill
        .fee_currency
        .as_ref()
        .map(|value| builder.create_string(&value.to_string()));
    let payload = fb::Fill::create(
        &mut builder,
        &fb::FillArgs {
            fill_id: Some(fill_id),
            trade_id: None,
            order_id: Some(order_id),
            intent_id: Some(intent_id),
            plan_id: Some(plan_id),
            leg_id: Some(leg_id),
            strategy_id: Some(strategy_id),
            account_id: Some(account_id),
            segment_key: Some(segment_key),
            instrument_id: Some(instrument_id),
            market_id: Some(market_id),
            execution_route_id: Some(execution_route_id),
            remote_order_id,
            reported_broker_id,
            execution_channel,
            provider_symbol: order_entry_symbol,
            side: match fill.side {
                OrderSide::Buy => kairos_protocol::generated::kairos::common::v_2::Side::BUY,
                OrderSide::Sell => kairos_protocol::generated::kairos::common::v_2::Side::SELL,
            },
            quantity: Some(&quantity),
            price: Some(&price),
            fee: Some(&fee),
            fee_asset_id,
            notional: None,
            source_filled_at_unix_nanos: fill.occurred_at_unix_nanos.get(),
        },
    );
    let root = fb::FillRecorded::create(
        &mut builder,
        &fb::FillRecordedArgs {
            metadata: Some(metadata),
            fill: Some(payload),
        },
    );
    fb::finish_fill_recorded_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}
