use super::*;

/// Aeron publisher boundary for v2 event frames.
pub struct AeronExecutionEventPublisher {
    sender: std::sync::mpsc::SyncSender<(Vec<u8>, std::sync::mpsc::Sender<Result<(), String>>)>,
    worker: Option<std::thread::JoinHandle<()>>,
    actor_id: String,
    identity: InstanceIdentity,
}

impl AeronExecutionEventPublisher {
    pub fn connect(
        aeron_dir: Option<&str>,
        channel: &str,
        stream_id: i32,
        actor_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> Result<Self, String> {
        let aeron_dir = aeron_dir.map(str::to_owned);
        let channel = channel.to_owned();
        let (sender, receiver) = std::sync::mpsc::sync_channel::<(
            Vec<u8>,
            std::sync::mpsc::Sender<Result<(), String>>,
        )>(64);
        let (ready_sender, ready_receiver) = std::sync::mpsc::sync_channel(1);
        let worker = std::thread::Builder::new()
            .name("execution-aeron-publisher".into())
            .spawn(move || {
                let publisher = kairos_transport::AeronBytePublisher::connect(
                    aeron_dir.as_deref(),
                    &channel,
                    stream_id,
                );
                let publisher = match publisher {
                    Ok(publisher) => {
                        let _ = ready_sender.send(Ok(()));
                        publisher
                    }
                    Err(error) => {
                        let _ = ready_sender.send(Err(error.to_string()));
                        return;
                    }
                };
                while let Ok((payload, reply)) = receiver.recv() {
                    let _ = reply.send(
                        publisher
                            .publish(&payload)
                            .map(|_| ())
                            .map_err(|error| error.to_string()),
                    );
                }
            })
            .map_err(|error| error.to_string())?;
        ready_receiver.recv().map_err(|error| error.to_string())??;
        Ok(Self {
            sender,
            worker: Some(worker),
            actor_id: actor_id.into(),
            identity,
        })
    }
}

impl AeronExecutionEventPublisher {
    pub fn publish(&mut self, event: &ExecutionBusinessEvent) -> Result<(), String> {
        for (index, change) in event.changes.iter().enumerate() {
            let bytes = encode_business_change(
                &self.actor_id,
                &self.identity,
                event.sequence.get(),
                event.occurred_at_unix_nanos.get(),
                index,
                change,
            )?;
            for payload in bytes {
                let (reply, result) = std::sync::mpsc::channel();
                self.sender
                    .send((payload, reply))
                    .map_err(|error| error.to_string())?;
                result.recv().map_err(|error| error.to_string())??;
            }
        }
        Ok(())
    }
}

impl Drop for AeronExecutionEventPublisher {
    fn drop(&mut self) {
        let (replacement, _receiver) = std::sync::mpsc::sync_channel(1);
        drop(std::mem::replace(&mut self.sender, replacement));
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

fn encode_business_change(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    occurred_at: u64,
    index: usize,
    change: &crate::application::ExecutionBusinessChange,
) -> Result<Vec<Vec<u8>>, String> {
    use crate::application::ExecutionBusinessChange;
    let event_id = format!("execution:{sequence}:{index}");
    let context = EncodeContext::event(actor_id, identity.clone(), sequence, event_id);
    match change {
        ExecutionBusinessChange::Intent(value) => {
            let mut builder = FlatBufferBuilder::new();
            let metadata = event_metadata(&mut builder, &context, occurred_at);
            let intent_id = builder.create_string(&value.intent.intent_id.to_string());
            if matches!(value.status, crate::application::IntentStatus::Rejected) {
                let codes = builder.create_vector(&[fb::IntentRejectionCode::UNSPECIFIED]);
                let detail = builder.create_string(&value.reason);
                let details = builder.create_vector(&[detail]);
                let root = fb::IntentRejected::create(
                    &mut builder,
                    &fb::IntentRejectedArgs {
                        metadata: Some(metadata),
                        intent_id: Some(intent_id),
                        codes: Some(codes),
                        details: Some(details),
                    },
                );
                fb::finish_intent_rejected_buffer(&mut builder, root);
            } else {
                let root = fb::IntentAccepted::create(
                    &mut builder,
                    &fb::IntentAcceptedArgs {
                        metadata: Some(metadata),
                        intent_id: Some(intent_id),
                        lifecycle: intent_lifecycle(value.status),
                    },
                );
                fb::finish_intent_accepted_buffer(&mut builder, root);
            }
            let mut payloads = vec![builder.finished_data().to_vec()];
            if let Some(plan) = value.plan.as_ref() {
                payloads.push(encode_plan_event(&context, occurred_at, plan)?);
            }
            Ok(payloads)
        }
        ExecutionBusinessChange::Order { order, .. } => {
            Ok(vec![encode_order_event(&context, occurred_at, order)?])
        }
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
        }
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
        }
        ExecutionOrderStatus::Rejected
        | ExecutionOrderStatus::Unknown
        | ExecutionOrderStatus::Failed => {
            finish_order!(
                OrderRejected,
                OrderRejectedArgs,
                finish_order_rejected_buffer
            );
        }
        ExecutionOrderStatus::Canceled | ExecutionOrderStatus::CancelRequested => {
            finish_order!(
                OrderCanceled,
                OrderCanceledArgs,
                finish_order_canceled_buffer
            );
        }
        ExecutionOrderStatus::Expired => {
            finish_order!(OrderExpired, OrderExpiredArgs, finish_order_expired_buffer);
        }
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
    plan_context.event_id.push_str(":plan");
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
    let reported_provider_id = fill
        .reported_provider_id
        .as_ref()
        .map(|value| builder.create_string(value));
    let provider_product = fill
        .provider_product
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    let provider_symbol = fill
        .provider_symbol
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
            reported_provider_id,
            provider_product,
            provider_symbol,
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
