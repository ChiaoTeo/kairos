use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use super::protocol::ExecutionStateStore;
use super::RemoteExecutionEvent;
use crate::domain::{ExecutionFill, ExecutionOrder, ExecutionOrderStatus, OrderSide, OrderType};
use kairos_integration::application::{
    ExecutionStreamConnection, ExternalOrderQuery, OrderQueryConnection,
};
use kairos_integration::domain::{
    OrderEntryOptions as ConnectionOrderEntryOptions, OrderSide as ConnectionOrderSide,
    OrderType as ConnectionOrderType, TimeInForce,
};
use kairos_integration::{
    DecimalValue as ConnectionDecimalValue, OrderEntryConnection, OrderEntryEvent,
    OrderEntryRequest, OrderEntryStatus,
};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SubmitOrder {
    pub order_id: String,
    pub intent_id: Option<String>,
    pub account_id: String,
    pub segment_key: String,
    pub instrument_id: String,
    pub market_id: Option<String>,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub quantity_mantissa: i64,
    pub quantity_scale: u8,
    pub limit_price_mantissa: Option<i64>,
    pub limit_price_scale: Option<u8>,
    #[serde(default)]
    pub options: ExecutionOrderOptions,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionOrderOptions {
    pub time_in_force: Option<String>,
    pub reduce_only: Option<bool>,
    pub post_only: Option<bool>,
    pub position_side: Option<String>,
    pub quote_asset: Option<String>,
    pub wallet_type: Option<String>,
    pub trading_session: Option<String>,
    pub tokenize: Option<bool>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CancelOrder {
    pub order_id: String,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReplaceOrder {
    pub order_id: String,
    pub replacement: SubmitOrder,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecuteIntent {
    pub intent_id: String,
    pub current_quantity_mantissa: i64,
    pub target_quantity_mantissa: i64,
    pub quantity_scale: u8,
    pub order_id: String,
    pub account_id: String,
    pub segment_key: String,
    pub instrument_id: String,
    pub market_id: Option<String>,
    pub limit_price_mantissa: Option<i64>,
    pub limit_price_scale: Option<u8>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionFillReport {
    pub fill_id: String,
    pub order_id: String,
    pub quantity_mantissa: i64,
    pub quantity_scale: u8,
    pub price_mantissa: i64,
    pub price_scale: u8,
    #[serde(default)]
    pub fee_mantissa: i64,
    #[serde(default)]
    pub fee_scale: u8,
    pub occurred_at_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionEvent {
    pub order_id: String,
    pub status: ExecutionOrderStatus,
    pub venue_order_id: Option<String>,
    pub occurred_at_unix_nanos: u64,
    pub reason: String,
    #[serde(default)]
    pub fill_id: Option<String>,
    #[serde(default)]
    pub filled_quantity_mantissa: Option<i64>,
    #[serde(default)]
    pub filled_quantity_scale: Option<u8>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionSnapshot {
    pub actor_id: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub orders: Vec<ExecutionOrder>,
    #[serde(default)]
    pub events: Vec<ExecutionEvent>,
    #[serde(default)]
    pub fills: Vec<ExecutionFill>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct RemoteOrderQuery {
    pub symbol: Option<String>,
    pub order_id: Option<String>,
    pub limit: Option<u32>,
    pub since_unix_millis: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RemoteOrder {
    pub order_id: String,
    pub client_order_id: Option<String>,
    pub symbol: String,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub status: String,
    pub quantity: String,
    pub filled_quantity: String,
    pub average_fill_price: Option<String>,
    pub occurred_at_unix_millis: Option<u64>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionAuditQuery {
    pub order_id: Option<String>,
    pub venue_order_id: Option<String>,
    pub status: Option<String>,
    pub since_unix_nanos: Option<u64>,
    pub until_unix_nanos: Option<u64>,
    pub limit: Option<u32>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionAuditEvent {
    pub sequence: u64,
    pub order_id: String,
    pub status: String,
    pub venue_order_id: Option<String>,
    pub occurred_at_unix_nanos: u64,
    pub reason: String,
}

#[derive(Debug, thiserror::Error)]
pub enum ExecutionError {
    #[error("invalid execution request: {0}")]
    Invalid(String),
    #[error("execution gateway failed: {0}")]
    Gateway(String),
    #[error("execution persistence failed: {0}")]
    Persistence(String),
}

pub struct ExecutionApplication {
    actor_id: String,
    generation: u64,
    event_sequence: u64,
    orders: BTreeMap<String, ExecutionOrder>,
    events: Vec<ExecutionEvent>,
    pending_events: Vec<ExecutionEvent>,
    fills: Vec<ExecutionFill>,
    order_entry: Option<Box<dyn OrderEntryConnection>>,
    order_query: Option<Box<dyn OrderQueryConnection>>,
    execution_stream: Option<Box<dyn ExecutionStreamConnection>>,
    store: Option<Box<dyn ExecutionStateStore>>,
    live_trading: bool,
    live_confirmed: bool,
}

impl ExecutionApplication {
    pub fn with_dependencies(
        actor_id: impl Into<String>,
        order_entry: Option<Box<dyn OrderEntryConnection>>,
        store: Option<Box<dyn ExecutionStateStore>>,
    ) -> Result<Self, ExecutionError> {
        Self::with_dependencies_and_query(actor_id, order_entry, None, store)
    }

    pub fn with_dependencies_and_query(
        actor_id: impl Into<String>,
        order_entry: Option<Box<dyn OrderEntryConnection>>,
        order_query: Option<Box<dyn OrderQueryConnection>>,
        store: Option<Box<dyn ExecutionStateStore>>,
    ) -> Result<Self, ExecutionError> {
        Self::with_dependencies_and_query_and_stream(
            actor_id,
            order_entry,
            order_query,
            None,
            store,
        )
    }

    pub fn with_dependencies_and_query_and_stream(
        actor_id: impl Into<String>,
        order_entry: Option<Box<dyn OrderEntryConnection>>,
        order_query: Option<Box<dyn OrderQueryConnection>>,
        execution_stream: Option<Box<dyn ExecutionStreamConnection>>,
        store: Option<Box<dyn ExecutionStateStore>>,
    ) -> Result<Self, ExecutionError> {
        let actor_id = actor_id.into();
        if actor_id.trim().is_empty() {
            return Err(ExecutionError::Invalid("actor_id is required".into()));
        }
        let mut application = Self {
            actor_id,
            generation: 0,
            event_sequence: 0,
            orders: BTreeMap::new(),
            events: Vec::new(),
            pending_events: Vec::new(),
            fills: Vec::new(),
            order_entry,
            order_query,
            execution_stream,
            store,
            live_trading: false,
            live_confirmed: false,
        };
        if let Some(store) = application.store.as_mut() {
            if let Some(snapshot) = store.load().map_err(ExecutionError::Persistence)? {
                application.generation = snapshot.generation;
                application.event_sequence = snapshot.event_sequence;
                application.orders = snapshot
                    .orders
                    .into_iter()
                    .map(|order| (order.order_id.clone(), order))
                    .collect();
                application.events = snapshot.events;
                application.pending_events = application.events.clone();
                application.fills = snapshot.fills;
            }
        }
        Ok(application)
    }

    pub fn remote_open_orders(
        &mut self,
        query: RemoteOrderQuery,
    ) -> Result<Vec<RemoteOrder>, ExecutionError> {
        self.order_query
            .as_mut()
            .ok_or_else(|| ExecutionError::Gateway("remote order query is not configured".into()))?
            .open_orders(&ExternalOrderQuery {
                symbol: query.symbol,
                order_id: query.order_id,
                limit: query.limit,
                since_unix_millis: query.since_unix_millis,
            })
            .map(|orders| orders.into_iter().map(remote_order).collect())
            .map_err(ExecutionError::Gateway)
    }

    pub fn remote_history(
        &mut self,
        query: RemoteOrderQuery,
    ) -> Result<Vec<RemoteOrder>, ExecutionError> {
        self.order_query
            .as_mut()
            .ok_or_else(|| ExecutionError::Gateway("remote order query is not configured".into()))?
            .order_history(&ExternalOrderQuery {
                symbol: query.symbol,
                order_id: query.order_id,
                limit: query.limit,
                since_unix_millis: query.since_unix_millis,
            })
            .map(|orders| orders.into_iter().map(remote_order).collect())
            .map_err(ExecutionError::Gateway)
    }

    pub fn remote_detail(
        &mut self,
        query: RemoteOrderQuery,
    ) -> Result<Option<RemoteOrder>, ExecutionError> {
        self.order_query
            .as_mut()
            .ok_or_else(|| ExecutionError::Gateway("remote order query is not configured".into()))?
            .order_detail(&ExternalOrderQuery {
                symbol: query.symbol,
                order_id: query.order_id,
                limit: query.limit,
                since_unix_millis: query.since_unix_millis,
            })
            .map(|order| order.map(remote_order))
            .map_err(ExecutionError::Gateway)
    }

    pub fn next_remote_execution_event(
        &mut self,
    ) -> Result<Option<RemoteExecutionEvent>, ExecutionError> {
        self.execution_stream
            .as_mut()
            .ok_or_else(|| ExecutionError::Gateway("execution stream is not configured".into()))?
            .next_execution_event()
            .map(|event| event.map(remote_execution_event))
            .map_err(ExecutionError::Gateway)
    }

    /// Pull one provider update and reconcile it into the local execution
    /// journal.  The provider order id is matched against the stored venue
    /// order id; this keeps the integration event provider-neutral while the
    /// execution application remains the owner of lifecycle state.
    pub fn consume_remote_execution_event(
        &mut self,
    ) -> Result<Option<(RemoteExecutionEvent, ExecutionOrder)>, ExecutionError> {
        let Some(event) = self.next_remote_execution_event()? else {
            return Ok(None);
        };
        let local = self
            .orders
            .values()
            .find(|order| {
                order.order_id == event.order_id
                    || order.venue_order_id.as_deref() == Some(event.order_id.as_str())
            })
            .cloned()
            .ok_or_else(|| {
                ExecutionError::Invalid(format!(
                    "remote execution references unknown order: {}",
                    event.order_id
                ))
            })?;
        if let (Some(quantity), Some(price)) = (&event.fill_quantity, &event.fill_price) {
            let quantity = parse_decimal(quantity)?;
            let price = parse_decimal(price)?;
            let fill = self.record_fill(ExecutionFillReport {
                fill_id: event.execution_id.clone().unwrap_or_else(|| {
                    format!("remote:{}:{}", event.order_id, event.occurred_at_unix_nanos)
                }),
                order_id: local.order_id.clone(),
                quantity_mantissa: quantity.0,
                quantity_scale: quantity.1,
                price_mantissa: price.0,
                price_scale: price.1,
                fee_mantissa: event
                    .fee_amount
                    .as_deref()
                    .map(parse_decimal)
                    .transpose()?
                    .map(|value| value.0)
                    .unwrap_or_default(),
                fee_scale: event
                    .fee_amount
                    .as_deref()
                    .map(parse_decimal)
                    .transpose()?
                    .map(|value| value.1)
                    .unwrap_or_default(),
                occurred_at_unix_nanos: Some(event.occurred_at_unix_nanos),
            })?;
            return Ok(Some((event, fill)));
        }
        let mut next = local;
        next.venue_order_id = Some(event.order_id.clone());
        next.updated_at_unix_nanos = event.occurred_at_unix_nanos;
        next.reason = event.reason.clone();
        next.status = remote_status(&event.status);
        self.orders.insert(next.order_id.clone(), next.clone());
        self.commit(ExecutionEvent {
            order_id: next.order_id.clone(),
            status: next.status,
            venue_order_id: next.venue_order_id.clone(),
            occurred_at_unix_nanos: next.updated_at_unix_nanos,
            reason: next.reason.clone(),
            fill_id: None,
            filled_quantity_mantissa: None,
            filled_quantity_scale: None,
        })?;
        Ok(Some((event, next)))
    }

    pub fn snapshot(&self) -> ExecutionSnapshot {
        ExecutionSnapshot {
            actor_id: self.actor_id.clone(),
            generation: self.generation,
            event_sequence: self.event_sequence,
            orders: self.orders.values().cloned().collect(),
            events: self.events.clone(),
            fills: self.fills.clone(),
        }
    }

    pub fn drain_events(&mut self) -> Vec<ExecutionEvent> {
        std::mem::take(&mut self.pending_events)
    }

    pub fn configure_live_trading(&mut self, enabled: bool, confirmed: bool) {
        self.live_trading = enabled;
        self.live_confirmed = confirmed;
    }

    pub fn preview_submit(&self, request: &SubmitOrder) -> Result<ExecutionOrder, ExecutionError> {
        ExecutionOrder::new(
            request.order_id.clone(),
            request.account_id.clone(),
            request.segment_key.clone(),
            request.instrument_id.clone(),
            request.side,
            request.order_type,
            request.quantity_mantissa,
            request.quantity_scale,
            now_nanos(),
        )
        .map(|mut order| {
            order.intent_id = request.intent_id.clone();
            order.market_id = request.market_id.clone();
            order.limit_price_mantissa = request.limit_price_mantissa;
            order.limit_price_scale = request.limit_price_scale;
            order.reason = "dry-run preview".into();
            order
        })
        .map_err(ExecutionError::Invalid)
    }

    pub fn orders(&self, account_id: Option<&str>) -> Vec<ExecutionOrder> {
        self.orders
            .values()
            .filter(|order| account_id.is_none_or(|id| order.account_id == id))
            .cloned()
            .collect()
    }

    pub fn events(&self, order_id: Option<&str>) -> Vec<ExecutionEvent> {
        self.events
            .iter()
            .filter(|event| order_id.is_none_or(|id| event.order_id == id))
            .cloned()
            .collect()
    }

    pub fn trace(&self, order_id: &str) -> Vec<ExecutionEvent> {
        self.events(Some(order_id))
    }

    pub fn audit_events(
        &mut self,
        query: ExecutionAuditQuery,
    ) -> Result<Vec<ExecutionAuditEvent>, ExecutionError> {
        let mut events = self
            .events
            .iter()
            .enumerate()
            .map(|(index, event)| ExecutionAuditEvent {
                sequence: index as u64 + 1,
                order_id: event.order_id.clone(),
                status: format!("{:?}", event.status).to_ascii_lowercase(),
                venue_order_id: event.venue_order_id.clone(),
                occurred_at_unix_nanos: event.occurred_at_unix_nanos,
                reason: event.reason.clone(),
            })
            .filter(|event| audit_matches(event, &query))
            .collect::<Vec<_>>();
        if let Some(limit) = query.limit {
            events.truncate(limit as usize);
        }
        Ok(events)
    }

    pub fn fills(&self, order_id: Option<&str>) -> Vec<ExecutionFill> {
        self.fills
            .iter()
            .filter(|fill| order_id.is_none_or(|id| fill.order_id == id))
            .cloned()
            .collect()
    }

    pub fn record_fill(
        &mut self,
        request: ExecutionFillReport,
    ) -> Result<ExecutionOrder, ExecutionError> {
        if request.fill_id.trim().is_empty() || request.order_id.trim().is_empty() {
            return Err(ExecutionError::Invalid("fill identity is required".into()));
        }
        if self
            .fills
            .iter()
            .any(|fill| fill.fill_id == request.fill_id)
        {
            return Err(ExecutionError::Invalid("fill_id already exists".into()));
        }
        if request.quantity_mantissa <= 0 || request.price_mantissa <= 0 {
            return Err(ExecutionError::Invalid(
                "fill quantity and price must be positive".into(),
            ));
        }
        if request.fee_mantissa < 0 {
            return Err(ExecutionError::Invalid(
                "fill fee cannot be negative".into(),
            ));
        }
        let current = self
            .orders
            .get(&request.order_id)
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unknown order".into()))?;
        if current.status.terminal() {
            return Err(ExecutionError::Invalid("order is terminal".into()));
        }
        if current.filled_quantity_scale != request.quantity_scale {
            return Err(ExecutionError::Invalid(
                "fill quantity scale must match order quantity scale".into(),
            ));
        }
        let filled = current
            .filled_quantity_mantissa
            .checked_add(request.quantity_mantissa)
            .ok_or_else(|| ExecutionError::Invalid("filled quantity overflow".into()))?;
        if filled > current.quantity_mantissa {
            return Err(ExecutionError::Invalid(
                "cumulative fill exceeds order quantity".into(),
            ));
        }
        let now = request.occurred_at_unix_nanos.unwrap_or_else(now_nanos);
        let mut next = current.clone();
        next.filled_quantity_mantissa = filled;
        next.updated_at_unix_nanos = now;
        next.status = if filled == next.quantity_mantissa {
            ExecutionOrderStatus::Filled
        } else {
            ExecutionOrderStatus::PartiallyFilled
        };
        let fill = ExecutionFill {
            fill_id: request.fill_id.clone(),
            order_id: next.order_id.clone(),
            intent_id: next.intent_id.clone(),
            instrument_id: next.instrument_id.clone(),
            side: next.side,
            quantity_mantissa: request.quantity_mantissa,
            quantity_scale: request.quantity_scale,
            price_mantissa: request.price_mantissa,
            price_scale: request.price_scale,
            fee_mantissa: request.fee_mantissa,
            fee_scale: request.fee_scale,
            occurred_at_unix_nanos: now,
        };
        self.orders.insert(next.order_id.clone(), next.clone());
        self.fills.push(fill);
        self.commit(ExecutionEvent {
            order_id: next.order_id.clone(),
            status: next.status,
            venue_order_id: next.venue_order_id.clone(),
            occurred_at_unix_nanos: now,
            reason: String::new(),
            fill_id: Some(request.fill_id),
            filled_quantity_mantissa: Some(filled),
            filled_quantity_scale: Some(next.filled_quantity_scale),
        })?;
        Ok(next)
    }

    pub fn submit(&mut self, request: SubmitOrder) -> Result<ExecutionOrder, ExecutionError> {
        if self.live_trading && !self.live_confirmed {
            return Err(ExecutionError::Invalid(
                "live order submission requires explicit confirmation".into(),
            ));
        }
        let now = now_nanos();
        if self.orders.contains_key(&request.order_id) {
            return Err(ExecutionError::Invalid("order_id already exists".into()));
        }
        let mut order = ExecutionOrder::new(
            request.order_id.clone(),
            request.account_id,
            request.segment_key.clone(),
            request.instrument_id,
            request.side,
            request.order_type,
            request.quantity_mantissa,
            request.quantity_scale,
            now,
        )
        .map_err(ExecutionError::Invalid)?;
        order.intent_id = request.intent_id;
        order.market_id = request.market_id;
        order.limit_price_mantissa = request.limit_price_mantissa;
        order.limit_price_scale = request.limit_price_scale;
        order.status = ExecutionOrderStatus::Submitting;
        self.orders.insert(order.order_id.clone(), order.clone());
        self.commit(ExecutionEvent {
            order_id: order.order_id.clone(),
            status: order.status,
            venue_order_id: None,
            occurred_at_unix_nanos: now,
            reason: String::new(),
            fill_id: None,
            filled_quantity_mantissa: None,
            filled_quantity_scale: None,
        })?;
        let event = if let Some(connection) = self.order_entry.as_mut() {
            match to_connection_request(&order, &request.segment_key, &request.options)
                .map_err(ExecutionError::Invalid)
                .and_then(|request| {
                    connection
                        .submit_order(&request)
                        .map_err(ExecutionError::Gateway)
                }) {
                Ok(event) => event,
                Err(error) => {
                    self.mark_unknown_after_gateway_error(&order.order_id, error.to_string())?;
                    return Err(error);
                }
            }
        } else {
            let error = ExecutionError::Gateway("order entry connection is not configured".into());
            self.mark_unknown_after_gateway_error(&order.order_id, error.to_string())?;
            return Err(error);
        };
        apply_connection_event(&mut order, event)?;
        if order.status == ExecutionOrderStatus::Accepted && order.venue_order_id.is_none() {
            order.status = ExecutionOrderStatus::Unknown;
            order.reason = "accepted order did not return a venue order id".into();
        }
        self.orders.insert(order.order_id.clone(), order.clone());
        self.commit(ExecutionEvent {
            order_id: order.order_id.clone(),
            status: order.status,
            venue_order_id: order.venue_order_id.clone(),
            occurred_at_unix_nanos: now,
            reason: String::new(),
            fill_id: None,
            filled_quantity_mantissa: None,
            filled_quantity_scale: None,
        })?;
        Ok(order)
    }

    fn mark_unknown_after_gateway_error(
        &mut self,
        order_id: &str,
        reason: String,
    ) -> Result<(), ExecutionError> {
        let Some(mut order) = self.orders.get(order_id).cloned() else {
            return Ok(());
        };
        let now = now_nanos();
        order.status = ExecutionOrderStatus::Unknown;
        order.reason = reason.clone();
        order.updated_at_unix_nanos = now;
        self.orders.insert(order_id.to_owned(), order.clone());
        self.commit(ExecutionEvent {
            order_id: order_id.to_owned(),
            status: order.status,
            venue_order_id: order.venue_order_id,
            occurred_at_unix_nanos: now,
            reason,
            fill_id: None,
            filled_quantity_mantissa: None,
            filled_quantity_scale: None,
        })
    }

    /// Resolve a target-position intent into one order.  Account remains the
    /// owner of the intent journal; execution only owns this translation and
    /// the resulting exchange lifecycle.
    pub fn execute_intent(
        &mut self,
        request: ExecuteIntent,
    ) -> Result<Option<ExecutionOrder>, ExecutionError> {
        if request.intent_id.trim().is_empty() {
            return Err(ExecutionError::Invalid("intent_id is required".into()));
        }
        let delta = request
            .target_quantity_mantissa
            .checked_sub(request.current_quantity_mantissa)
            .ok_or_else(|| ExecutionError::Invalid("intent quantity overflow".into()))?;
        if delta == 0 {
            return Ok(None);
        }
        let order_type = if request.limit_price_mantissa.is_some() {
            OrderType::Limit
        } else {
            OrderType::Market
        };
        let quantity_mantissa = delta
            .checked_abs()
            .ok_or_else(|| ExecutionError::Invalid("intent quantity overflow".into()))?;
        let order = self.submit(SubmitOrder {
            order_id: request.order_id,
            intent_id: Some(request.intent_id),
            account_id: request.account_id,
            segment_key: request.segment_key,
            instrument_id: request.instrument_id,
            market_id: request.market_id,
            side: if delta > 0 {
                OrderSide::Buy
            } else {
                OrderSide::Sell
            },
            order_type,
            quantity_mantissa,
            quantity_scale: request.quantity_scale,
            limit_price_mantissa: request.limit_price_mantissa,
            limit_price_scale: request.limit_price_scale,
            options: ExecutionOrderOptions::default(),
        })?;
        Ok(Some(order))
    }

    pub fn cancel(&mut self, request: CancelOrder) -> Result<ExecutionOrder, ExecutionError> {
        let order = self
            .orders
            .get(&request.order_id)
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unknown order".into()))?;
        if order.status.terminal() {
            return Err(ExecutionError::Invalid("order is terminal".into()));
        }
        let connection = self.order_entry.as_mut().ok_or_else(|| {
            ExecutionError::Gateway("order entry connection is not configured".into())
        })?;
        let event = match to_connection_request(
            &order,
            &order.segment_key,
            &ExecutionOrderOptions::default(),
        )
        .map_err(ExecutionError::Invalid)
        .and_then(|request| {
            connection
                .cancel_order(
                    &request,
                    order.venue_order_id.as_deref().unwrap_or_default(),
                    now_nanos(),
                )
                .map_err(ExecutionError::Gateway)
        }) {
            Ok(event) => event,
            Err(error) => {
                self.mark_unknown_after_gateway_error(&order.order_id, error.to_string())?;
                return Err(error);
            }
        };
        let now = now_nanos();
        let mut next = order;
        apply_connection_event(&mut next, event)?;
        next.updated_at_unix_nanos = now;
        next.reason = request.reason.clone();
        self.orders.insert(next.order_id.clone(), next.clone());
        self.commit(ExecutionEvent {
            order_id: next.order_id.clone(),
            status: next.status,
            venue_order_id: next.venue_order_id.clone(),
            occurred_at_unix_nanos: now,
            reason: request.reason,
            fill_id: None,
            filled_quantity_mantissa: None,
            filled_quantity_scale: None,
        })?;
        Ok(next)
    }

    pub fn replace(&mut self, request: ReplaceOrder) -> Result<ExecutionOrder, ExecutionError> {
        let current = self
            .orders
            .get(&request.order_id)
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unknown order".into()))?;
        if !current.status.terminal() {
            self.cancel(CancelOrder {
                order_id: request.order_id,
                reason: "replaced".into(),
            })?;
        }
        self.submit(request.replacement)
    }

    fn commit(&mut self, event: ExecutionEvent) -> Result<(), ExecutionError> {
        self.event_sequence += 1;
        self.generation += 1;
        self.events.push(event.clone());
        self.pending_events.push(event);
        let snapshot = self.snapshot();
        if let Some(store) = self.store.as_mut() {
            store.save(&snapshot).map_err(ExecutionError::Persistence)?;
        }
        Ok(())
    }
}

fn to_connection_request(
    order: &ExecutionOrder,
    segment_key: &str,
    options: &ExecutionOrderOptions,
) -> Result<OrderEntryRequest, String> {
    Ok(OrderEntryRequest {
        order_id: order.order_id.clone(),
        intent_id: order.intent_id.clone(),
        account_id: order.account_id.clone(),
        segment_key: segment_key.to_string(),
        instrument_id: order.instrument_id.clone(),
        market_id: order.market_id.clone(),
        side: match order.side {
            OrderSide::Buy => ConnectionOrderSide::Buy,
            OrderSide::Sell => ConnectionOrderSide::Sell,
        },
        quantity: ConnectionDecimalValue::new(order.quantity_mantissa, order.quantity_scale),
        order_type: match order.order_type {
            OrderType::Market => ConnectionOrderType::Market,
            OrderType::Limit => ConnectionOrderType::Limit,
        },
        limit_price: order.limit_price_mantissa.map(|mantissa| {
            ConnectionDecimalValue::new(
                mantissa,
                order.limit_price_scale.unwrap_or(order.quantity_scale),
            )
        }),
        options: ConnectionOrderEntryOptions {
            time_in_force: options
                .time_in_force
                .as_deref()
                .map(parse_time_in_force)
                .transpose()?,
            reduce_only: options.reduce_only,
            post_only: options.post_only,
            position_side: options.position_side.clone(),
            quote_asset: options.quote_asset.clone(),
            wallet_type: options.wallet_type.clone(),
            trading_session: options.trading_session.clone(),
            tokenize: options.tokenize,
        },
    })
}

fn parse_time_in_force(value: &str) -> Result<TimeInForce, String> {
    match value.trim().to_ascii_uppercase().as_str() {
        "GTC" | "GOOD_TIL_CANCELED" => Ok(TimeInForce::GoodTilCanceled),
        "IOC" | "IMMEDIATE_OR_CANCEL" => Ok(TimeInForce::ImmediateOrCancel),
        "FOK" | "FILL_OR_KILL" => Ok(TimeInForce::FillOrKill),
        "DAY" => Ok(TimeInForce::Day),
        _ => Err(format!("unsupported time_in_force: {value}")),
    }
}

fn apply_connection_event(
    order: &mut ExecutionOrder,
    event: OrderEntryEvent,
) -> Result<(), ExecutionError> {
    order.venue_order_id = event.venue_order_id;
    order.updated_at_unix_nanos = event.occurred_at_unix_nanos;
    order.reason = event.reason;
    order.status = match event.status {
        OrderEntryStatus::Accepted => ExecutionOrderStatus::Accepted,
        OrderEntryStatus::PartiallyFilled => ExecutionOrderStatus::PartiallyFilled,
        OrderEntryStatus::Filled => ExecutionOrderStatus::Filled,
        OrderEntryStatus::Canceled => ExecutionOrderStatus::Canceled,
        OrderEntryStatus::Rejected => ExecutionOrderStatus::Rejected,
        OrderEntryStatus::Expired => ExecutionOrderStatus::Expired,
        OrderEntryStatus::Unknown => ExecutionOrderStatus::Unknown,
    };
    Ok(())
}

fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

fn remote_order(order: kairos_integration::application::ExternalOrder) -> RemoteOrder {
    RemoteOrder {
        order_id: order.order_id,
        client_order_id: order.client_order_id,
        symbol: order.symbol,
        side: match order.side {
            kairos_integration::domain::OrderSide::Buy => OrderSide::Buy,
            kairos_integration::domain::OrderSide::Sell => OrderSide::Sell,
        },
        order_type: match order.order_type {
            kairos_integration::domain::OrderType::Market => OrderType::Market,
            _ => OrderType::Limit,
        },
        status: order.status,
        quantity: format_decimal(order.quantity),
        filled_quantity: format_decimal(order.filled_quantity),
        average_fill_price: order.average_fill_price.map(format_decimal),
        occurred_at_unix_millis: order.occurred_at_unix_millis,
    }
}

fn remote_execution_event(
    event: kairos_integration::application::ExternalExecutionEvent,
) -> RemoteExecutionEvent {
    RemoteExecutionEvent {
        order_id: event.order_id,
        symbol: event.symbol,
        status: event.status,
        fill_quantity: event.fill_quantity.map(format_decimal),
        fill_price: event.fill_price.map(format_decimal),
        execution_id: event.execution_id,
        fee_currency: event.fee_currency,
        fee_amount: event.fee_amount.map(format_decimal),
        occurred_at_unix_nanos: event.occurred_at_unix_nanos,
        reason: event.reason,
    }
}

fn format_decimal(value: kairos_integration::domain::DecimalValue) -> String {
    if value.scale == 0 {
        return value.mantissa.to_string();
    }
    let negative = value.mantissa < 0;
    let digits = value.mantissa.unsigned_abs().to_string();
    let scale = value.scale as usize;
    let padded = format!("{digits:0>width$}", width = scale + 1);
    let split = padded.len() - scale;
    format!(
        "{}{}.{}",
        if negative { "-" } else { "" },
        &padded[..split],
        &padded[split..]
    )
}

fn parse_decimal(value: &str) -> Result<(i64, u8), ExecutionError> {
    let value = value.trim();
    let negative = value.starts_with('-');
    let unsigned = value.trim_start_matches('-');
    let (whole, fraction) = unsigned.split_once('.').unwrap_or((unsigned, ""));
    let mantissa = format!("{whole}{fraction}")
        .parse::<i64>()
        .map_err(|_| ExecutionError::Invalid(format!("invalid decimal value: {value}")))?;
    Ok((
        if negative { -mantissa } else { mantissa },
        fraction.len() as u8,
    ))
}

fn remote_status(value: &str) -> ExecutionOrderStatus {
    let normalized = value.to_ascii_lowercase();
    if normalized.contains("fill") {
        ExecutionOrderStatus::Filled
    } else if normalized.contains("cancel") {
        ExecutionOrderStatus::Canceled
    } else if normalized.contains("reject") {
        ExecutionOrderStatus::Rejected
    } else if normalized.contains("expire") {
        ExecutionOrderStatus::Expired
    } else if normalized.contains("submit") || normalized.contains("accept") {
        ExecutionOrderStatus::Accepted
    } else {
        ExecutionOrderStatus::Unknown
    }
}

fn audit_matches(event: &ExecutionAuditEvent, query: &ExecutionAuditQuery) -> bool {
    query
        .order_id
        .as_deref()
        .is_none_or(|value| event.order_id == value)
        && query
            .venue_order_id
            .as_deref()
            .is_none_or(|value| event.venue_order_id.as_deref() == Some(value))
        && query
            .status
            .as_deref()
            .is_none_or(|value| event.status.eq_ignore_ascii_case(value))
        && query
            .since_unix_nanos
            .is_none_or(|value| event.occurred_at_unix_nanos >= value)
        && query
            .until_unix_nanos
            .is_none_or(|value| event.occurred_at_unix_nanos <= value)
}
