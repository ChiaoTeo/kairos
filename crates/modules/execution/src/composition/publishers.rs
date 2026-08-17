//! Concrete Execution v2 publication adapters.
//!
//! The application layer still owns business values.  This module is the
//! composition boundary where those values will be encoded into the v2
//! contract and sent over Aeron or mmap.  Keeping the adapters here prevents
//! generated FlatBuffers types from leaking into the Actor.

use std::path::{Path, PathBuf};

use crate::application::{ExecutionBusinessEvent, ExecutionCurrentView};
use crate::domain::{
    CommitmentBasis, CommitmentResource, CommitmentStatus, ExecutionOrder, ExecutionOrderStatus,
    OrderCommitment, OrderSide, OrderType, RiskReservationEvidence, RiskReservationSagaStatus,
};
use flatbuffers::FlatBufferBuilder;
use kairos_execution_contract::{
    event_metadata, view_metadata, EncodeContext, ExecutionViewKey, ExecutionViewKind,
    ExecutionViewPublisher,
};
use kairos_protocol::generated::kairos::execution::v_2 as fb;
use kairos_protocol::InstanceIdentity;
use kairos_transport::SnapshotEnvelopeMetadata;

const DEFAULT_SLOT_SIZE: usize = 4 * 1024 * 1024;

pub struct SharedExecutionSnapshotPublisher {
    root: PathBuf,
    slot_size: usize,
    actor_id: String,
    identity: InstanceIdentity,
    publisher: Option<ExecutionViewPublisher>,
    current_publisher: Option<ExecutionViewPublisher>,
    producer_incarnation: u64,
}

impl SharedExecutionSnapshotPublisher {
    pub fn create(
        path: impl AsRef<Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
    ) -> Result<Self, String> {
        Self::create_with_identity(path, slot_size, actor_id, InstanceIdentity::default())
    }

    pub fn create_with_identity(
        path: impl AsRef<Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> Result<Self, String> {
        let path = path.as_ref();
        Ok(Self {
            root: snapshot_root(path),
            slot_size: nonzero_slot_size(slot_size),
            actor_id: actor_id.into(),
            identity,
            publisher: None,
            current_publisher: None,
            producer_incarnation: kairos_workspace::ProducerIncarnation::allocate().get(),
        })
    }

    pub fn publish(&mut self, snapshot: &ExecutionCurrentView) -> Result<(), String> {
        let generation = snapshot.generation.get();
        let key = self.key(ExecutionViewKind::ActiveOrders)?;
        let bytes =
            encode_active_orders(&self.actor_id, &self.identity, generation, &key, snapshot)?;
        let metadata = envelope_metadata(self.producer_incarnation, snapshot);
        self.ensure_publisher(key)?
            .publish(metadata, &bytes)
            .map_err(|e| e.to_string())?;

        let current_key = self.key(ExecutionViewKind::CurrentExecution)?;
        let current_bytes = encode_current_execution(
            &self.actor_id,
            &self.identity,
            generation,
            &current_key,
            snapshot,
        )?;
        if self.current_publisher.is_none() {
            self.current_publisher = Some(
                ExecutionViewPublisher::create(&self.root, current_key, self.slot_size)
                    .map_err(|error| error.to_string())?,
            );
        }
        self.current_publisher
            .as_mut()
            .expect("current publisher was inserted")
            .publish(metadata, &current_bytes)
            .map_err(|error| error.to_string())
    }

    fn key(&self, kind: ExecutionViewKind) -> Result<ExecutionViewKey, String> {
        ExecutionViewKey::new(
            self.identity.workspace_id.clone(),
            kind,
            Some(self.identity.launch_id.clone()),
            Some(self.identity.instance_id.clone()),
        )
        .map_err(|e| e.to_string())
    }

    fn ensure_publisher(
        &mut self,
        key: ExecutionViewKey,
    ) -> Result<&mut ExecutionViewPublisher, String> {
        if self.publisher.is_none() {
            self.publisher = Some(
                ExecutionViewPublisher::create(&self.root, key, self.slot_size)
                    .map_err(|e| e.to_string())?,
            );
        }
        Ok(self.publisher.as_mut().expect("publisher was inserted"))
    }
}

impl crate::application::ExecutionSnapshotPublisher for SharedExecutionSnapshotPublisher {
    fn publish(&mut self, snapshot: &ExecutionCurrentView) -> Result<(), String> {
        Self::publish(self, snapshot)
    }
}

pub struct SharedIntentSnapshotPublisher {
    root: PathBuf,
    slot_size: usize,
    actor_id: String,
    identity: InstanceIdentity,
    publisher: Option<ExecutionViewPublisher>,
    producer_incarnation: u64,
}

impl SharedIntentSnapshotPublisher {
    pub fn create(
        path: impl AsRef<Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
    ) -> Result<Self, String> {
        Self::create_with_identity(path, slot_size, actor_id, InstanceIdentity::default())
    }

