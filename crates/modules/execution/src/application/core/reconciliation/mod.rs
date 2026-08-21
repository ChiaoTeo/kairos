//! Remote order reconciliation, exchange-event application, and unknown-order recovery.

use super::*;

impl ExecutionApplication {
    pub fn remote_open_orders(
        &mut self,
        query: RemoteOrderQuery,
    ) -> Result<Vec<RemoteOrder>, ExecutionError> {
        info!(event = "remote_open_orders_query_started", component = "execution", order_id = ?query.order_id, symbol = ?query.symbol, "remote open-orders query started");
        self.order_query
            .as_mut()
            .ok_or_else(|| ExecutionError::Gateway("remote order query is not configured".into()))?
            .open_orders(&ExternalOrderQuery {
                instrument_type: None,
                symbol: query.symbol.clone(),
                order_id: query.order_id.clone(),
                limit: query.limit,
                since_unix_nanos: query.since_unix_nanos,
            })
            .map(|orders| {
                let count = orders.len();
                info!(event = "remote_open_orders_query_completed", component = "execution", count, "remote open-orders query completed");
                orders.into_iter().map(remote_order).collect()
            })
            .map_err(|error| { warn!(event = "remote_open_orders_query_failed", component = "execution", error = %error, "remote open-orders query failed"); ExecutionError::Gateway(error.to_string()) })
    }

    pub fn remote_history(
        &mut self,
        query: RemoteOrderQuery,
    ) -> Result<Vec<RemoteOrder>, ExecutionError> {
        self.order_query
            .as_mut()
            .ok_or_else(|| ExecutionError::Gateway("remote order query is not configured".into()))?
            .order_history(&ExternalOrderQuery {
                instrument_type: None,
                symbol: query.symbol.clone(),
                order_id: query.order_id.clone(),
                limit: query.limit,
                since_unix_nanos: query.since_unix_nanos,
            })
            .map(|orders| orders.into_iter().map(remote_order).collect())
            .map_err(|error| ExecutionError::Gateway(error.to_string()))
    }

    pub fn remote_detail(
        &mut self,
        query: RemoteOrderQuery,
    ) -> Result<Option<RemoteOrder>, ExecutionError> {
        self.order_query
            .as_mut()
            .ok_or_else(|| ExecutionError::Gateway("remote order query is not configured".into()))?
            .order_detail(&ExternalOrderQuery {
                instrument_type: None,
                symbol: query.symbol.clone(),
                order_id: query.order_id.clone(),
                limit: query.limit,
                since_unix_nanos: query.since_unix_nanos,
            })
            .map(|order| order.map(remote_order))
            .map_err(|error| ExecutionError::Gateway(error.to_string()))
    }

    /// Reconcile locally active/unknown orders with the exchange query surface.
    /// Private streams are not guaranteed to deliver events during a
    /// disconnect, so recovery must compare the durable local journal with
    /// the exchange's open-order and history views.
    pub fn reconcile_remote_orders(
        &mut self,
        mut query: RemoteOrderQuery,
    ) -> Result<usize, ExecutionError> {
        if query.since_unix_nanos.is_none() && self.actor.remote_watermark() > 0 {
            query.since_unix_nanos = Some(
                self.actor
                    .remote_watermark()
                    .saturating_sub(30_000_000_000)
                    .into(),
            );
        }
        let mut remote = self.remote_open_orders(query.clone())?;
        remote.extend(self.remote_history(query)?);
        self.reconcile_remote_order_facts(remote)
    }

    pub(crate) fn reconcile_external_orders(
        &mut self,
        remote: Vec<kairos_conflux::ExternalOrder>,
    ) -> Result<usize, ExecutionError> {
        self.reconcile_remote_order_facts(remote.into_iter().map(remote_order).collect())
    }

