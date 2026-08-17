use super::*;

impl ExecutionActor {
    pub(crate) fn record_fill(
        &mut self,
        request: &ExecutionFillReport,
        now: u64,
    ) -> Result<FillTransition, String> {
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
                && existing.reported_provider_id == request.reported_provider_id
                && existing.provider_product == request.provider_product
                && existing.provider_symbol == request.provider_symbol
                && request
                    .remote_order_id
                    .as_ref()
                    .is_none_or(|value| existing.remote_order_id.as_ref() == Some(value))
                && request
                    .occurred_at_unix_nanos
                    .is_none_or(|value| value == existing.occurred_at_unix_nanos);
            if same_fact {
                let order = self
                    .order(request.order_id.as_str())
                    .cloned()
                    .ok_or_else(|| "duplicate fill references unknown order".to_string())?;
                return Ok(FillTransition::Duplicate(order));
            }
            return Ok(FillTransition::Conflict(existing));
        }
        if request.quantity.mantissa() <= 0 || request.price.mantissa() <= 0 {
            return Err("fill quantity and price must be positive".into());
        }
        if request.fee.mantissa() < 0 {
            return Err("fill fee cannot be negative".into());
        }
        let current = self
            .order(request.order_id.as_str())
            .cloned()
            .ok_or_else(|| "unknown order".to_string())?;
        if current.status.terminal() {
            return Err("order is terminal".into());
        }
        let filled = current
            .filled_quantity
            .checked_add(request.quantity)
            .map_err(|error| error.to_string())?;
        if filled > current.quantity {
            return Err("cumulative fill exceeds order quantity".into());
        }
        let mut order = current;
        order.filled_quantity = filled;
        order.updated_at_unix_nanos = UnixNanos::new(now);
        order.status = if filled == order.quantity {
            ExecutionOrderStatus::Filled
        } else {
            ExecutionOrderStatus::PartiallyFilled
        };
        let fill = ExecutionFill {
            fill_id: request.fill_id.clone(),
            order_id: order.order_id.clone(),
            plan_id: order.plan_id.clone(),
            leg_id: order.leg_id.clone(),
            intent_id: order.intent_id.clone(),
            instrument_id: order.instrument_id.clone(),
            execution_market_id: request.execution_market_id.clone(),
            reported_provider_id: request.reported_provider_id.clone(),
            provider_product: request.provider_product.clone(),
            provider_symbol: request.provider_symbol.clone(),
            remote_order_id: request
                .remote_order_id
                .clone()
                .or_else(|| order.remote_order_id.clone()),
            side: order.side,
            quantity: request.quantity,
            price: request.price,
            fee: request.fee,
            fee_currency: request.fee_currency.clone(),
            occurred_at_unix_nanos: UnixNanos::new(now),
        };
        let mut event = order_event(&order, now, String::new());
        event.fill_id = Some(request.fill_id.clone());
        event.filled_quantity = Some(filled);
        self.orders.insert(order.order_id.clone(), order.clone());
        self.fills.push(fill.clone());
        Ok(FillTransition::Applied { order, fill, event })
    }
}