    pub fn create_with_identity(
        path: impl AsRef<Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> Result<Self, String> {
        let path = path.as_ref();
        Ok(Self {
            root: snapshot_root(path),
            slot_size: nonzero_slot_size(slot_size),
            actor_id: actor_id.into(),
            identity,
            publisher: None,
            producer_incarnation: kairos_workspace::ProducerIncarnation::allocate().get(),
        })
    }

    pub fn publish(&mut self, snapshot: &ExecutionCurrentView) -> Result<(), String> {
        let generation = snapshot.generation.get();
        let key = ExecutionViewKey::new(
            self.identity.workspace_id.clone(),
            ExecutionViewKind::ActiveIntents,
            Some(self.identity.launch_id.clone()),
            Some(self.identity.instance_id.clone()),
        )
        .map_err(|e| e.to_string())?;
        let bytes =
            encode_active_intents(&self.actor_id, &self.identity, generation, &key, snapshot)?;
        if self.publisher.is_none() {
            self.publisher = Some(
                ExecutionViewPublisher::create(&self.root, key, self.slot_size)
                    .map_err(|e| e.to_string())?,
            );
        }
        self.publisher
            .as_mut()
            .expect("publisher was inserted")
            .publish(
                envelope_metadata(self.producer_incarnation, snapshot),
                &bytes,
            )
            .map_err(|e| e.to_string())
    }
}

fn envelope_metadata(
    producer_incarnation: u64,
    snapshot: &ExecutionCurrentView,
) -> SnapshotEnvelopeMetadata {
    SnapshotEnvelopeMetadata {
        resource_epoch: 1,
        producer_incarnation,
        generation: snapshot.generation.get(),
        applied_event_sequence: snapshot.event_sequence.get(),
        published_at_unix_nanos: now_unix_nanos(),
    }
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u64::MAX as u128) as u64
}

impl crate::application::IntentSnapshotPublisher for SharedIntentSnapshotPublisher {
    fn publish(&mut self, snapshot: &ExecutionCurrentView) -> Result<(), String> {
        Self::publish(self, snapshot)
    }
}

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

impl crate::application::ExecutionEventPublisher for AeronExecutionEventPublisher {
    fn publish(&mut self, event: &ExecutionBusinessEvent) -> Result<(), String> {
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
    let execution_access_id = builder.create_string("");
    let remote_order_id = remote_order_id.map(|value| builder.create_string(value));
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
            execution_access_id: Some(execution_access_id),
            remote_order_id,
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

fn snapshot_root(path: impl AsRef<Path>) -> PathBuf {
    path.as_ref()
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
        .unwrap_or_else(|| Path::new("."))
        .to_path_buf()
}

fn nonzero_slot_size(slot_size: usize) -> usize {
    if slot_size == 0 {
        DEFAULT_SLOT_SIZE
    } else {
        slot_size
    }
}

fn encode_active_orders(
    actor_id: &str,
    identity: &InstanceIdentity,
    generation: u64,
    key: &ExecutionViewKey,
    snapshot: &ExecutionCurrentView,
) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let context = EncodeContext::view(
        actor_id,
        actor_id,
        identity.clone(),
        generation,
        key.canonical_key(),
    );
    let metadata = view_metadata(
        &mut builder,
        &context,
        key,
        0,
        snapshot.event_sequence.get(),
    );
    let order_offsets = snapshot
        .orders
        .iter()
        .map(|order| encode_order_state(&mut builder, order))
        .collect::<Result<Vec<_>, _>>()?;
    let orders = builder.create_vector(&order_offsets);
    let commitment_offsets = snapshot
        .commitments
        .iter()
        .map(|commitment| encode_commitment_state(&mut builder, commitment))
        .collect::<Result<Vec<_>, _>>()?;
    let commitments = builder.create_vector(&commitment_offsets);
    let reservation_offsets = snapshot
        .risk_reservations
        .iter()
        .map(|reservation| encode_risk_reservation_state(&mut builder, reservation))
        .collect::<Result<Vec<_>, _>>()?;
    let risk_reservations = builder.create_vector(&reservation_offsets);
    let root = fb::ActiveOrdersView::create(
        &mut builder,
        &fb::ActiveOrdersViewArgs {
            metadata: Some(metadata),
            orders: Some(orders),
            commitments: Some(commitments),
            risk_reservations: Some(risk_reservations),
        },
    );
    fb::finish_active_orders_view_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}

fn encode_current_execution(
    actor_id: &str,
    identity: &InstanceIdentity,
    generation: u64,
    key: &ExecutionViewKey,
    snapshot: &ExecutionCurrentView,
) -> Result<Vec<u8>, String> {
    const HISTORY_LIMIT: usize = 4_096;
    let mut builder = FlatBufferBuilder::new();
    let context = EncodeContext::view(
        actor_id,
        actor_id,
        identity.clone(),
        generation,
        key.canonical_key(),
    );
    let metadata = view_metadata(
        &mut builder,
        &context,
        key,
        snapshot.exchange_event_watermark_unix_nanos.get(),
        snapshot.event_sequence.get(),
    );
    let offsets = snapshot
        .orders
        .iter()
        .map(|value| encode_order_state(&mut builder, value))
        .collect::<Result<Vec<_>, _>>()?;
    let orders = builder.create_vector(&offsets);
    let offsets = snapshot
        .intents
        .iter()
        .map(|value| encode_intent_state(&mut builder, value))
        .collect::<Result<Vec<_>, _>>()?;
    let intents = builder.create_vector(&offsets);
    let fill_start = snapshot.fills.len().saturating_sub(HISTORY_LIMIT);
    let offsets = snapshot.fills[fill_start..]
        .iter()
        .map(|value| encode_current_fill(&mut builder, value, &snapshot.orders))
        .collect::<Result<Vec<_>, _>>()?;
    let fills = builder.create_vector(&offsets);
    let event_start = snapshot.events.len().saturating_sub(HISTORY_LIMIT);
    let offsets = snapshot.events[event_start..]
        .iter()
        .map(|value| encode_order_event_state(&mut builder, value))
        .collect::<Vec<_>>();
    let order_events = builder.create_vector(&offsets);
    let intent_event_start = snapshot.intent_events.len().saturating_sub(HISTORY_LIMIT);
    let offsets = snapshot.intent_events[intent_event_start..]
        .iter()
        .map(|value| encode_intent_event_state(&mut builder, value))
        .collect::<Vec<_>>();
    let intent_events = builder.create_vector(&offsets);
    let offsets = snapshot
        .unknown_remote_orders
        .iter()
        .map(|value| encode_unknown_remote_order(&mut builder, value))
        .collect::<Vec<_>>();
    let unknown_remote_orders = builder.create_vector(&offsets);
    let offsets = snapshot
        .commitments
        .iter()
        .map(|value| encode_commitment_state(&mut builder, value))
        .collect::<Result<Vec<_>, _>>()?;
    let commitments = builder.create_vector(&offsets);
    let offsets = snapshot
        .risk_reservations
        .iter()
        .map(|value| encode_risk_reservation_state(&mut builder, value))
        .collect::<Result<Vec<_>, _>>()?;
    let risk_reservations = builder.create_vector(&offsets);
    let root = fb::CurrentExecutionView::create(
        &mut builder,
        &fb::CurrentExecutionViewArgs {
            metadata: Some(metadata),
            orders: Some(orders),
            intents: Some(intents),
            fills: Some(fills),
            order_events: Some(order_events),
            intent_events: Some(intent_events),
            unknown_remote_orders: Some(unknown_remote_orders),
            commitments: Some(commitments),
            risk_reservations: Some(risk_reservations),
            exchange_event_watermark_unix_nanos: snapshot.exchange_event_watermark_unix_nanos.get(),
            fill_history_truncated: snapshot.fills.len() > HISTORY_LIMIT,
            order_event_history_truncated: snapshot.events.len() > HISTORY_LIMIT,
            intent_event_history_truncated: snapshot.intent_events.len() > HISTORY_LIMIT,
        },
    );
    fb::finish_current_execution_view_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}

fn encode_current_fill<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    fill: &crate::domain::ExecutionFill,
    orders: &[ExecutionOrder],
) -> Result<flatbuffers::WIPOffset<fb::Fill<'a>>, String> {
    let order = orders.iter().find(|value| value.order_id == fill.order_id);
    let fill_id = builder.create_string(fill.fill_id.as_str());
    let order_id = builder.create_string(fill.order_id.as_str());
    let intent_id = builder.create_string(
        &fill
            .intent_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
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
    let strategy_id = builder.create_string(
        &order
            .and_then(|value| value.strategy_id.as_ref())
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let account_id = builder.create_string(
        &order
            .map(|value| value.account_id.to_string())
            .unwrap_or_default(),
    );
    let segment_key = builder.create_string(
        &order
            .map(|value| value.segment_key.to_string())
            .unwrap_or_default(),
    );
    let instrument_id = builder.create_string(fill.instrument_id.as_str());
    let market_id = builder.create_string(
        &fill
            .execution_market_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let execution_access_id = builder.create_string(
        &order
            .and_then(|value| value.execution_access_id.as_ref())
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let remote_order_id = order
        .and_then(|value| value.remote_order_id.as_ref())
        .map(|value| builder.create_string(value.as_str()));
    let fee_asset_id = fill
        .fee_currency
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    let quantity = decimal(fill.quantity);
    let price = decimal(fill.price);
    let fee = decimal(fill.fee);
    Ok(fb::Fill::create(
        builder,
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
            execution_access_id: Some(execution_access_id),
            remote_order_id,
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
    ))
}

fn encode_order_event_state<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    event: &crate::application::ExecutionEvent,
) -> flatbuffers::WIPOffset<fb::OrderLifecycleEventState<'a>> {
    let order_id = builder.create_string(event.order_id.as_str());
    let intent_id = event
        .intent_id
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    let plan_id = event
        .plan_id
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    let leg_id = event
        .leg_id
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    let remote_order_id = event
        .remote_order_id
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    let reason = (!event.reason.is_empty()).then(|| builder.create_string(&event.reason));
    let fill_id = event
        .fill_id
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    let filled_quantity = event.filled_quantity.map(decimal);
    fb::OrderLifecycleEventState::create(
        builder,
        &fb::OrderLifecycleEventStateArgs {
            order_id: Some(order_id),
            intent_id,
            plan_id,
            leg_id,
            lifecycle: order_lifecycle(event.status),
            remote_order_id,
            occurred_at_unix_nanos: event.occurred_at_unix_nanos.get(),
            reason,
            fill_id,
            filled_quantity: filled_quantity.as_ref(),
        },
    )
}

fn encode_intent_event_state<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    event: &crate::application::IntentEvent,
) -> flatbuffers::WIPOffset<fb::IntentLifecycleEventState<'a>> {
    let intent_id = builder.create_string(event.intent_id.as_str());
    let order_ids = event
        .order_ids
        .iter()
        .map(|value| builder.create_string(value.as_str()))
        .collect::<Vec<_>>();
    let order_ids = builder.create_vector(&order_ids);
    let completed_quantity = decimal(event.completed_quantity);
    let reason = (!event.reason.is_empty()).then(|| builder.create_string(&event.reason));
    fb::IntentLifecycleEventState::create(
        builder,
        &fb::IntentLifecycleEventStateArgs {
            intent_id: Some(intent_id),
            event_sequence: event.event_sequence.get(),
            lifecycle: intent_lifecycle(event.status),
            order_ids: Some(order_ids),
            completed_quantity: Some(&completed_quantity),
            occurred_at_unix_nanos: event.occurred_at_unix_nanos.get(),
            reason,
        },
    )
}