    fn reconcile_remote_order_facts(
        &mut self,
        mut remote: Vec<RemoteOrder>,
    ) -> Result<usize, ExecutionError> {
        remote.sort_by(|left, right| left.order_id.cmp(&right.order_id));
        remote.dedup_by(|left, right| left.order_id == right.order_id);

        let mut changed = 0;
        for remote_order in remote {
            let local = self
                .actor
                .order_map()
                .values()
                .find(|order| {
                    remote_order.order_id == order.order_id.as_str()
                        || remote_order
                            .client_order_id
                            .as_ref()
                            .is_some_and(|client_id| client_id.as_str() == order.order_id.as_str())
                        || order.remote_order_id.as_deref() == Some(remote_order.order_id.as_str())
                })
                .cloned();
            let Some(local) = local else {
                self.record_unknown_remote_order(&RemoteOrderUpdate {
                    order_id: remote_order.order_id.clone(),
                    symbol: remote_order.symbol,
                    status: remote_order.status,
                    fill_quantity: Some(remote_order.filled_quantity),
                    fill_price: remote_order.average_fill_price,
                    execution_id: None,
                    fee_currency: None,
                    fee_amount: None,
                    occurred_at_unix_nanos: remote_order
                        .occurred_at_unix_nanos
                        .unwrap_or_else(|| now_nanos().into()),
                    reason: "remote query found an order without a local journal entry".into(),
                })?;
                changed += 1;
                continue;
            };

            let mut reconciled_status = remote_order.status;
            let mut reconciliation_reason = "reconciled from remote order query".to_string();

            // The private stream can be disconnected after the exchange has
            // accepted or filled an order.  A remote order query returns a
            // cumulative fill quantity, so recover only the delta that is
            // missing from the local journal.  This keeps recovery idempotent
            // even when the same order appears in both open-orders and
            // history, or when the query window overlaps a previous recovery.
            let remote_filled = Quantity::new(
                remote_order.filled_quantity.mantissa(),
                remote_order.filled_quantity.scale(),
            )
            .map_err(|error| ExecutionError::Invalid(error.to_string()))?;
            match remote_filled {
                remote_filled if remote_filled > local.filled_quantity => {
                    let delta = remote_filled
                        .checked_sub(local.filled_quantity)
                        .map_err(|error| ExecutionError::Invalid(error.to_string()))?;
                    let price = remote_order
                        .average_fill_price
                        .map(|value| (value.mantissa(), value.scale()));
                    let price = price.or_else(|| {
                        local
                            .limit_price
                            .map(|value| (value.mantissa(), value.scale()))
                    });
                    if let Some(price) = price {
                        let fill_result = self.record_fill(ExecutionFillReport {
                            fill_id: FillId::new(format!(
                                "reconcile:{}:{}:{}",
                                remote_order.order_id,
                                remote_filled,
                                remote_filled.scale()
                            ))
                            .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                            order_id: local.order_id.clone(),
                            quantity: delta,
                            price: Price::new(price.0, price.1)
                                .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                            fee: Money::ZERO,
                            fee_currency: None,
                            occurred_at_unix_nanos: remote_order.occurred_at_unix_nanos,
                            execution_market_id: None,
                            reported_provider_id: local
                                .selected_route
                                .as_ref()
                                .map(|route| route.participant_id.clone()),
                            provider_product: local
                                .selected_route
                                .as_ref()
                                .map(|route| route.provider_product.clone()),
                            order_entry_symbol: local
                                .selected_route
                                .as_ref()
                                .map(|route| route.order_entry_symbol.clone()),
                            remote_order_id: Some(
                                RemoteOrderId::new(remote_order.order_id.to_string())
                                    .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                            ),
                        });
                        match fill_result {
                            Ok(_) => changed += 1,
                            Err(error) => {
                                reconciled_status = ExecutionOrderStatus::Unknown;
                                reconciliation_reason =
                                    format!("remote cumulative fill could not be applied: {error}");
                            },
                        }
                    } else {
                        reconciled_status = ExecutionOrderStatus::Unknown;
                        reconciliation_reason =
                            "remote fill has no average price; manual reconciliation required"
                                .into();
                    }
                },
                remote_filled if remote_filled < local.filled_quantity => {
                    reconciled_status = ExecutionOrderStatus::Unknown;
                    reconciliation_reason = format!(
                        "remote cumulative fill {} is behind local fill {}; manual reconciliation required",
                        remote_filled, local.filled_quantity
                    );
                },
                _ => {},
            }

            let local = self
                .actor
                .order_map()
                .get(&local.order_id)
                .cloned()
                .ok_or_else(|| ExecutionError::Invalid("reconciled order disappeared".into()))?;
            if local.status != reconciled_status
                || local.remote_order_id.as_deref() != Some(remote_order.order_id.as_str())
            {
                let occurred_at = remote_order
                    .occurred_at_unix_nanos
                    .unwrap_or_else(|| now_nanos().into())
                    .get();
                let (_, event) = self
                    .actor
                    .reconcile_order(
                        local.order_id.as_str(),
                        &remote_order.order_id,
                        reconciled_status,
                        occurred_at,
                        reconciliation_reason,
                    )
                    .map_err(ExecutionError::Invalid)?;
                let reconciled = self
                    .actor
                    .order(local.order_id.as_str())
                    .cloned()
                    .ok_or_else(|| {
                        ExecutionError::Invalid("reconciled order disappeared".into())
                    })?;
                self.update_commitment_from_order(&reconciled, occurred_at)?;
                let risk_effect = match reconciled.status {
                    ExecutionOrderStatus::Filled => Some(RiskReservationSagaStatus::ConsumePending),
                    status if status.terminal() => Some(RiskReservationSagaStatus::ReleasePending),
                    _ => None,
                };
                if let Some(status) = risk_effect {
                    self.actor.set_risk_reservation_status(
                        reconciled.order_id.as_str(),
                        status,
                        occurred_at,
                    );
                }
                self.commit(event)?;
                match risk_effect {
                    Some(RiskReservationSagaStatus::ConsumePending) => {
                        self.complete_risk_consume(reconciled.order_id.as_str(), occurred_at)?
                    },
                    Some(RiskReservationSagaStatus::ReleasePending) => {
                        self.complete_risk_release(reconciled.order_id.as_str(), occurred_at)?
                    },
                    _ => {},
                }
                changed += 1;
            }
        }
        Ok(changed)
    }

