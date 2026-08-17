//! Order lifecycle use cases for the Execution application facade.

pub(crate) mod admission;

use super::*;

impl ExecutionApplication {
    pub fn preview_submit(&self, request: &SubmitOrder) -> Result<ExecutionOrder, ExecutionError> {
        let mut order = ExecutionOrder::new(
            request.order_id.to_string(),
            request.account_id.to_string(),
            request.segment_key.to_string(),
            request.instrument_id.to_string(),
            request.side,
            request.order_type,
            request.quantity,
            now_nanos(),
        )
        .map_err(ExecutionError::Invalid)?;
        order.intent_id = request.intent_id.clone();
        order.strategy_id = request.strategy_id.clone();
        order.market_id = request.market_id.clone();
        order.limit_price = request.limit_price;
        order.reason = "dry-run preview".into();
        Ok(order)
    }

    pub fn record_fill(
        &mut self,
        request: ExecutionFillReport,
    ) -> Result<ExecutionOrder, ExecutionError> {
        info!(event = "fill_received", component = "execution", fill_id = %request.fill_id, order_id = %request.order_id, "execution fill received");
        let now = request
            .occurred_at_unix_nanos
            .unwrap_or_else(|| now_nanos().into());
        let transition = self
            .actor
            .record_fill(&request, now.get())
            .map_err(ExecutionError::Invalid)?;
        let (next, fill, event) = match transition {
            crate::services::actor::FillTransition::Duplicate(order) => return Ok(order),
            crate::services::actor::FillTransition::Conflict(existing) => {
                if let Some(intent_id) = existing.intent_id.as_deref() {
                    if let Some(state) = self.actor.intent(intent_id).cloned() {
                        self.commit_intent(IntentEvent {
                            intent_id: typed_intent_id(intent_id),
                            event_sequence: 0.into(),
                            status: IntentStatus::ReconciliationRequired,
                            order_ids: Vec::new(),
                            completed_quantity: state.completed_quantity,
                            occurred_at_unix_nanos: now_nanos().into(),
                            reason: format!("conflicting duplicate fill: {}", request.fill_id),
                            dependency_watermarks: state.dependency_watermarks,
                        })?;
                    }
                }
                return Err(ExecutionError::Invalid("conflicting duplicate fill".into()));
            }
            crate::services::actor::FillTransition::Applied { order, fill, event } => {
                (order, fill, event)
            }
        };
        if next.status == ExecutionOrderStatus::Filled {
            self.actor.set_commitment_status(
                next.order_id.as_str(),
                CommitmentStatus::Released,
                fill.occurred_at_unix_nanos.get(),
            );
            self.actor.set_risk_reservation_status(
                next.order_id.as_str(),
                RiskReservationSagaStatus::ConsumePending,
                fill.occurred_at_unix_nanos.get(),
            );
        } else if next.status == ExecutionOrderStatus::PartiallyFilled {
            let remaining = next
                .quantity
                .checked_sub(next.filled_quantity)
                .map_err(|error| ExecutionError::Invalid(error.to_string()))?;
            self.actor
                .resize_commitment(
                    next.order_id.as_str(),
                    remaining,
                    fill.occurred_at_unix_nanos.get(),
                )
                .map_err(ExecutionError::Invalid)?;
            self.actor.set_risk_reservation_status(
                next.order_id.as_str(),
                RiskReservationSagaStatus::ResizePending,
                fill.occurred_at_unix_nanos.get(),
            );
        }
        self.commit(event)?;
        if next.status == ExecutionOrderStatus::Filled {
            self.complete_risk_consume(next.order_id.as_str(), fill.occurred_at_unix_nanos.get())?;
        } else if next.status == ExecutionOrderStatus::PartiallyFilled {
            let remaining = next
                .quantity
                .checked_sub(next.filled_quantity)
                .map_err(|error| ExecutionError::Invalid(error.to_string()))?;
            self.complete_risk_resize(
                next.order_id.as_str(),
                remaining,
                fill.occurred_at_unix_nanos.get(),
            )?;
        }
        if let Some(intent_id) = next.intent_id.as_deref() {
            self.refresh_intent(intent_id)?;
            self.maybe_submit_compensating_hedge(intent_id)?;
        }
        info!(event = "fill_applied", component = "execution", fill_id = %fill.fill_id, order_id = %next.order_id, status = ?next.status, filled_quantity = next.filled_quantity.mantissa(), "execution fill applied");
        Ok(next)
    }

