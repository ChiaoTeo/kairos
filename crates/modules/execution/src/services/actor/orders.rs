use super::*;

impl ExecutionActor {
    pub(crate) fn unknown_remote_orders(&self) -> impl Iterator<Item = &UnknownRemoteOrder> {
        self.unknown_remote_orders.values()
    }

    pub(crate) fn drain_events(&mut self) -> Vec<ExecutionEvent> {
        std::mem::take(&mut self.pending_events)
    }

    pub(crate) fn contains_order(&self, order_id: &str) -> bool {
        self.orders.contains_key(order_id)
    }

    pub(crate) fn order(&self, order_id: &str) -> Option<&ExecutionOrder> {
        self.orders.get(order_id)
    }

    pub(crate) fn prepare_submission(
        &mut self,
        request: &SubmitOrder,
        commitment: OrderCommitment,
        risk_reservation: RiskReservationEvidence,
        now: u64,
    ) -> Result<(ExecutionOrder, ExecutionEvent), String> {
        if self.contains_order(request.order_id.as_str()) {
            return Err("order_id already exists".into());
        }
        let mut order = ExecutionOrder::new(
            request.order_id.to_string(),
            request.account_id.to_string(),
            request.segment_key.to_string(),
            request.instrument_id.to_string(),
            request.side,
            request.order_type,
            request.quantity,
            now,
        )?;
        order.intent_id = request.intent_id.clone();
        order.strategy_id = request.strategy_id.clone();
        order.market_id = request.market_id.clone();
        order.execution_access_id = request.execution_access_id.clone();
        order.limit_price = request.limit_price;
        order.status = ExecutionOrderStatus::Pending;
        let event = order_event(&order, now, String::new());
        self.orders.insert(order.order_id.clone(), order.clone());
        self.commitments.insert(order.order_id.clone(), commitment);
        self.risk_reservations
            .insert(order.order_id.clone(), risk_reservation);
        Ok((order, event))
    }

    pub(crate) fn activate_submission(
        &mut self,
        order_id: &str,
        reservation: RiskReservationEvidence,
        now: u64,
    ) -> Result<(ExecutionOrder, ExecutionEvent), String> {
        let mut order = self
            .orders
            .get(order_id)
            .cloned()
            .ok_or_else(|| "unknown order".to_string())?;
        if order.status != ExecutionOrderStatus::Pending {
            return Err("order is not pending admission".into());
        }
        order.status = ExecutionOrderStatus::Submitting;
        order.updated_at_unix_nanos = now.into();
        order.reason.clear();
        self.orders.insert(order.order_id.clone(), order.clone());
        self.risk_reservations
            .insert(order.order_id.clone(), reservation);
        Ok((order.clone(), order_event(&order, now, String::new())))
    }

    pub(crate) fn set_risk_reservation_status(
        &mut self,
        order_id: &str,
        status: RiskReservationSagaStatus,
        now: u64,
    ) {
        if let Some(reservation) = self.risk_reservations.get_mut(order_id) {
            reservation.status = status;
            reservation.updated_at_unix_nanos = now.into();
        }
    }

    pub(crate) fn set_risk_reservation_amount(&mut self, order_id: &str, amount: Money, now: u64) {
        if let Some(reservation) = self.risk_reservations.get_mut(order_id) {
            reservation.amount = amount;
            reservation.updated_at_unix_nanos = now.into();
        }
    }

    pub(crate) fn reconcile_risk_reservation(&mut self, evidence: RiskReservationEvidence) {
        self.risk_reservations
            .insert(evidence.order_id.clone(), evidence);
    }

    pub(crate) fn set_commitment_status(
        &mut self,
        order_id: &str,
        status: CommitmentStatus,
        now: u64,
    ) {
        if let Some(commitment) = self.commitments.get_mut(order_id) {
            commitment.status = status;
            commitment.updated_at_unix_nanos = now.into();
        }
    }

    pub(crate) fn resize_commitment(
        &mut self,
        order_id: &str,
        remaining: Quantity,
        now: u64,
    ) -> Result<(), String> {
        let Some(commitment) = self.commitments.get_mut(order_id) else {
            return Ok(());
        };
        if remaining == Quantity::ZERO {
            commitment.remaining_quantity = remaining;
            commitment.amount = Money::ZERO;
            commitment.status = CommitmentStatus::Released;
            commitment.updated_at_unix_nanos = now.into();
            return Ok(());
        }
        let amount = match commitment.basis {
            CommitmentBasis::QuotePriceCap { price_cap } => remaining
                .checked_mul(price_cap)
                .map_err(|error| error.to_string())?,
            CommitmentBasis::ContractNotional { .. } => {
                return Err(
                    "contract commitment resizing requires product-specific semantics".into(),
                )
            }
            CommitmentBasis::BaseQuantity | CommitmentBasis::SimulationQuantity => {
                Money::new(remaining.mantissa(), remaining.scale())
                    .map_err(|error| error.to_string())?
            }
        };
        commitment.remaining_quantity = remaining;
        commitment.amount = amount;
        commitment.status = CommitmentStatus::Reduced;
        commitment.updated_at_unix_nanos = now.into();
        Ok(())
    }

    pub(crate) fn apply_order_entry_event(
        &mut self,
        order_id: &str,
        event: OrderEntryEvent,
    ) -> Result<(ExecutionOrder, ExecutionEvent), String> {
        let mut order = self
            .order(order_id)
            .cloned()
            .ok_or_else(|| "unknown order".to_string())?;
        let occurred_at = event.occurred_at_unix_nanos;
        crate::application::apply_connection_event(&mut order, event)
            .map_err(|error| error.to_string())?;
        if order.status == ExecutionOrderStatus::Accepted && order.remote_order_id.is_none() {
            order.status = ExecutionOrderStatus::Unknown;
            order.reason = "accepted order did not return a exchange order id".into();
        }
        let persisted = order_event(&order, occurred_at.get(), String::new());
        self.orders.insert(order.order_id.clone(), order.clone());
        Ok((order, persisted))
    }

    pub(crate) fn mark_delivery_status(
        &mut self,
        order_id: &str,
        status: ExecutionOrderStatus,
        reason: String,
        now: u64,
    ) -> Option<(ExecutionOrder, ExecutionEvent)> {
        let mut order = self.order(order_id)?.clone();
        order.status = status;
        order.reason = reason.clone();
        order.updated_at_unix_nanos = UnixNanos::new(now);
        let event = order_event(&order, now, reason);
        self.orders.insert(order.order_id.clone(), order.clone());
        Some((order, event))
    }
}