fn encode_unknown_remote_order<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    order: &crate::application::UnknownRemoteOrder,
) -> flatbuffers::WIPOffset<fb::UnknownRemoteOrderState<'a>> {
    let remote_order_id = builder.create_string(order.remote_order_id.as_str());
    let symbol = builder.create_string(order.symbol.as_str());
    let execution_id = order
        .execution_id
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    let fill_quantity = order.fill_quantity.map(decimal);
    let fill_price = order.fill_price.map(decimal);
    let fee_currency = order
        .fee_currency
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    let fee_amount = order.fee_amount.map(decimal);
    let resolution = builder.create_string(match order.resolution {
        crate::application::UnknownRemoteOrderResolution::Pending => "pending",
        crate::application::UnknownRemoteOrderResolution::LinkedToLocalOrder => {
            "linked_to_local_order"
        }
        crate::application::UnknownRemoteOrderResolution::ImportedAsExternalOrder => {
            "imported_as_external_order"
        }
        crate::application::UnknownRemoteOrderResolution::ManualReview => "manual_review",
    });
    let reason = (!order.reason.is_empty()).then(|| builder.create_string(&order.reason));
    fb::UnknownRemoteOrderState::create(
        builder,
        &fb::UnknownRemoteOrderStateArgs {
            remote_order_id: Some(remote_order_id),
            symbol: Some(symbol),
            lifecycle: order_lifecycle(order.status),
            execution_id,
            fill_quantity: fill_quantity.as_ref(),
            fill_price: fill_price.as_ref(),
            fee_currency,
            fee_amount: fee_amount.as_ref(),
            first_seen_at_unix_nanos: order.first_seen_at_unix_nanos.get(),
            last_seen_at_unix_nanos: order.last_seen_at_unix_nanos.get(),
            resolution: Some(resolution),
            reason,
        },
    )
}