    pub fn prepare_submission(
        &mut self,
        request: SubmitOrder,
    ) -> Result<(ExecutionOrder, OrderEntryRequest), ExecutionError> {
        info!(event = "order_submission_preparing", component = "execution", order_id = %request.order_id, intent_id = ?request.intent_id, account_id = %request.account_id, instrument_id = %request.instrument_id, live_trading = self.live_trading, "order submission preparing");
        if self.live_trading && !self.live_confirmed {
            return Err(ExecutionError::Invalid(
                "live order submission requires explicit confirmation".into(),
            ));
        }
        if self.live_trading && !self.writer_recovery_ready {
            return Err(ExecutionError::Invalid(
                "live order submission is blocked until writer takeover reconciliation completes"
                    .into(),
            ));
        }
        if self.live_trading && !self.risk_recovery_ready {
            return Err(ExecutionError::Invalid(format!(
                "live order submission is blocked by Risk recovery{}",
                self.risk_recovery_error
                    .as_deref()
                    .map(|error| format!(": {error}"))
                    .unwrap_or_default()
            )));
        }
        let now = request
            .submitted_at_unix_nanos
            .map(UnixNanos::get)
            .unwrap_or_else(now_nanos);
        if self.actor.contains_order(request.order_id.as_str()) {
            return Err(ExecutionError::Invalid("order_id already exists".into()));
        }
        let execution_route_id = request.execution_route_id.as_ref().ok_or_else(|| {
            ExecutionError::Invalid(
                "execution_route_id is required; provider identity is not inferred".into(),
            )
        })?;
        let route = self
            .execution_routes
            .get(execution_route_id)
            .ok_or_else(|| {
                ExecutionError::Invalid(format!(
                    "execution route is not configured: {execution_route_id}"
                ))
            })?;
        validate_execution_route(&request, &route.candidate).map_err(ExecutionError::Invalid)?;
        let selected_route = crate::domain::SelectedExecutionRoute {
            route_id: route.candidate.route_id.clone(),
            participant_id: route.candidate.participant_id.clone(),
            provider_product: route.candidate.provider_product.clone(),
            provider_symbol: route.candidate.provider_symbol.clone(),
            destination_market_id: route.candidate.market_id.clone(),
            selected_at_unix_nanos: now.into(),
            selection_kind: crate::domain::RouteSelectionKind::Explicit,
        };
        request
            .options
            .time_in_force
            .as_deref()
            .map(parse_time_in_force)
            .transpose()
            .map_err(ExecutionError::Invalid)?;
        let active_commitments = self.actor.commitments().cloned().collect::<Vec<_>>();
        let (commitment, dependency_watermarks, risk_context) =
            if let Some(admission) = self.order_admission.as_mut() {
                let commitment = admission
                    .validate_order(&request, &active_commitments, now)
                    .map_err(ExecutionError::Invalid)?;
                let risk_context = admission
                    .risk_authorization_context(&request)
                    .map_err(ExecutionError::Invalid)?;
                (commitment, admission.dependency_watermarks(), risk_context)
            } else if self.live_trading {
                return Err(ExecutionError::Invalid(
                    "live order submission requires configured order admission".into(),
                ));
            } else {
                (
                    simulation_commitment(&request, now)?,
                    DependencyWatermarks::default(),
                    crate::application::RiskAuthorizationContext::default(),
                )
            };
        let planned_reservation =
            planned_risk_reservation(&request, &commitment, &dependency_watermarks, now);
        let options = request.options.clone();
        let (_, pending_event) = self
            .actor
            .prepare_submission(
                &request,
                selected_route,
                commitment,
                planned_reservation,
                now,
            )
            .map_err(ExecutionError::Invalid)?;
        // Persist the stable reservation identity and commitment before the
        // Risk command can possibly be sent. Recovery can therefore reconcile
        // an authorization whose response was lost without inventing an ID.
        self.commit(pending_event)?;
        let risk_reservation = match self.risk_reservations.as_mut() {
            Some(risk_reservations) => match risk_reservations.authorize(&request, &risk_context) {
                Ok(reservation) => reservation,
                Err(error) => {
                    let indeterminate = error.may_have_been_applied();
                    self.actor.set_risk_reservation_status(
                        request.order_id.as_str(),
                        if indeterminate {
                            RiskReservationSagaStatus::Uncertain
                        } else {
                            RiskReservationSagaStatus::Failed
                        },
                        now,
                    );
                    self.persist_snapshot()?;
                    return Err(if indeterminate {
                        ExecutionError::Indeterminate(error.to_string())
                    } else {
                        ExecutionError::Invalid(error.to_string())
                    });
                }
            },
            None if !self.live_trading => simulation_risk_reservation(&request, now)?,
            None => {
                return Err(ExecutionError::Invalid(
                    "live order submission requires configured Risk reservations".into(),
                ))
            }
        };
        let (order, submitting_event) = self
            .actor
            .activate_submission(request.order_id.as_str(), risk_reservation, now)
            .map_err(ExecutionError::Invalid)?;
        self.commit(submitting_event)?;
        let connection_request = to_connection_request(
            &order,
            &request.segment_key,
            &options,
            &self.execution_routes,
        )
        .map_err(ExecutionError::Invalid)?;
        Ok((order, connection_request))
    }

