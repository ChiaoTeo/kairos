use super::*;
use crate::domain::RemoteOrderUpdate;

impl ExecutionActor {
    pub(crate) fn observe_remote_time(&mut self, observed_at: u64) {
        self.exchange_event_watermark_unix_nanos =
            self.exchange_event_watermark_unix_nanos.max(observed_at);
    }

    pub(crate) fn remote_watermark(&self) -> u64 {
        self.exchange_event_watermark_unix_nanos
    }

    pub(crate) fn observe_order_fact_cursor(
        &mut self,
        order_id: &str,
        cursor: crate::domain::OrderFactCursor,
    ) -> Result<bool, OrderError> {
        let order = self
            .orders
            .get_mut(order_id)
            .ok_or_else(|| OrderError::UnknownOrder {
                order_id: order_id.to_owned(),
            })?;
        if order.last_order_fact_cursor.as_ref() == Some(&cursor) {
            return Ok(false);
        }
        order.last_order_fact_cursor = Some(cursor);
        Ok(true)
    }

    pub(crate) fn find_remote_order(
        &self,
        remote_order_id: &str,
        client_order_id: Option<&str>,
    ) -> Option<ExecutionOrder> {
        self.orders
            .values()
            .find(|order| {
                client_order_id.is_some_and(|client| order.order_id.as_str() == client)
                    || order.remote_order_id.as_deref() == Some(remote_order_id)
            })
            .cloned()
    }

    pub(crate) fn reconcile_order(
        &mut self,
        local_order_id: &str,
        remote_order_id: &str,
        status: ExecutionOrderStatus,
        occurred_at: u64,
        reason: String,
        source_cursor: Option<crate::domain::OrderFactCursor>,
    ) -> Result<(ExecutionOrder, ExecutionEvent), OrderError> {
        let mut order =
            self.order(local_order_id)
                .cloned()
                .ok_or_else(|| OrderError::UnknownOrder {
                    order_id: local_order_id.to_owned(),
                })?;
        order.remote_order_id = Some(
            crate::domain::RemoteOrderId::new(remote_order_id.to_owned()).map_err(|source| {
                OrderError::InvalidSemantic {
                    field: "remote_order_id",
                    source,
                }
            })?,
        );
        order.status = status;
        order.reconciliation_cause = match status {
            ExecutionOrderStatus::Unknown => {
                Some(crate::domain::OrderReconciliationCause::AuthoritativeFactConflict)
            },
            _ => None,
        };
        order.updated_at_unix_nanos = UnixNanos::new(occurred_at);
        order.reason = reason.clone();
        if let Some(source_cursor) = source_cursor {
            order.last_order_fact_cursor = Some(source_cursor);
        }
        if status != ExecutionOrderStatus::Unknown {
            if let Some(cancel_attempt) = order.attempts.iter_mut().rev().find(|attempt| {
                attempt.command == crate::domain::ExecutionCommandKind::Cancel
                    && attempt.delivery_certainty == crate::domain::DeliveryCertainty::Indeterminate
            }) {
                cancel_attempt.delivery_certainty = crate::domain::DeliveryCertainty::Reconciled;
            }
        }
        let event = order_event(&order, occurred_at, reason);
        self.orders.insert(order.order_id.clone(), order.clone());
        Ok((order, event))
    }

    pub(crate) fn mark_authoritative_fact_conflict(
        &mut self,
        local_order_id: &str,
        remote_order_id: Option<&str>,
        applied_at: u64,
        reason: String,
        source_cursor: Option<crate::domain::OrderFactCursor>,
    ) -> Result<(ExecutionOrder, ExecutionEvent), OrderError> {
        let mut order =
            self.order(local_order_id)
                .cloned()
                .ok_or_else(|| OrderError::UnknownOrder {
                    order_id: local_order_id.to_owned(),
                })?;
        if let Some(remote_order_id) = remote_order_id {
            let remote_order_id = crate::domain::RemoteOrderId::new(remote_order_id.to_owned())
                .map_err(|source| OrderError::InvalidSemantic {
                    field: "remote_order_id",
                    source,
                })?;
            if order
                .remote_order_id
                .as_ref()
                .is_some_and(|current| current != &remote_order_id)
            {
                return Err(OrderError::RemoteIdentityConflict {
                    order_id: order.order_id,
                    expected: order.remote_order_id,
                    actual: remote_order_id,
                });
            }
            order.remote_order_id = Some(remote_order_id);
        }
        order.status = ExecutionOrderStatus::Unknown;
        order.reconciliation_cause =
            Some(crate::domain::OrderReconciliationCause::AuthoritativeFactConflict);
        order.updated_at_unix_nanos = applied_at.into();
        order.reason = reason.clone();
        if let Some(source_cursor) = source_cursor {
            order.last_order_fact_cursor = Some(source_cursor);
        }
        let event = order_event(&order, applied_at, reason);
        self.orders.insert(order.order_id.clone(), order.clone());
        Ok((order, event))
    }