fn encode_commitment_state<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    commitment: &OrderCommitment,
) -> Result<flatbuffers::WIPOffset<fb::OrderCommitmentState<'a>>, String> {
    let order_id = builder.create_string(commitment.order_id.as_str());
    let account_id = builder.create_string(commitment.account_id.as_str());
    let segment_key = builder.create_string(commitment.segment_key.as_str());
    let instrument_id = builder.create_string(commitment.instrument_id.as_str());
    let (resource_kind, resource_id) = match &commitment.resource {
        CommitmentResource::Asset(value) => (fb::CommitmentResourceKind::ASSET, value.as_str()),
        CommitmentResource::Instrument(value) => {
            (fb::CommitmentResourceKind::INSTRUMENT, value.as_str())
        }
        CommitmentResource::MarginNotional(value) => {
            (fb::CommitmentResourceKind::MARGIN_NOTIONAL, value.as_str())
        }
    };
    let resource_id = builder.create_string(resource_id);
    let settlement_asset = commitment
        .settlement_asset
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    let amount = decimal(commitment.amount);
    let remaining_quantity = decimal(commitment.remaining_quantity);
    let (basis_kind, price_cap, contract_size) = match commitment.basis {
        CommitmentBasis::QuotePriceCap { price_cap } => (
            fb::CommitmentBasisKind::QUOTE_PRICE_CAP,
            Some(decimal(price_cap)),
            None,
        ),
        CommitmentBasis::BaseQuantity => (fb::CommitmentBasisKind::BASE_QUANTITY, None, None),
        CommitmentBasis::ContractNotional {
            price_cap,
            contract_size,
        } => (
            fb::CommitmentBasisKind::CONTRACT_NOTIONAL,
            Some(decimal(price_cap)),
            Some(decimal(contract_size)),
        ),
        CommitmentBasis::SimulationQuantity => {
            (fb::CommitmentBasisKind::SIMULATION_QUANTITY, None, None)
        }
    };
    Ok(fb::OrderCommitmentState::create(
        builder,
        &fb::OrderCommitmentStateArgs {
            order_id: Some(order_id),
            account_id: Some(account_id),
            segment_key: Some(segment_key),
            instrument_id: Some(instrument_id),
            side: match commitment.side {
                OrderSide::Buy => kairos_protocol::generated::kairos::common::v_2::Side::BUY,
                OrderSide::Sell => kairos_protocol::generated::kairos::common::v_2::Side::SELL,
            },
            resource_kind,
            resource_id: Some(resource_id),
            amount: Some(&amount),
            remaining_quantity: Some(&remaining_quantity),
            lifecycle: match commitment.status {
                CommitmentStatus::HeldBeforeSend => fb::CommitmentLifecycle::HELD_BEFORE_SEND,
                CommitmentStatus::Active => fb::CommitmentLifecycle::ACTIVE,
                CommitmentStatus::Uncertain => fb::CommitmentLifecycle::UNCERTAIN,
                CommitmentStatus::Reduced => fb::CommitmentLifecycle::REDUCED,
                CommitmentStatus::Released => fb::CommitmentLifecycle::RELEASED,
                CommitmentStatus::Reconciled => fb::CommitmentLifecycle::RECONCILED,
            },
            basis_kind,
            price_cap: price_cap.as_ref(),
            contract_size: contract_size.as_ref(),
            settlement_asset,
            updated_at_unix_nanos: commitment.updated_at_unix_nanos.get(),
        },
    ))
}

fn encode_risk_reservation_state<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    reservation: &RiskReservationEvidence,
) -> Result<flatbuffers::WIPOffset<fb::RiskReservationSagaState<'a>>, String> {
    let order_id = builder.create_string(reservation.order_id.as_str());
    let reservation_id = builder.create_string(&reservation.reservation_id);
    let idempotency_key = builder.create_string(&reservation.idempotency_key);
    let account_id = builder.create_string(reservation.account_id.as_str());
    let amount = decimal(reservation.amount);
    Ok(fb::RiskReservationSagaState::create(
        builder,
        &fb::RiskReservationSagaStateArgs {
            order_id: Some(order_id),
            reservation_id: Some(reservation_id),
            idempotency_key: Some(idempotency_key),
            account_id: Some(account_id),
            amount: Some(&amount),
            lifecycle: match reservation.status {
                RiskReservationSagaStatus::AuthorizePending => {
                    fb::RiskReservationSagaLifecycle::AUTHORIZE_PENDING
                }
                RiskReservationSagaStatus::Active => fb::RiskReservationSagaLifecycle::ACTIVE,
                RiskReservationSagaStatus::ResizePending => {
                    fb::RiskReservationSagaLifecycle::RESIZE_PENDING
                }
                RiskReservationSagaStatus::ReleasePending => {
                    fb::RiskReservationSagaLifecycle::RELEASE_PENDING
                }
                RiskReservationSagaStatus::ConsumePending => {
                    fb::RiskReservationSagaLifecycle::CONSUME_PENDING
                }
                RiskReservationSagaStatus::Released => fb::RiskReservationSagaLifecycle::RELEASED,
                RiskReservationSagaStatus::Consumed => fb::RiskReservationSagaLifecycle::CONSUMED,
                RiskReservationSagaStatus::Expired => fb::RiskReservationSagaLifecycle::EXPIRED,
                RiskReservationSagaStatus::Failed => fb::RiskReservationSagaLifecycle::FAILED,
                RiskReservationSagaStatus::Uncertain => fb::RiskReservationSagaLifecycle::UNCERTAIN,
            },
            risk_generation: reservation.risk_generation,
            risk_event_sequence: reservation.risk_event_sequence,
            policy_version: reservation.policy_version,
            expires_at_unix_nanos: reservation.expires_at_unix_nanos.get(),
            updated_at_unix_nanos: reservation.updated_at_unix_nanos.get(),
        },
    ))
}