    /// Apply a provider response or private-stream update to one local order.
    /// The gateway worker calls this through the exchange event mailbox; it does
    /// not call the provider from inside this method.
    pub fn apply_order_entry_event(
        &mut self,
        order_id: &str,
        event: OrderEntryEvent,
    ) -> Result<ExecutionOrder, ExecutionError> {
        let (order, persisted_event) = self
            .actor
            .apply_order_entry_event(order_id, event)
            .map_err(ExecutionError::Invalid)?;
        self.update_commitment_from_order(&order, persisted_event.occurred_at_unix_nanos.get())?;
        let risk_effect = match order.status {
            ExecutionOrderStatus::Rejected
            | ExecutionOrderStatus::Canceled
            | ExecutionOrderStatus::Expired
            | ExecutionOrderStatus::Failed => Some(RiskReservationSagaStatus::ReleasePending),
            ExecutionOrderStatus::Filled => Some(RiskReservationSagaStatus::ConsumePending),
            _ => None,
        };
        if let Some(status) = risk_effect {
            self.actor.set_risk_reservation_status(
                order_id,
                status,
                persisted_event.occurred_at_unix_nanos.get(),
            );
        }
        self.commit(persisted_event)?;
        match risk_effect {
            Some(RiskReservationSagaStatus::ReleasePending) => {
                self.complete_risk_release(order_id, order.updated_at_unix_nanos.get())?
            }
            Some(RiskReservationSagaStatus::ConsumePending) => {
                self.complete_risk_consume(order_id, order.updated_at_unix_nanos.get())?
            }
            _ => {}
        }
        if let Some(intent_id) = order
            .intent_id
            .as_deref()
            .filter(|id| self.actor.contains_intent(id))
        {
            self.refresh_intent(intent_id)?;
        }
        info!(event = "order_submitted", component = "execution", order_id = %order.order_id, remote_order_id = ?order.remote_order_id, status = ?order.status, "order submission completed");
        Ok(order)
    }

    pub(super) fn update_commitment_from_order(
        &mut self,
        order: &ExecutionOrder,
        occurred_at: u64,
    ) -> Result<(), ExecutionError> {
        match order.status {
            ExecutionOrderStatus::Unknown => self.actor.set_commitment_status(
                order.order_id.as_str(),
                CommitmentStatus::Uncertain,
                occurred_at,
            ),
            status if status.terminal() => self.actor.set_commitment_status(
                order.order_id.as_str(),
                CommitmentStatus::Released,
                occurred_at,
            ),
            ExecutionOrderStatus::PartiallyFilled => {
                let remaining = order
                    .quantity
                    .checked_sub(order.filled_quantity)
                    .map_err(|error| ExecutionError::Invalid(error.to_string()))?;
                self.actor
                    .resize_commitment(order.order_id.as_str(), remaining, occurred_at)
                    .map_err(ExecutionError::Invalid)?;
            }
            _ => self.actor.set_commitment_status(
                order.order_id.as_str(),
                CommitmentStatus::Active,
                occurred_at,
            ),
        }
        Ok(())
    }

    pub(super) fn complete_risk_release(
        &mut self,
        order_id: &str,
        now: u64,
    ) -> Result<(), ExecutionError> {
        let evidence = self.actor.risk_reservation(order_id).cloned();
        let result = self
            .risk_reservations
            .as_mut()
            .zip(evidence.as_ref())
            .map(|(reservations, evidence)| reservations.release(evidence, now.into()))
            .unwrap_or(Ok(()));
        self.actor.set_risk_reservation_status(
            order_id,
            match &result {
                Ok(()) => RiskReservationSagaStatus::Released,
                Err(error) if error.may_have_been_applied() => RiskReservationSagaStatus::Uncertain,
                Err(_) => RiskReservationSagaStatus::Active,
            },
            now,
        );
        self.persist_snapshot()?;
        result.map_err(|error| {
            if error.may_have_been_applied() {
                ExecutionError::Indeterminate(error.to_string())
            } else {
                ExecutionError::Invalid(error.to_string())
            }
        })
    }

    pub(super) fn complete_risk_consume(
        &mut self,
        order_id: &str,
        now: u64,
    ) -> Result<(), ExecutionError> {
        let evidence = self.actor.risk_reservation(order_id).cloned();
        let result = self
            .risk_reservations
            .as_mut()
            .zip(evidence.as_ref())
            .map(|(reservations, evidence)| reservations.consume(evidence, now.into()))
            .unwrap_or(Ok(()));
        self.actor.set_risk_reservation_status(
            order_id,
            match &result {
                Ok(()) => RiskReservationSagaStatus::Consumed,
                Err(error) if error.may_have_been_applied() => RiskReservationSagaStatus::Uncertain,
                Err(_) => RiskReservationSagaStatus::Active,
            },
            now,
        );
        self.persist_snapshot()?;
        result.map_err(|error| {
            if error.may_have_been_applied() {
                ExecutionError::Indeterminate(error.to_string())
            } else {
                ExecutionError::Invalid(error.to_string())
            }
        })
    }