    #[cfg(test)]
    pub(crate) fn install_order_entry(&mut self, connection: Box<dyn BlockingOrderCommand>) {
        self.order_entry = Some(connection);
    }

    pub fn has_order_query(&self) -> bool {
        self.order_query.is_some()
    }

    /// Apply one normalized exchange fact received from the private order stream.
    /// The stream consumer owns subscription and delivery; this method only
    /// mutates Execution-owned lifecycle state.
    pub fn apply_remote_execution_event(
        &mut self,
        event: RemoteOrderUpdate,
    ) -> Result<ExecutionOrder, ExecutionError> {
        self.apply_remote_execution_event_with_compensation(event, true)
    }

    pub(crate) fn apply_remote_execution_event_deferred(
        &mut self,
        event: RemoteOrderUpdate,
    ) -> Result<ExecutionOrder, ExecutionError> {
        self.apply_remote_execution_event_with_compensation(event, false)
    }

    fn apply_remote_execution_event_with_compensation(
        &mut self,
        event: RemoteOrderUpdate,
        compensate: bool,
    ) -> Result<ExecutionOrder, ExecutionError> {
        info!(event = "remote_execution_event_received", component = "execution", remote_order_id = %event.order_id, status = ?event.status, "remote execution event received");
        self.actor
            .observe_remote_time(event.occurred_at_unix_nanos.get());
        let local = self
            .actor
            .find_remote_order(event.order_id.as_str())
            .ok_or_else(|| {
                let _ = self.record_unknown_remote_order(&event);
                ExecutionError::Invalid(format!(
                    "remote execution references unknown order: {}",
                    event.order_id
                ))
            })?;
        if let (Some(quantity), Some(price)) = (&event.fill_quantity, &event.fill_price) {
            let quantity = parse_decimal(&quantity.to_string())?;
            let price = parse_decimal(&price.to_string())?;
            let fee = event
                .fee_amount
                .as_ref()
                .map(|value| parse_decimal(&value.to_string()))
                .transpose()?
                .map(|value| {
                    Money::new(value.0, value.1)
                        .map_err(|error| ExecutionError::Invalid(error.to_string()))
                })
                .transpose()?
                .unwrap_or(Money::ZERO);
            let fill_id = event.execution_id.clone().unwrap_or(
                FillId::new(format!(
                    "remote:{}:{}",
                    event.order_id, event.occurred_at_unix_nanos
                ))
                .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
            );
            let report = ExecutionFillReport {
                fill_id,
                order_id: local.order_id.clone(),
                quantity: Quantity::new(quantity.0, quantity.1)
                    .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                price: Price::new(price.0, price.1)
                    .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                fee,
                fee_currency: event.fee_currency.clone(),
                occurred_at_unix_nanos: Some(event.occurred_at_unix_nanos),
                execution_market_id: None,
                reported_provider_id: local
                    .selected_route
                    .as_ref()
                    .map(|route| route.participant_id.clone()),
                provider_product: local
                    .selected_route
                    .as_ref()
                    .map(|route| route.provider_product.clone()),
                order_entry_symbol: local
                    .selected_route
                    .as_ref()
                    .map(|route| route.order_entry_symbol.clone()),
                remote_order_id: Some(
                    RemoteOrderId::new(event.order_id.to_string())
                        .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                ),
            };
            let fill = if compensate {
                self.record_fill(report)?
            } else {
                self.record_fill_deferred(report)?
            };
            return Ok(fill);
        }
        let occurred_at = event.occurred_at_unix_nanos.get();
        let (next, persisted_event) = self
            .actor
            .reconcile_order(
                local.order_id.as_str(),
                event.order_id.as_str(),
                event.status,
                occurred_at,
                event.reason,
            )
            .map_err(ExecutionError::Invalid)?;
        self.update_commitment_from_order(&next, occurred_at)?;
        let risk_effect = match next.status {
            ExecutionOrderStatus::Filled => Some(RiskReservationSagaStatus::ConsumePending),
            status if status.terminal() => Some(RiskReservationSagaStatus::ReleasePending),
            _ => None,
        };
        if let Some(status) = risk_effect {
            self.actor
                .set_risk_reservation_status(next.order_id.as_str(), status, occurred_at);
        }
        self.commit(persisted_event)?;
        match risk_effect {
            Some(RiskReservationSagaStatus::ConsumePending) => {
                self.complete_risk_consume(next.order_id.as_str(), occurred_at)?
            },
            Some(RiskReservationSagaStatus::ReleasePending) => {
                self.complete_risk_release(next.order_id.as_str(), occurred_at)?
            },
            _ => {},
        }
        info!(event = "remote_execution_event_reconciled", component = "execution", order_id = %next.order_id, status = ?next.status, "remote execution event reconciled");
        Ok(next)
    }