fn encode_order_state<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    order: &ExecutionOrder,
) -> Result<flatbuffers::WIPOffset<fb::OrderState<'a>>, String> {
    let order_id = builder.create_string(&order.order_id.to_string());
    let intent_id = builder.create_string(
        &order
            .intent_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let plan_id = builder.create_string(
        &order
            .plan_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let leg_id = builder.create_string(
        &order
            .leg_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let strategy_id = builder.create_string(
        &order
            .strategy_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let account_id = builder.create_string(&order.account_id.to_string());
    let segment_key = builder.create_string(&order.segment_key.to_string());
    let instrument_id = builder.create_string(&order.instrument_id.to_string());
    let market_id = builder.create_string(
        &order
            .market_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let execution_access_id = builder.create_string(
        &order
            .execution_access_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let remote_order_id = order
        .remote_order_id
        .as_ref()
        .map(|value| builder.create_string(&value.to_string()));
    let quantity = decimal(order.quantity);
    let filled_quantity = decimal(order.filled_quantity);
    let limit_price = order.limit_price.map(decimal);
    let reason = (!order.reason.is_empty()).then(|| builder.create_string(&order.reason));
    Ok(fb::OrderState::create(
        builder,
        &fb::OrderStateArgs {
            order_id: Some(order_id),
            intent_id: Some(intent_id),
            plan_id: Some(plan_id),
            leg_id: Some(leg_id),
            strategy_id: Some(strategy_id),
            account_id: Some(account_id),
            segment_key: Some(segment_key),
            instrument_id: Some(instrument_id),
            market_id: Some(market_id),
            execution_access_id: Some(execution_access_id),
            remote_order_id,
            side: match order.side {
                OrderSide::Buy => kairos_protocol::generated::kairos::common::v_2::Side::BUY,
                OrderSide::Sell => kairos_protocol::generated::kairos::common::v_2::Side::SELL,
            },
            order_type: match order.order_type {
                OrderType::Market => fb::OrderType::MARKET,
                OrderType::Limit => fb::OrderType::LIMIT,
            },
            quantity: Some(&quantity),
            filled_quantity: Some(&filled_quantity),
            limit_price: limit_price.as_ref(),
            average_fill_price: None,
            lifecycle: order_lifecycle(order.status),
            created_at_unix_nanos: order.submitted_at_unix_nanos.get(),
            submitted_at_unix_nanos: Some(order.submitted_at_unix_nanos.get()),
            updated_at_unix_nanos: order.updated_at_unix_nanos.get(),
            reason,
        },
    ))
}

fn order_lifecycle(status: ExecutionOrderStatus) -> fb::OrderLifecycle {
    match status {
        ExecutionOrderStatus::Pending => fb::OrderLifecycle::PENDING,
        ExecutionOrderStatus::Submitting => fb::OrderLifecycle::SUBMITTING,
        ExecutionOrderStatus::Accepted => fb::OrderLifecycle::ACCEPTED,
        ExecutionOrderStatus::PartiallyFilled => fb::OrderLifecycle::PARTIALLY_FILLED,
        ExecutionOrderStatus::Filled => fb::OrderLifecycle::FILLED,
        ExecutionOrderStatus::CancelRequested => fb::OrderLifecycle::CANCEL_REQUESTED,
        ExecutionOrderStatus::Canceled => fb::OrderLifecycle::CANCELED,
        ExecutionOrderStatus::Rejected => fb::OrderLifecycle::REJECTED,
        ExecutionOrderStatus::Expired => fb::OrderLifecycle::EXPIRED,
        ExecutionOrderStatus::Unknown => fb::OrderLifecycle::UNKNOWN,
        ExecutionOrderStatus::Failed => fb::OrderLifecycle::FAILED,
    }
}

fn intent_lifecycle(status: crate::application::IntentStatus) -> fb::IntentLifecycle {
    use crate::application::IntentStatus;
    match status {
        IntentStatus::Accepted => fb::IntentLifecycle::ACCEPTED,
        IntentStatus::Planning => fb::IntentLifecycle::PLANNING,
        IntentStatus::Planned => fb::IntentLifecycle::PLANNED,
        IntentStatus::Executing => fb::IntentLifecycle::EXECUTING,
        IntentStatus::PartiallyFilled => fb::IntentLifecycle::PARTIALLY_FILLED,
        IntentStatus::CancelRequested => fb::IntentLifecycle::CANCEL_REQUESTED,
        IntentStatus::Satisfied => fb::IntentLifecycle::SATISFIED,
        IntentStatus::Rejected => fb::IntentLifecycle::REJECTED,
        IntentStatus::Canceled => fb::IntentLifecycle::CANCELED,
        IntentStatus::Expired => fb::IntentLifecycle::EXPIRED,
        IntentStatus::Failed => fb::IntentLifecycle::FAILED,
        IntentStatus::Compensating => fb::IntentLifecycle::COMPENSATING,
        IntentStatus::ReconciliationRequired => fb::IntentLifecycle::RECONCILIATION_REQUIRED,
    }
}

trait DecimalValue {
    fn mantissa(&self) -> i64;
    fn scale(&self) -> u8;
}

impl DecimalValue for kairos_primitives::Quantity {
    fn mantissa(&self) -> i64 {
        kairos_primitives::Quantity::mantissa(*self)
    }
    fn scale(&self) -> u8 {
        kairos_primitives::Quantity::scale(*self)
    }
}

impl DecimalValue for kairos_primitives::Price {
    fn mantissa(&self) -> i64 {
        kairos_primitives::Price::mantissa(*self)
    }
    fn scale(&self) -> u8 {
        kairos_primitives::Price::scale(*self)
    }
}

impl DecimalValue for kairos_primitives::Money {
    fn mantissa(&self) -> i64 {
        kairos_primitives::Money::mantissa(*self)
    }
    fn scale(&self) -> u8 {
        kairos_primitives::Money::scale(*self)
    }
}

fn decimal<T: DecimalValue>(
    value: T,
) -> kairos_protocol::generated::kairos::common::v_2::Decimal64 {
    kairos_protocol::generated::kairos::common::v_2::Decimal64::new(value.mantissa(), value.scale())
}

fn encode_active_intents(
    actor_id: &str,
    identity: &InstanceIdentity,
    generation: u64,
    key: &ExecutionViewKey,
    snapshot: &ExecutionCurrentView,
) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let context = EncodeContext::view(
        actor_id,
        actor_id,
        identity.clone(),
        generation,
        key.canonical_key(),
    );
    let metadata = view_metadata(
        &mut builder,
        &context,
        key,
        0,
        snapshot.event_sequence.get(),
    );
    let intent_offsets = snapshot
        .intents
        .iter()
        .map(|intent| encode_intent_state(&mut builder, intent))
        .collect::<Result<Vec<_>, _>>()?;
    let intents = builder.create_vector(&intent_offsets);
    let root = fb::ActiveIntentsView::create(
        &mut builder,
        &fb::ActiveIntentsViewArgs {
            metadata: Some(metadata),
            intents: Some(intents),
        },
    );
    fb::finish_active_intents_view_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}

fn encode_intent_state<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    state: &crate::application::IntentState,
) -> Result<flatbuffers::WIPOffset<fb::IntentState<'a>>, String> {
    let intent = &state.intent;
    let intent_id = builder.create_string(&intent.intent_id.to_string());
    let strategy_id = builder.create_string(&intent.strategy_id);
    let launch_id = builder.create_string(&intent.launch_id);
    let instance_id = builder.create_string(&intent.instance_id);
    let reason = (!intent.reason.is_empty()).then(|| builder.create_string(&intent.reason));
    let leg_offsets = intent
        .legs
        .iter()
        .map(|leg| encode_intent_leg(builder, leg))
        .collect::<Result<Vec<_>, _>>()?;
    let legs = builder.create_vector(&leg_offsets);
    let evidence = builder.create_vector::<flatbuffers::WIPOffset<
        kairos_protocol::generated::kairos::common::v_2::EvidenceRef,
    >>(&[]);
    let intent_offset = fb::ExecutionIntent::create(
        builder,
        &fb::ExecutionIntentArgs {
            intent_id: Some(intent_id),
            strategy_id: Some(strategy_id),
            launch_id: Some(launch_id),
            instance_id: Some(instance_id),
            intent_type: intent_type(intent.intent_type),
            legs: Some(legs),
            completion_policy: completion_policy(intent.completion_policy),
            failure_policy: failure_policy(intent.failure_policy),
            hedge_policy: None,
            deadline_unix_nanos: intent.deadline_unix_nanos.map(|value| value.get()),
            min_edge_bps: intent.min_edge_bps,
            max_slippage_bps: intent.max_slippage_bps,
            estimated_fee_bps: intent.estimated_fee_bps,
            evidence: Some(evidence),
            reason,
        },
    );
    let dependency_evidence = builder.create_vector::<flatbuffers::WIPOffset<
        kairos_protocol::generated::kairos::common::v_2::EvidenceRef,
    >>(&[]);
    let state_reason = (!state.reason.is_empty()).then(|| builder.create_string(&state.reason));
    let plan = state
        .plan
        .as_ref()
        .map(|plan| encode_plan(builder, plan))
        .transpose()?;
    Ok(fb::IntentState::create(
        builder,
        &fb::IntentStateArgs {
            intent: Some(intent_offset),
            lifecycle: intent_lifecycle(state.status),
            plan,
            updated_at_unix_nanos: state.updated_at_unix_nanos.get(),
            reason: state_reason,
            dependency_evidence: Some(dependency_evidence),
            quote_version: Some(state.quote_version),
            last_quote_refresh_unix_nanos: state.last_quote_refresh_unix_nanos.map(|v| v.get()),
            compensation_attempts: state.compensation_attempts,
        },
    ))
}

fn encode_intent_leg<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    leg: &crate::application::IntentLegRequest,
) -> Result<flatbuffers::WIPOffset<fb::IntentLeg<'a>>, String> {
    let leg_id = builder.create_string(&leg.leg_id.to_string());
    let account_id = builder.create_string(&leg.account_id.to_string());
    let segment_key = builder.create_string(&leg.segment_key.to_string());
    let instrument_id = builder.create_string(&leg.instrument_id.to_string());
    let market_id = builder.create_string(
        &leg.market_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let execution_access_id = builder.create_string(
        &leg.execution_access_id
            .as_ref()
            .ok_or_else(|| "execution intent leg execution_access_id is required".to_owned())?
            .to_string(),
    );
    let quantity = decimal(leg.quantity);
    let limit_price = leg.limit_price.map(decimal);
    let options =
        fb::ExecutionOrderOptions::create(builder, &fb::ExecutionOrderOptionsArgs::default());
    Ok(fb::IntentLeg::create(
        builder,
        &fb::IntentLegArgs {
            leg_id: Some(leg_id),
            account_id: Some(account_id),
            segment_key: Some(segment_key),
            instrument_id: Some(instrument_id),
            market_id: Some(market_id),
            execution_access_id: Some(execution_access_id),
            side: match leg.side {
                OrderSide::Buy => kairos_protocol::generated::kairos::common::v_2::Side::BUY,
                OrderSide::Sell => kairos_protocol::generated::kairos::common::v_2::Side::SELL,
            },
            quantity: Some(&quantity),
            quantity_semantics: if leg.target_position {
                fb::QuantitySemantics::TARGET_POSITION
            } else {
                fb::QuantitySemantics::ORDER_QUANTITY
            },
            limit_price: limit_price.as_ref(),
            options: Some(options),
        },
    ))
}

fn encode_plan<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    plan: &crate::domain::ExecutionPlan,
) -> Result<flatbuffers::WIPOffset<fb::ExecutionPlan<'a>>, String> {
    let plan_id = builder.create_string(&plan.plan_id.to_string());
    let intent_id = builder.create_string(&plan.intent_id.to_string());
    let leg_offsets = plan
        .legs
        .iter()
        .map(|leg| {
            let leg_id = builder.create_string(&leg.leg_id.to_string());
            let account_id = builder.create_string(&leg.account_id.to_string());
            let segment_key = builder.create_string(&leg.segment_key.to_string());
            let instrument_id = builder.create_string(&leg.instrument_id.to_string());
            let market_id = builder.create_string(
                &leg.market_id
                    .as_ref()
                    .map(ToString::to_string)
                    .unwrap_or_default(),
            );
            let order_ids = leg
                .order_ids
                .iter()
                .map(|id| builder.create_string(&id.to_string()))
                .collect::<Vec<_>>();
            let order_ids = builder.create_vector(&order_ids);
            let target_quantity = decimal(leg.target_quantity);
            let completed_quantity = decimal(leg.completed_quantity);
            let reason = (!leg.reason.is_empty()).then(|| builder.create_string(&leg.reason));
            Ok(fb::ExecutionLegState::create(
                builder,
                &fb::ExecutionLegStateArgs {
                    leg_id: Some(leg_id),
                    account_id: Some(account_id),
                    segment_key: Some(segment_key),
                    instrument_id: Some(instrument_id),
                    market_id: Some(market_id),
                    side: match leg.side {
                        OrderSide::Buy => {
                            kairos_protocol::generated::kairos::common::v_2::Side::BUY
                        }
                        OrderSide::Sell => {
                            kairos_protocol::generated::kairos::common::v_2::Side::SELL
                        }
                    },
                    target_quantity: Some(&target_quantity),
                    order_ids: Some(order_ids),
                    lifecycle: leg_lifecycle(leg.lifecycle),
                    completed_quantity: Some(&completed_quantity),
                    reason,
                },
            ))
        })
        .collect::<Result<Vec<_>, String>>()?;
    let legs = builder.create_vector(&leg_offsets);
    Ok(fb::ExecutionPlan::create(
        builder,
        &fb::ExecutionPlanArgs {
            plan_id: Some(plan_id),
            intent_id: Some(intent_id),
            intent_type: intent_type(plan.intent_type),
            legs: Some(legs),
            completion_policy: completion_policy(plan.completion_policy),
            failure_policy: failure_policy(plan.failure_policy),
            created_at_unix_nanos: 0,
        },
    ))
}