    pub(crate) fn resolve_unknown_remote_order(
        &mut self,
        remote_order_id: &str,
        resolution: UnknownRemoteOrderResolution,
        reason: String,
        now: u64,
    ) -> Result<(), OrderError> {
        let order = self
            .unknown_remote_orders
            .get_mut(remote_order_id)
            .ok_or_else(|| OrderError::UnknownRemoteOrder {
                remote_order_id: remote_order_id.to_owned(),
            })?;
        order.resolution = resolution;
        order.reason = reason;
        order.last_seen_at_unix_nanos = now.into();
        Ok(())
    }

    pub(crate) fn link_unknown_remote_order(
        &mut self,
        remote_order_id: &str,
        local_order_id: &str,
    ) -> Result<(UnknownRemoteOrder, ExecutionOrder, ExecutionEvent), OrderError> {
        let unknown = self
            .unknown_remote_orders
            .get(remote_order_id)
            .cloned()
            .ok_or_else(|| OrderError::UnknownRemoteOrder {
                remote_order_id: remote_order_id.to_owned(),
            })?;
        let (order, event) = self.reconcile_order(
            local_order_id,
            remote_order_id,
            unknown.status,
            unknown.last_seen_at_unix_nanos.get(),
            "linked from unknown remote order reconciliation".into(),
            unknown.source_cursor.clone(),
        )?;
        self.resolve_unknown_remote_order(
            remote_order_id,
            UnknownRemoteOrderResolution::LinkedToLocalOrder,
            format!("linked to local order {local_order_id}"),
            unknown.last_seen_at_unix_nanos.get(),
        )?;
        Ok((unknown, order, event))
    }

    pub(crate) fn attach_plan_identity(
        &mut self,
        order_id: &str,
        plan_id: crate::domain::PlanId,
        leg_id: crate::domain::LegId,
    ) {
        if let Some(order) = self.orders.get_mut(order_id) {
            order.plan_id = Some(plan_id);
            order.leg_id = Some(leg_id);
        }
    }

    pub(crate) fn commit_event(&mut self, event: ExecutionEvent) -> ExecutionEvent {
        self.event_sequence += 1;
        self.generation += 1;
        self.events.push(event.clone());
        self.pending_events.push(event.clone());
        event
    }

    pub(crate) fn record_unknown_remote_order(&mut self, event: &RemoteOrderUpdate) {
        if self
            .unknown_remote_orders
            .get(event.remote_order_id.as_str())
            .and_then(|order| order.source_cursor.as_ref())
            .zip(event.source_cursor.as_ref())
            .is_some_and(|(previous, incoming)| incoming.regresses(previous))
        {
            return;
        }
        let remote_order_id = event.remote_order_id.clone();
        let entry = self
            .unknown_remote_orders
            .entry(event.remote_order_id.to_string())
            .or_insert_with(|| UnknownRemoteOrder {
                remote_order_id: remote_order_id.clone(),
                symbol: event.symbol.clone(),
                status: event.status,
                execution_id: event.execution_id.clone(),
                fill_quantity: event.fill_quantity,
                fill_price: event.fill_price,
                fee_currency: event.fee_currency.clone(),
                fee_amount: event.fee_amount,
                source_cursor: event.source_cursor.clone(),
                first_seen_at_unix_nanos: event.occurred_at_unix_nanos,
                last_seen_at_unix_nanos: event.occurred_at_unix_nanos,
                resolution: UnknownRemoteOrderResolution::Pending,
                reason: event.reason.clone(),
            });
        entry.symbol = event.symbol.clone();
        entry.status = event.status;
        entry.execution_id = event.execution_id.clone();
        entry.fill_quantity = event.fill_quantity;
        entry.fill_price = event.fill_price;
        entry.fee_currency = event.fee_currency.clone();
        entry.fee_amount = event.fee_amount;
        entry.source_cursor = event.source_cursor.clone();
        entry.last_seen_at_unix_nanos = event.occurred_at_unix_nanos;
        entry.reason = event.reason.clone();
    }
}
