use super::*;

impl ExecutionActor {
    pub(crate) fn record_fill(
        &mut self,
        request: &ExecutionFillReport,
        occurred_at: u64,
        applied_at: u64,
    ) -> Result<FillTransition, OrderError> {
        if let Some(existing) = self
            .fills
            .iter()
            .find(|fill| fill.fill_id == request.fill_id)
            .cloned()
        {
            let same_fact = existing.order_id == request.order_id
                && existing.quantity == request.quantity
                && existing.price == request.price
                && existing.fee == request.fee
                && existing.fee_currency == request.fee_currency
                && existing.execution_market_id == request.execution_market_id
                && existing.reported_broker_id == request.reported_broker_id
                && existing.execution_channel == request.execution_channel
                && existing.order_entry_symbol == request.order_entry_symbol
                && request
                    .remote_order_id
                    .as_ref()
                    .is_none_or(|value| existing.remote_order_id.as_ref() == Some(value))
                && request
                    .occurred_at_unix_nanos
                    .is_none_or(|value| value == existing.occurred_at_unix_nanos);
            if same_fact {
                let order = self
                    .orders
                    .get_mut(request.order_id.as_str())
                    .ok_or_else(|| OrderError::UnknownOrder {
                        order_id: request.order_id.to_string(),
                    })?;
                let cursor_changed = request.source_cursor.as_ref().is_some_and(|incoming| {
                    if order
                        .last_order_fact_cursor
                        .as_ref()
                        .is_some_and(|previous| incoming.regresses(previous))
                        || order.last_order_fact_cursor.as_ref() == Some(incoming)
                    {
                        return false;
                    }
                    order.last_order_fact_cursor = Some(incoming.clone());
                    true
                });
                return Ok(FillTransition::Duplicate {
                    order: order.clone(),
                    cursor_changed,
                });
            }
            return Ok(FillTransition::Conflict(existing));
        }
        if request.quantity.mantissa() <= 0 || request.price.mantissa() <= 0 {
            return Err(OrderError::InvalidFill {
                fill_id: request.fill_id.to_string(),
                failure: crate::domain::FillValidationFailure::QuantityOrPriceNotPositive,
            });
        }
        if request.fee.mantissa() < 0 {
            return Err(OrderError::InvalidFill {
                fill_id: request.fill_id.to_string(),
                failure: crate::domain::FillValidationFailure::FeeNegative,
            });
        }
        let current = self
            .order(request.order_id.as_str())
            .cloned()
            .ok_or_else(|| OrderError::UnknownOrder {
                order_id: request.order_id.to_string(),
            })?;
        if current.status.terminal() {
            return Err(OrderError::TerminalOrder {
                order_id: current.order_id,
                status: current.status,
            });
        }
        let filled = current
            .filled_quantity
            .checked_add(request.quantity)
            .map_err(|source| OrderError::Arithmetic {
                operation: "cumulative fill addition",
                source,
            })?;
        if filled > current.quantity {
            return Err(OrderError::FillExceedsQuantity {
                order_id: current.order_id.clone(),
                cumulative: filled,
                ordered: current.quantity,
            });
        }
        let mut order = current;
        if let Some(remote_order_id) = request.remote_order_id.as_ref() {
            if order
                .remote_order_id
                .as_ref()
                .is_some_and(|current| current != remote_order_id)
            {
                return Err(OrderError::RemoteIdentityConflict {
                    order_id: order.order_id,
                    expected: order.remote_order_id,
                    actual: remote_order_id.clone(),
                });
            }
            order.remote_order_id = Some(remote_order_id.clone());
        }
        order.filled_quantity = filled;
        order.updated_at_unix_nanos = UnixNanos::new(applied_at);
        order.status = if filled == order.quantity {
            ExecutionOrderStatus::Filled
        } else {
            ExecutionOrderStatus::PartiallyFilled
        };
        order.reconciliation_cause = None;
        if let Some(source_cursor) = request.source_cursor.as_ref() {
            order.last_order_fact_cursor = Some(source_cursor.clone());
        }
        let fill = ExecutionFill {
            fill_id: request.fill_id.clone(),
            order_id: order.order_id.clone(),
            plan_id: order.plan_id.clone(),
            leg_id: order.leg_id.clone(),
            intent_id: order.intent_id.clone(),
            instrument_id: order.instrument_id.clone(),
            execution_market_id: request.execution_market_id.clone(),
            reported_broker_id: request.reported_broker_id.clone(),
            execution_channel: request.execution_channel.clone(),
            order_entry_symbol: request.order_entry_symbol.clone(),
            remote_order_id: request
                .remote_order_id
                .clone()
                .or_else(|| order.remote_order_id.clone()),
            side: order.side,
            quantity: request.quantity,
            price: request.price,
            fee: request.fee,
            fee_currency: request.fee_currency.clone(),
            source_cursor: request.source_cursor.clone(),
            occurred_at_unix_nanos: UnixNanos::new(occurred_at),
        };
        let mut event = order_event(&order, applied_at, String::new());
        event.fill_id = Some(request.fill_id.clone());
        event.filled_quantity = Some(filled);
        self.orders.insert(order.order_id.clone(), order.clone());
        self.fills.push(fill.clone());
        Ok(FillTransition::Applied { order, fill, event })
    }
}