fn leg_lifecycle(value: crate::domain::LegLifecycle) -> fb::LegLifecycle {
    match value {
        crate::domain::LegLifecycle::Pending => fb::LegLifecycle::PENDING,
        crate::domain::LegLifecycle::Ready => fb::LegLifecycle::READY,
        crate::domain::LegLifecycle::Executing => fb::LegLifecycle::EXECUTING,
        crate::domain::LegLifecycle::PartiallyFilled => fb::LegLifecycle::PARTIALLY_FILLED,
        crate::domain::LegLifecycle::Satisfied => fb::LegLifecycle::SATISFIED,
        crate::domain::LegLifecycle::Canceled => fb::LegLifecycle::CANCELED,
        crate::domain::LegLifecycle::Failed => fb::LegLifecycle::FAILED,
        crate::domain::LegLifecycle::Compensating => fb::LegLifecycle::COMPENSATING,
    }
}

fn intent_type(value: crate::domain::IntentType) -> fb::IntentType {
    match value {
        crate::domain::IntentType::SingleOrder => fb::IntentType::SINGLE_ORDER,
        crate::domain::IntentType::TargetPosition => fb::IntentType::TARGET_POSITION,
        crate::domain::IntentType::PairArbitrage => fb::IntentType::PAIR_ARBITRAGE,
        crate::domain::IntentType::OptionSpread => fb::IntentType::OPTION_SPREAD,
        crate::domain::IntentType::PortfolioRebalance => fb::IntentType::PORTFOLIO_REBALANCE,
        crate::domain::IntentType::QuoteProvisioning => fb::IntentType::QUOTE_PROVISIONING,
        crate::domain::IntentType::Hedge => fb::IntentType::HEDGE,
    }
}