    pub(super) fn complete_risk_resize(
        &mut self,
        order_id: &str,
        _remaining: Quantity,
        now: u64,
    ) -> Result<(), ExecutionError> {
        let evidence = self.actor.risk_reservation(order_id).cloned();
        let amount = self.actor.commitment(order_id).map(|value| value.amount);
        let result = self
            .risk_reservations
            .as_mut()
            .zip(evidence.as_ref())
            .zip(amount)
            .map(|((reservations, evidence), amount)| {
                reservations.resize(evidence, amount, now.into())
            })
            .unwrap_or(Ok(()));
        self.actor.set_risk_reservation_status(
            order_id,
            match &result {
                Ok(()) => RiskReservationSagaStatus::Active,
                Err(error) if error.may_have_been_applied() => RiskReservationSagaStatus::Uncertain,
                Err(_) => RiskReservationSagaStatus::Active,
            },
            now,
        );
        if result.is_ok() {
            if let Some(amount) = self.actor.commitment(order_id).map(|value| value.amount) {
                self.actor
                    .set_risk_reservation_amount(order_id, amount, now);
            }
        }
        self.persist_snapshot()?;
        result.map_err(|error| {
            if error.may_have_been_applied() {
                ExecutionError::Indeterminate(error.to_string())
            } else {
                ExecutionError::Invalid(error.to_string())
            }
        })
    }

    pub(super) fn mark_not_sent(
        &mut self,
        order_id: &str,
        reason: impl Into<String>,
    ) -> Result<(), ExecutionError> {
        let reason = reason.into();
        let now = now_nanos();
        let Some((order, event)) = self.actor.mark_delivery_status(
            order_id,
            ExecutionOrderStatus::Failed,
            reason.clone(),
            now,
        ) else {
            return Ok(());
        };
        self.actor
            .set_commitment_status(order_id, CommitmentStatus::Released, now);
        self.actor.set_risk_reservation_status(
            order_id,
            RiskReservationSagaStatus::ReleasePending,
            now,
        );
        self.commit(event)?;
        self.complete_risk_release(order_id, now)?;
        if let Some(intent_id) = order.intent_id.as_deref() {
            self.refresh_intent(intent_id)?;
        }
        Ok(())
    }

    pub fn submit(&mut self, request: SubmitOrder) -> Result<ExecutionOrder, ExecutionError> {
        let (order, connection_request) = self.prepare_submission(request)?;
        if self.order_entry.is_none() {
            self.mark_not_sent(&order.order_id, "order entry connection is not configured")?;
            return Err(ExecutionError::Gateway(
                "order entry connection is not configured".into(),
            ));
        }
        self.actor
            .mark_attempt_dispatched(order.order_id.as_str(), now_nanos())
            .ok_or_else(|| ExecutionError::Invalid("execution attempt is missing".into()))?;
        // Persist indeterminate delivery before the provider command can
        // possibly leave the process. A crash after this point reconciles the
        // attempt instead of retrying it transparently.
        self.persist_snapshot()?;
        let connection = self
            .order_entry
            .as_mut()
            .expect("order-entry presence checked above");
        let event = match connection.submit_order(&connection_request) {
            Ok(CommandOutcome::Confirmed(event)) => event,
            Ok(CommandOutcome::Rejected(rejection)) => OrderEntryEvent {
                order_id: order.order_id.clone(),
                status: OrderEntryStatus::Rejected,
                remote_order_id: None,
                filled_quantity: None,
                occurred_at_unix_nanos: now_nanos().into(),
                reason: rejection.message,
            },
            Ok(CommandOutcome::Indeterminate(command)) => {
                warn!(event = "order_submission_indeterminate", component = "execution", order_id = %order.order_id, error = %command.message, "provider order submission requires reconciliation");
                self.mark_unknown_after_gateway_error(&order.order_id, command.message.clone())?;
                return Err(ExecutionError::Indeterminate(command.message));
            }
            Err(error) => {
                warn!(event = "order_submission_failed", component = "execution", order_id = %order.order_id, error = %error, "provider order submission failed");
                let message = error.to_string();
                // The Integration command contract reserves an ordinary Err
                // for failures proven to occur before delivery. Any failure
                // after command dispatch must be returned as Indeterminate.
                self.mark_not_sent(&order.order_id, message.clone())?;
                return Err(ExecutionError::Gateway(message));
            }
        };
        self.apply_order_entry_event(&order.order_id, event)
    }

