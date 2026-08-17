//! Typed control and external-event ingress for the process facade.

use super::*;

mod events;

pub(in crate::application::process) use events::remote_order_event_from_envelope;

impl<E, Q, S> ExecutionProcess<E, Q, S> {
    pub(in crate::application::process) fn handle_http_request(
        &mut self,
        request: ControlRequest,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let operation_started = std::time::Instant::now();
        let is_command = !matches!(
            request.operation,
            ControlOperation::Health | ControlOperation::AvailableRoutes(_)
        );
        let response = self.handle_operation(request.operation);
        let _ = request
            .response
            .send(response.map_err(|error| error.to_string()));
        if is_command {
            self.flush_events()?;
            self.publish_snapshots()?;
        }
        self.metrics.last_operation_micros.store(
            operation_started.elapsed().as_micros() as u64,
            Ordering::Relaxed,
        );
        Ok(())
    }

    fn handle_operation(
        &mut self,
        operation: ControlOperation,
    ) -> Result<ControlResponse, Box<dyn std::error::Error>> {
        let response = match operation {
            ControlOperation::Health => {
                let (route_status, routes) = process_readiness(&self.route_readiness);
                let writer_recovery_ready = self.application.writer_recovery_ready();
                let status = if route_status == "ready" && writer_recovery_ready {
                    "ready"
                } else {
                    "degraded"
                };
                ControlResponse::health(status, writer_recovery_ready, &routes)?
            }
            ControlOperation::AvailableRoutes(query) => {
                ControlResponse::routes(&self.application.available_execution_routes(&query))?
            }
            ControlOperation::AdvanceTime(event_time) => {
                if let Some(current) = self
                    .simulator
                    .as_ref()
                    .and_then(ExecutionSimulator::business_time)
                {
                    if event_time < current.get() {
                        return Err("execution business time cannot move backwards".into());
                    }
                }
                self.application.advance_time(event_time)?;
                if let Some(simulator) = self.simulator.as_mut() {
                    simulator.set_business_time(event_time.into());
                }
                ControlResponse::event_time(event_time)
            }
            ControlOperation::SubmitIntent {
                intent,
                idempotency_key,
            } => match self
                .application
                .submit_intent_with_idempotency(intent, idempotency_key)
            {
                Ok((intent, duplicate)) => {
                    for order_id in &intent.order_ids {
                        if let Some(order) = self
                            .application
                            .orders(None)
                            .into_iter()
                            .find(|order| order.order_id.as_str() == order_id.as_str())
                        {
                            self.register_simulation_order(&order)?;
                        }
                    }
                    ControlResponse::intent_accepted(intent.intent.intent_id.as_str(), duplicate)
                }
                Err(error) => ControlResponse::intent_error("execution.intent_invalid", error),
            },
            ControlOperation::SubmitOrder(request) => match self.application.submit(request) {
                Ok(order) => {
                    self.register_simulation_order(&order)?;
                    ControlResponse::accepted_order(order.order_id.as_str())
                }
                Err(error) => ControlResponse::error(422, error),
            },
            ControlOperation::CancelOrder(request) => match self.application.cancel(request) {
                Ok(order) => ControlResponse::accepted_order(order.order_id.as_str()),
                Err(error) => ControlResponse::error(422, error),
            },
            ControlOperation::ReplaceOrder { order_id, patch } => {
                let original = self
                    .application
                    .orders(None)
                    .into_iter()
                    .find(|order| order.order_id == order_id);
                match original {
                    Some(original) => {
                        let replacement = SubmitOrder {
                            order_id: kairos_primitives::OrderId::new(format!(
                                "{}:replacement",
                                order_id
                            ))?,
                            intent_id: original.intent_id,
                            strategy_id: original.strategy_id,
                            account_id: original.account_id,
                            segment_key: original.segment_key,
                            instrument_id: original.instrument_id,
                            market_id: original.market_id,
                            execution_route_id: original.execution_route_id,
                            side: original.side,
                            order_type: original.order_type,
                            quantity: patch.quantity.unwrap_or(original.quantity),
                            limit_price: patch.limit_price.unwrap_or(original.limit_price),
                            options: patch.options,
                            submitted_at_unix_nanos: None,
                        };
                        match self.application.replace(crate::application::ReplaceOrder {
                            order_id,
                            replacement,
                        }) {
                            Ok(order) => ControlResponse::accepted_order(order.order_id.as_str()),
                            Err(error) => ControlResponse::error(422, error),
                        }
                    }
                    None => ControlResponse::error(422, "order not found"),
                }
            }
            ControlOperation::Reconcile(query) => {
                match self.application.reconcile_remote_orders(query) {
                    Ok(changed) => ControlResponse::accepted_reconciliation(changed),
                    Err(error) => ControlResponse::error(422, error),
                }
            }
            ControlOperation::LinkUnknownRemote {
                remote_order_id,
                local_order_id,
            } => match self
                .application
                .link_unknown_remote_order(&remote_order_id, &local_order_id)
            {
                Ok(order) => ControlResponse::serialized(200, &order)?,
                Err(error) => ControlResponse::error(422, error),
            },
            ControlOperation::EvaluateBacktest(request) => {
                match BacktestApplication::evaluate(request) {
                    Ok(metrics) => ControlResponse::serialized(200, &metrics)?,
                    Err(error) => ControlResponse::error(422, error),
                }
            }
            ControlOperation::RunBacktest(request) => match BacktestApplication::run(request) {
                Ok(result) => ControlResponse::serialized(200, &result)?,
                Err(error) => ControlResponse::error(422, error),
            },
            ControlOperation::ApplyBacktestMarket(event) => {
                match self.apply_simulated_market(event) {
                    Ok(fills) => ControlResponse::fills(&fills)?,
                    Err(error) => ControlResponse::error(422, error),
                }
            }
            ControlOperation::CancelIntent(request) => {
                match self.application.cancel_intent(request) {
                    Ok(intent) => ControlResponse::intent_result("cancel_requested", &intent)?,
                    Err(error) => {
                        ControlResponse::intent_error("execution.intent_cancel_invalid", error)
                    }
                }
            }
            ControlOperation::ExpireIntent(request) => {
                match self.application.expire_intent(request) {
                    Ok(intent) => ControlResponse::intent_result("expired", &intent)?,
                    Err(error) => {
                        ControlResponse::intent_error("execution.intent_expire_invalid", error)
                    }
                }
            }
            ControlOperation::RefreshQuote(request) => {
                match self.application.refresh_quote_intent(request) {
                    Ok(intent) => ControlResponse::intent_result("quote_refreshed", &intent)?,
                    Err(error) => {
                        ControlResponse::intent_error("execution.quote_refresh_invalid", error)
                    }
                }
            }
            ControlOperation::PreviewSubmit(request) => {
                match self.application.preview_submit(&request) {
                    Ok(order) => ControlResponse::serialized(200, &order)?,
                    Err(error) => ControlResponse::error(422, error),
                }
            }
            ControlOperation::RecordFill(request) => match self.application.record_fill(request) {
                Ok(order) => ControlResponse::serialized(202, &order)?,
                Err(error) => ControlResponse::error(422, error),
            },
            ControlOperation::Stop => {
                self.stopping = true;
                ControlResponse::stopping()
            }
        };
        Ok(response)
    }
}