fn completion_policy(value: crate::domain::CompletionPolicy) -> fb::CompletionPolicy {
    match value {
        crate::domain::CompletionPolicy::AllLegsSatisfied => {
            fb::CompletionPolicy::ALL_LEGS_SATISFIED
        }
        crate::domain::CompletionPolicy::AllOrNothing => fb::CompletionPolicy::ALL_OR_NOTHING,
        crate::domain::CompletionPolicy::BestEffort => fb::CompletionPolicy::BEST_EFFORT,
        crate::domain::CompletionPolicy::HedgeWithinTolerance => {
            fb::CompletionPolicy::HEDGE_WITHIN_TOLERANCE
        }
        crate::domain::CompletionPolicy::TargetQuantityReached => {
            fb::CompletionPolicy::TARGET_QUANTITY_REACHED
        }
    }
}

fn failure_policy(value: crate::domain::FailurePolicy) -> fb::FailurePolicy {
    match value {
        crate::domain::FailurePolicy::CancelRemaining => fb::FailurePolicy::CANCEL_REMAINING,
        crate::domain::FailurePolicy::ContinueOtherLegs => fb::FailurePolicy::CONTINUE_OTHER_LEGS,
        crate::domain::FailurePolicy::Compensate => fb::FailurePolicy::COMPENSATE,
        crate::domain::FailurePolicy::PauseForManualIntervention => {
            fb::FailurePolicy::PAUSE_FOR_MANUAL_INTERVENTION
        }
        crate::domain::FailurePolicy::MarkReconciliationRequired => {
            fb::FailurePolicy::MARK_RECONCILIATION_REQUIRED
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::{
        CommitmentBasis, CommitmentResource, OrderCommitment, RiskReservationEvidence,
        RiskReservationSagaStatus,
    };
    use kairos_primitives::{
        AccountId, Currency, Generation, InstrumentId, Money, OrderId, Quantity, SegmentKey,
        Sequence, UnixNanos,
    };

    #[test]
    fn active_orders_mmap_encodes_commitments_and_risk_saga() {
        let order_id = OrderId::new("order-1").unwrap();
        let account_id = AccountId::new("account-1").unwrap();
        let segment_key = SegmentKey::new("spot").unwrap();
        let instrument_id = InstrumentId::new("BTC-USDT").unwrap();
        let mut commitment = OrderCommitment::new(
            order_id.clone(),
            account_id.clone(),
            segment_key,
            instrument_id.clone(),
            OrderSide::Buy,
            CommitmentResource::Asset(Currency::new("USDT").unwrap()),
            Money::new(100, 0).unwrap(),
            Quantity::new(1, 0).unwrap(),
            CommitmentBasis::QuotePriceCap {
                price_cap: kairos_primitives::Price::new(100, 0).unwrap(),
            },
            UnixNanos::new(10),
        )
        .unwrap();
        commitment.settlement_asset = Some(Currency::new("USDT").unwrap());
        let snapshot = ExecutionCurrentView {
            generation: Generation::new(3),
            event_sequence: Sequence::new(4),
            orders: Vec::new(),
            commitments: vec![commitment],
            risk_reservations: vec![RiskReservationEvidence {
                order_id,
                reservation_id: "execution:order-1".into(),
                idempotency_key: "execution:order-1".into(),
                account_id,
                amount: Money::new(100, 0).unwrap(),
                status: RiskReservationSagaStatus::Active,
                risk_generation: 7,
                risk_event_sequence: 8,
                policy_version: 2,
                expires_at_unix_nanos: UnixNanos::new(1000),
                updated_at_unix_nanos: UnixNanos::new(11),
            }],
            intents: Vec::new(),
            events: Vec::new(),
            intent_events: Vec::new(),
            fills: Vec::new(),
            unknown_remote_orders: Vec::new(),
            exchange_event_watermark_unix_nanos: UnixNanos::new(0),
        };
        let identity = InstanceIdentity::new("workspace", "launch", "instance");
        let key = ExecutionViewKey::new(
            identity.workspace_id.clone(),
            ExecutionViewKind::ActiveOrders,
            Some(identity.launch_id.clone()),
            Some(identity.instance_id.clone()),
        )
        .unwrap();
        let bytes = encode_active_orders("execution", &identity, 3, &key, &snapshot).unwrap();
        let decoded = fb::root_as_active_orders_view(&bytes).unwrap();
        assert_eq!(decoded.commitments().len(), 1);
        assert_eq!(decoded.risk_reservations().len(), 1);
        assert_eq!(
            decoded.commitments().get(0).lifecycle(),
            fb::CommitmentLifecycle::HELD_BEFORE_SEND
        );
        assert_eq!(
            decoded.commitments().get(0).settlement_asset(),
            Some("USDT")
        );
        assert_eq!(
            decoded.risk_reservations().get(0).lifecycle(),
            fb::RiskReservationSagaLifecycle::ACTIVE
        );

        let current_key = ExecutionViewKey::new(
            identity.workspace_id.clone(),
            ExecutionViewKind::CurrentExecution,
            Some(identity.launch_id.clone()),
            Some(identity.instance_id.clone()),
        )
        .unwrap();
        let bytes =
            encode_current_execution("execution", &identity, 3, &current_key, &snapshot).unwrap();
        let current = fb::root_as_current_execution_view(&bytes).unwrap();
        assert_eq!(current.metadata().generation(), 3);
        assert_eq!(current.metadata().applied_revision(), Some(4));
        assert_eq!(current.commitments().len(), 1);
        assert_eq!(current.risk_reservations().len(), 1);

        let directory = tempfile::tempdir().unwrap();
        let legacy_path = directory
            .path()
            .join("snapshots/execution/execution.snapshot");
        let mut publisher = SharedExecutionSnapshotPublisher::create_with_identity(
            &legacy_path,
            1024 * 1024,
            "execution",
            identity.clone(),
        )
        .unwrap();
        publisher.publish(&snapshot).unwrap();
        let reader =
            kairos_execution_contract::ExecutionViewReader::open(directory.path(), current_key)
                .unwrap();
        let frame = reader.read().unwrap();
        assert_eq!(frame.envelope_metadata().applied_event_sequence, 4);
        assert_eq!(
            frame.current_execution().unwrap().metadata().generation(),
            3
        );
    }
}