    pub(super) fn mark_unknown_after_gateway_error(
        &mut self,
        order_id: &str,
        reason: String,
    ) -> Result<(), ExecutionError> {
        let now = now_nanos();
        let Some((order, event)) =
            self.actor
                .mark_delivery_status(order_id, ExecutionOrderStatus::Unknown, reason, now)
        else {
            return Ok(());
        };
        self.actor
            .set_commitment_status(order_id, CommitmentStatus::Uncertain, now);
        self.commit(event)?;
        if let Some(intent_id) = order.intent_id.as_deref() {
            self.refresh_intent(intent_id)?;
        }
        Ok(())
    }

    pub fn cancel(&mut self, request: CancelOrder) -> Result<ExecutionOrder, ExecutionError> {
        info!(event = "order_cancel_started", component = "execution", order_id = %request.order_id, reason = %request.reason, "order cancellation started");
        let order = self
            .actor
            .order_map()
            .get(request.order_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unknown order".into()))?;
        if order.status.terminal() {
            return Err(ExecutionError::Invalid("order is terminal".into()));
        }
        let connection_request = to_connection_request(
            &order,
            &order.segment_key,
            &ExecutionOrderOptions::default(),
            &self.execution_routes,
        )
        .map_err(ExecutionError::Invalid)?;
        let outcome = self
            .order_entry
            .as_mut()
            .ok_or_else(|| {
                ExecutionError::Gateway("order entry connection is not configured".into())
            })?
            .cancel_order(
                &connection_request,
                order.remote_order_id.as_deref().unwrap_or_default(),
                now_nanos(),
            );
        let event = match outcome {
            Ok(CommandOutcome::Confirmed(event)) => event,
            Ok(CommandOutcome::Rejected(rejection)) => {
                warn!(event = "order_cancel_rejected", component = "execution", order_id = %order.order_id, error = %rejection.message, "provider rejected order cancellation");
                return Err(ExecutionError::ProviderRejected(rejection.message));
            }
            Ok(CommandOutcome::Indeterminate(command)) => {
                warn!(event = "order_cancel_indeterminate", component = "execution", order_id = %order.order_id, error = %command.message, "provider order cancellation requires reconciliation");
                self.mark_unknown_after_gateway_error(&order.order_id, command.message.clone())?;
                return Err(ExecutionError::Indeterminate(command.message));
            }
            Err(error) => {
                warn!(event = "order_cancel_failed", component = "execution", order_id = %order.order_id, error = %error, "provider order cancellation failed");
                // No cancel command reached the provider. The original order
                // remains in its current state and does not require recovery
                // solely because a local/pre-delivery cancel attempt failed.
                return Err(ExecutionError::Gateway(error.to_string()));
            }
        };
        let now = now_nanos();
        let (provider_order, _) = self
            .actor
            .apply_order_entry_event(order.order_id.as_str(), event)
            .map_err(ExecutionError::Invalid)?;
        let (next, persisted_event) = self
            .actor
            .mark_delivery_status(
                provider_order.order_id.as_str(),
                provider_order.status,
                request.reason,
                now,
            )
            .expect("provider outcome retains the local order");
        self.update_commitment_from_order(&next, now)?;
        let releases_risk = matches!(
            next.status,
            ExecutionOrderStatus::Canceled
                | ExecutionOrderStatus::Rejected
                | ExecutionOrderStatus::Expired
                | ExecutionOrderStatus::Failed
        );
        if releases_risk {
            self.actor.set_risk_reservation_status(
                next.order_id.as_str(),
                RiskReservationSagaStatus::ReleasePending,
                now,
            );
        }
        self.commit(persisted_event)?;
        if releases_risk {
            self.complete_risk_release(next.order_id.as_str(), now)?;
        }
        if let Some(intent_id) = next.intent_id.as_deref() {
            self.refresh_intent(intent_id)?;
        }
        info!(event = "order_cancelled", component = "execution", order_id = %next.order_id, status = ?next.status, "order cancellation completed");
        Ok(next)
    }

    pub fn replace(&mut self, request: ReplaceOrder) -> Result<ExecutionOrder, ExecutionError> {
        info!(event = "order_replace_started", component = "execution", order_id = %request.order_id, replacement_order_id = %request.replacement.order_id, "order replacement started");
        let current = self
            .actor
            .order_map()
            .get(request.order_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unknown order".into()))?;
        if !current.status.terminal() {
            self.cancel(CancelOrder {
                order_id: request.order_id,
                reason: "replaced".into(),
            })?;
        }
        let result = self.submit(request.replacement);
        match &result {
            Ok(order) => {
                info!(event = "order_replaced", component = "execution", order_id = %order.order_id, status = ?order.status, "order replacement completed")
            }
            Err(error) => {
                warn!(event = "order_replace_failed", component = "execution", error = %error, "order replacement failed")
            }
        }
        result
    }

    /// Refresh both sides of a persistent maker quote.  A refresh is a
    /// lifecycle operation, not a second order owner: old orders remain in
    /// the plan as canceled/fill history and the replacement orders are
    /// attached to the same intent and leg.
    pub fn refresh_quote_intent(
        &mut self,
        request: RefreshQuoteIntent,
    ) -> Result<IntentState, ExecutionError> {
        let state = self
            .actor
            .intent(request.intent_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unknown intent".into()))?;
        if state.intent.intent_type != IntentType::QuoteProvisioning {
            return Err(ExecutionError::Invalid(
                "quote refresh requires a QuoteProvisioning intent".into(),
            ));
        }
        if request.bid_price.mantissa() <= 0 || request.ask_price.mantissa() <= 0 {
            return Err(ExecutionError::Invalid(
                "quote refresh prices must be positive".into(),
            ));
        }
        if request.bid_price >= request.ask_price {
            return Err(ExecutionError::Invalid(
                "quote refresh requires bid below ask".into(),
            ));
        }
        let now = now_nanos();
        if request.quote_observed_at.get() > now {
            return Err(ExecutionError::Invalid(
                "quote observation cannot be in the future".into(),
            ));
        }
        let max_age = state
            .intent
            .legs
            .iter()
            .filter_map(|leg| leg.options.maker.as_ref())
            .chain(state.intent.order_options.maker.as_ref())
            .filter_map(|policy| policy.max_quote_age)
            .min();
        if let Some(max_age) = max_age {
            let age = now.saturating_sub(request.quote_observed_at.get());
            if age > max_age.get() {
                return Err(ExecutionError::Invalid(format!(
                    "quote is stale: age={}ms exceeds {}ms",
                    age / 1_000_000,
                    max_age.get() / 1_000_000
                )));
            }
        }
        if let Some(last) = state.last_quote_refresh_unix_nanos {
            let min_interval = state
                .intent
                .legs
                .iter()
                .filter_map(|leg| leg.options.maker.as_ref())
                .chain(state.intent.order_options.maker.as_ref())
                .filter_map(|policy| policy.min_interval)
                .max()
                .unwrap_or(DurationNanos::new(0));
            if now.saturating_sub(last.get()) < min_interval.get() {
                return Err(ExecutionError::Invalid(
                    "quote refresh violates maker minimum interval".into(),
                ));
            }
        }
        let plan = state
            .plan
            .clone()
            .ok_or_else(|| ExecutionError::Invalid("quote intent has no execution plan".into()))?;
        let version = state.quote_version.saturating_add(1);
        let mut templates = Vec::new();
        for leg in &plan.legs {
            let template = leg
                .order_ids
                .iter()
                .rev()
                .filter_map(|order_id| self.actor.order_map().get(order_id.as_str()))
                .find(|order| !order.status.terminal())
                .or_else(|| {
                    leg.order_ids
                        .iter()
                        .rev()
                        .filter_map(|order_id| self.actor.order_map().get(order_id.as_str()))
                        .next()
                })
                .cloned()
                .ok_or_else(|| {
                    ExecutionError::Invalid(format!(
                        "quote leg has no order template: {}",
                        leg.leg_id
                    ))
                })?;
            templates.push((leg.leg_id.clone(), template));
        }
        for (_, template) in &templates {
            if !template.status.terminal() {
                self.cancel(CancelOrder {
                    order_id: template.order_id.clone(),
                    reason: "maker quote refresh".into(),
                })?;
            }
        }
        let mut new_order_ids: Vec<OrderId> = Vec::new();
        for (leg_id, template) in templates {
            let options = template_options(&state.intent, &leg_id);
            let mut replacement = SubmitOrder {
                order_id: OrderId::new(format!(
                    "{}:quote:{}:{}",
                    request.intent_id, version, leg_id
                ))
                .expect("validated quote order ID"),
                intent_id: Some(request.intent_id.clone()),
                strategy_id: Some(typed_strategy_id(state.intent.strategy_id.clone())),
                account_id: template.account_id.clone(),
                segment_key: template.segment_key.clone(),
                instrument_id: template.instrument_id.clone(),
                market_id: template.market_id.clone(),
                execution_route_id: template.execution_route_id.clone(),
                side: template.side,
                order_type: OrderType::Limit,
                quantity: Quantity::new(
                    plan.legs
                        .iter()
                        .find(|leg| leg.leg_id == leg_id)
                        .map(|leg| leg.target_quantity.mantissa())
                        .unwrap_or(template.quantity.mantissa()),
                    template.quantity.scale(),
                )
                .expect("validated quote quantity"),
                limit_price: Some(
                    Price::new(
                        if template.side == OrderSide::Buy {
                            request.bid_price.mantissa()
                        } else {
                            request.ask_price.mantissa()
                        },
                        if template.side == OrderSide::Buy {
                            request.bid_price.scale()
                        } else {
                            request.ask_price.scale()
                        },
                    )
                    .expect("validated quote price"),
                ),
                options,
                submitted_at_unix_nanos: Some(request.quote_observed_at),
            };
            replacement.options.post_only = Some(true);
            let order = match self.submit(replacement) {
                Ok(order) => order,
                Err(error) => {
                    let current = self
                        .actor
                        .intent(request.intent_id.as_str())
                        .cloned()
                        .ok_or_else(|| {
                            ExecutionError::Invalid("quote intent disappeared".into())
                        })?;
                    self.commit_intent(IntentEvent {
                        intent_id: request.intent_id.clone(),
                        event_sequence: 0.into(),
                        status: IntentStatus::ReconciliationRequired,
                        order_ids: Vec::new(),
                        completed_quantity: current.completed_quantity,
                        occurred_at_unix_nanos: now.into(),
                        reason: format!("maker quote refresh failed: {error}"),
                        dependency_watermarks: current.dependency_watermarks,
                    })?;
                    return Err(error);
                }
            };
            self.attach_plan_order(request.intent_id.as_str(), &leg_id, &order.order_id)?;
            new_order_ids.push(order.order_id);
        }
        self.actor
            .update_quote_refresh(request.intent_id.as_str(), version, now);
        let current = self
            .actor
            .intent(request.intent_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("quote intent disappeared".into()))?;
        self.commit_intent(IntentEvent {
            intent_id: request.intent_id,
            event_sequence: 0.into(),
            status: IntentStatus::Executing,
            order_ids: new_order_ids,
            completed_quantity: current.completed_quantity,
            occurred_at_unix_nanos: now.into(),
            reason: if request.reason.trim().is_empty() {
                "maker quote refreshed".into()
            } else {
                request.reason
            },
            dependency_watermarks: current.dependency_watermarks.clone(),
        })?;
        self.persist_snapshot()?;
        self.actor
            .intent(current.intent.intent_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("quote intent disappeared".into()))
    }

    /// Pull current projected quotes through the intent-planning port and
    /// automatically refresh changed QuoteProvisioning intents.  The market
    /// projection is advisory; every replacement still goes through the
    /// normal order validation, reservation and lifecycle path.
    pub fn refresh_maker_quotes(&mut self) -> Result<usize, ExecutionError> {
        let targets = self
            .actor
            .intents()
            .filter(|state| {
                state.intent.intent_type == IntentType::QuoteProvisioning
                    && !matches!(
                        state.status,
                        IntentStatus::Satisfied
                            | IntentStatus::Rejected
                            | IntentStatus::Canceled
                            | IntentStatus::Expired
                            | IntentStatus::Failed
                            | IntentStatus::ReconciliationRequired
                    )
            })
            .map(|state| {
                (
                    state.intent.intent_id.clone(),
                    state.intent.instrument_id.clone(),
                    state.intent.market_id.clone(),
                )
            })
            .collect::<Vec<_>>();
        if targets.is_empty() || self.intent_planner.is_none() {
            return Ok(0);
        }
        let observations = {
            let planner = self
                .intent_planner
                .as_mut()
                .expect("intent planner checked above");
            targets
                .iter()
                .map(|(intent_id, instrument_id, market_id)| {
                    planner
                        .latest_quote(instrument_id, market_id.as_deref())
                        .map(|quote| (intent_id.clone(), quote))
                })
                .collect::<Result<Vec<_>, _>>()
                .map_err(ExecutionError::Invalid)?
        };
        let mut refreshed = 0;
        for (intent_id, quote) in observations {
            let Some(quote) = quote else {
                continue;
            };
            let (Some(bid), Some(ask)) = (quote.bid_price, quote.ask_price) else {
                continue;
            };
            let changed = self
                .actor
                .intent(intent_id.as_str())
                .and_then(|state| state.plan.as_ref())
                .map(|plan| {
                    let current_price = |side: OrderSide| {
                        plan.legs
                            .iter()
                            .filter(|leg| leg.side == side)
                            .flat_map(|leg| leg.order_ids.iter().rev())
                            .filter_map(|id| self.actor.order_map().get(id))
                            .find(|order| !order.status.terminal())
                            .and_then(|order| order.limit_price)
                    };
                    current_price(OrderSide::Buy) != Some(bid)
                        || current_price(OrderSide::Sell) != Some(ask)
                })
                .unwrap_or(false);
            if !changed {
                continue;
            }
            match self.refresh_quote_intent(RefreshQuoteIntent {
                intent_id,
                bid_price: bid,
                ask_price: ask,
                quote_observed_at: quote.observed_at_unix_nanos,
                reason: "projected market quote changed".into(),
            }) {
                Ok(_) => refreshed += 1,
                Err(error) => warn!(
                    event = "maker_quote_refresh_skipped",
                    component = "execution",
                    error = %error,
                    "maker quote refresh was rejected by execution guardrails"
                ),
            }
        }
        Ok(refreshed)
    }

    pub(super) fn commit(&mut self, event: ExecutionEvent) -> Result<(), ExecutionError> {
        debug!(event = "execution_event_committing", component = "execution", order_id = %event.order_id, status = ?event.status, "execution event committing");
        let event = self.actor.commit_event(event);
        let snapshot = self.snapshot();
        if let Some(store) = self.store.as_mut() {
            store
                .commit_event(&event, &snapshot)
                .map_err(ExecutionError::Persistence)?;
        }
        let mut changes = Vec::new();
        if let Some(order) = self.actor.order_map().get(&event.order_id).cloned() {
            let strategy_id = order
                .strategy_id
                .as_ref()
                .map(ToString::to_string)
                .or_else(|| {
                    order.intent_id.as_ref().and_then(|intent_id| {
                        self.actor
                            .intent(intent_id.as_str())
                            .map(|state| state.intent.strategy_id.clone())
                    })
                });
            if let Some(strategy_id) = strategy_id {
                changes.push(ExecutionBusinessChange::Order {
                    strategy_id: strategy_id.clone(),
                    order: order.clone(),
                });
                if let Some(fill_id) = event.fill_id.as_ref() {
                    if let Some(fill) = self
                        .actor
                        .fills()
                        .iter()
                        .find(|fill| &fill.fill_id == fill_id)
                    {
                        changes.push(ExecutionBusinessChange::Fill {
                            strategy_id,
                            account_id: order.account_id.to_string(),
                            intent_id: order.intent_id.as_ref().map(ToString::to_string),
                            market_id: order.market_id.as_ref().map(ToString::to_string),
                            remote_order_id: order
                                .remote_order_id
                                .as_ref()
                                .map(ToString::to_string),
                            side: order.side,
                            fill: fill.clone(),
                        });
                    }
                }
            }
        }
        self.pending_business_events
            .push_back(ExecutionBusinessEvent {
                sequence: self.actor.event_sequence().into(),
                occurred_at_unix_nanos: event.occurred_at_unix_nanos,
                changes,
            });
        Ok(())
    }

    pub(super) fn record_unknown_remote_order(
        &mut self,
        event: &RemoteOrderUpdate,
    ) -> Result<(), ExecutionError> {
        self.actor
            .record_unknown_remote_order(event)
            .map_err(ExecutionError::Invalid)?;
        self.persist_snapshot()
    }
}

fn validate_execution_route(
    request: &SubmitOrder,
    route: &ExecutionRouteCandidate,
) -> Result<(), String> {
    if route
        .account_id
        .as_ref()
        .is_some_and(|account_id| &request.account_id != account_id)
    {
        return Err(format!(
            "execution route {} is configured for account {}, not {}",
            route.route_id,
            route.account_id.as_ref().expect("constraint checked"),
            request.account_id
        ));
    }
    if route
        .segment_key
        .as_ref()
        .is_some_and(|segment_key| &request.segment_key != segment_key)
    {
        return Err(format!(
            "execution route {} is configured for segment {}, not {}",
            route.route_id,
            route.segment_key.as_ref().expect("constraint checked"),
            request.segment_key
        ));
    }
    if route
        .instrument_id
        .as_ref()
        .is_some_and(|instrument_id| &request.instrument_id != instrument_id)
    {
        return Err(format!(
            "execution route {} is configured for instrument {}, not {}",
            route.route_id,
            route.instrument_id.as_ref().expect("constraint checked"),
            request.instrument_id
        ));
    }
    if let (Some(request_market), Some(route_market)) =
        (request.market_id.as_ref(), route.market_id.as_ref())
    {
        if route_market != request_market {
            return Err(format!(
                "execution route {} does not target market {}",
                route.route_id, request_market
            ));
        }
    }
    if !route.supported_order_types.contains(&request.order_type) {
        return Err(format!(
            "execution route {} does not support {:?} orders",
            route.route_id, request.order_type
        ));
    }
    for option in used_order_options(&request.options) {
        if !route.supported_options.iter().any(|value| value == option) {
            return Err(format!(
                "execution route {} does not support order option {}",
                route.route_id, option
            ));
        }
    }
    if !route.ready {
        return Err(format!("execution route {} is not ready", route.route_id));
    }
    Ok(())
}

fn used_order_options(options: &ExecutionOrderOptions) -> Vec<&'static str> {
    let mut used = Vec::new();
    if options.time_in_force.is_some() {
        used.push("time_in_force");
    }
    if options.reduce_only.is_some() {
        used.push("reduce_only");
    }
    if options.post_only.is_some() {
        used.push("post_only");
    }
    if options.position_side.is_some() {
        used.push("position_side");
    }
    if options.quote_asset.is_some() {
        used.push("quote_asset");
    }
    if options.wallet_type.is_some() {
        used.push("wallet_type");
    }
    if options.trading_session.is_some() {
        used.push("trading_session");
    }
    if options.tokenize.is_some() {
        used.push("tokenize");
    }
    used
}