    pub(crate) fn accept_remote_event_identity(&mut self, event_id: &str) -> bool {
        self.actor.accept_exchange_event(event_id)
    }

    pub fn unknown_remote_orders(&self) -> Vec<UnknownRemoteOrder> {
        self.actor.unknown_remote_orders().cloned().collect()
    }

    /// Mark a previously unknown remote order as resolved once an operator or
    /// reconciliation process has established its local order association.
    pub fn resolve_unknown_remote_order(
        &mut self,
        remote_order_id: &str,
        resolution: UnknownRemoteOrderResolution,
        reason: impl Into<String>,
    ) -> Result<(), ExecutionError> {
        self.actor
            .resolve_unknown_remote_order(remote_order_id, resolution, reason.into(), now_nanos())
            .map_err(ExecutionError::Invalid)?;
        self.persist_snapshot()
    }

    /// Link an unknown exchange order to a locally submitted order after
    /// reconciliation has established the association. If the remote fact
    /// includes a fill, apply it through the normal idempotent fill path.
    pub fn link_unknown_remote_order(
        &mut self,
        remote_order_id: &str,
        local_order_id: &str,
    ) -> Result<ExecutionOrder, ExecutionError> {
        let (unknown, local, event) = self
            .actor
            .link_unknown_remote_order(remote_order_id, local_order_id)
            .map_err(ExecutionError::Invalid)?;
        self.commit(event)?;
        if let (Some(quantity), Some(price)) = (unknown.fill_quantity, unknown.fill_price) {
            return self.record_fill(ExecutionFillReport {
                fill_id: unknown.execution_id.unwrap_or(
                    FillId::new(format!("remote:{remote_order_id}:{local_order_id}"))
                        .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                ),
                order_id: OrderId::new(local_order_id.to_owned())
                    .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                quantity,
                price,
                fee: unknown.fee_amount.unwrap_or(Money::ZERO),
                fee_currency: unknown.fee_currency,
                occurred_at_unix_nanos: Some(unknown.last_seen_at_unix_nanos),
                execution_market_id: None,
                reported_provider_id: local
                    .selected_route
                    .as_ref()
                    .map(|route| route.participant_id.clone()),
                provider_product: local
                    .selected_route
                    .as_ref()
                    .map(|route| route.provider_product.clone()),
                order_entry_symbol: local
                    .selected_route
                    .as_ref()
                    .map(|route| route.order_entry_symbol.clone()),
                remote_order_id: Some(unknown.remote_order_id.clone()),
            });
        }
        self.persist_snapshot()?;
        Ok(local)
    }
}
