use std::collections::BTreeMap;
use std::convert::Infallible;

use kairos_conflux::{ConfluxActor, ConfluxEvent, Context, IndexedMutation, SystemEvent};
use kairos_protocol::control::jsonrpc::{ErrorObjectOwned, RpcResult, business_error};
use kairos_risk_contract::{
    AdvanceRiskTimeRequest, AdvanceRiskTimeResponse, AuthorizeRequest, CloseCircuitRequest,
    ConsumeReservationRequest, FlatbuffersRiskEventWriter, Health, OpenCircuitRequest,
    PublishPolicyRequest, ReleaseReservationRequest, Reservation, ResizeReservationRequest,
    RiskCommandStatus, RiskControlError, RiskDecision, encode_indexed_current,
};

use crate::application::{
    CloseCircuit, ConsumeReservation, OpenCircuit, PublishPolicy, ReleaseReservation,
    ResizeReservation, RiskApplication, RiskError, RiskRpcActor, contract,
};

const RISK_BUSINESS_ERROR_CODE: i32 = -31_002;

impl ConfluxActor for RiskApplication {
    type FatalError = Infallible;
    type LocalEvent = Infallible;

    async fn started(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        if self.clock_mode() == crate::application::RiskClockMode::Wall {
            context.spawn_timer("maintenance", self.maintenance_interval());
        }
        self.publish_contract_outputs(context);
        Ok(())
    }

    async fn handle(
        &mut self,
        event: ConfluxEvent,
        context: &mut Context<'_, Self>,
    ) -> Result<(), Self::FatalError> {
        match event {
            ConfluxEvent::System(SystemEvent::Timer {
                name,
                fired_at_unix_nanos,
            }) if name == "maintenance" => {
                let _ = self.maintenance_tick(fired_at_unix_nanos.into());
            },
            _ => {},
        };
        self.publish_contract_outputs(context);
        Ok(())
    }
}

impl RiskRpcActor for RiskApplication {
    async fn health(&mut self, (): (), context: &mut Context<'_, Self>) -> RpcResult<Health> {
        let snapshot = self.snapshot();
        let response = Health {
            status: "ready".into(),
            generation: snapshot.generation,
            event_sequence: snapshot.event_sequence,
            policy_version: snapshot.policy_version,
            reservation_count: snapshot.reservations.len() as u64,
            open_circuit_count: snapshot
                .circuits
                .iter()
                .filter(|circuit| circuit.open)
                .count() as u64,
        };
        self.publish_contract_outputs(context);
        Ok(response)
    }

    async fn publish_policy(
        &mut self,
        request: PublishPolicyRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<RiskCommandStatus> {
        let policy = contract::policy_from(request.policy).map_err(rpc_invalid)?;
        self.publish_policy(PublishPolicy { policy })
            .map_err(rpc_control_error)?;
        self.publish_contract_outputs(context);
        Ok(RiskCommandStatus {
            status: "active".into(),
        })
    }

    async fn authorize_and_reserve(
        &mut self,
        request: AuthorizeRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<RiskDecision> {
        let decision =
            self.authorize_request(request, RiskControlOperation::AuthorizeAndReserve)?;
        self.publish_contract_outputs(context);
        Ok(decision)
    }

    async fn pre_trade_check(
        &mut self,
        request: AuthorizeRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<RiskDecision> {
        let decision = self.authorize_request(request, RiskControlOperation::PreTradeCheck)?;
        self.publish_contract_outputs(context);
        Ok(decision)
    }

    async fn post_trade_check(
        &mut self,
        request: AuthorizeRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<RiskDecision> {
        let decision = self.authorize_request(request, RiskControlOperation::PostTradeCheck)?;
        self.publish_contract_outputs(context);
        Ok(decision)
    }

    async fn open_circuit(
        &mut self,
        request: OpenCircuitRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<kairos_risk_contract::CircuitState> {
        let scope = contract::circuit_scope_from(request.scope).map_err(rpc_invalid)?;
        let circuit = self
            .open_circuit(OpenCircuit {
                scope,
                at_unix_nanos: request.at_unix_nanos,
                reset_at_unix_nanos: request.reset_at_unix_nanos,
                reason: request.reason,
            })
            .map_err(rpc_control_error)?;
        self.publish_contract_outputs(context);
        Ok(contract::circuit(&circuit))
    }

    async fn close_circuit(
        &mut self,
        request: CloseCircuitRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<kairos_risk_contract::CircuitState> {
        let scope = contract::circuit_scope_from(request.scope).map_err(rpc_invalid)?;
        let circuit = self
            .close_circuit(CloseCircuit {
                scope,
                at_unix_nanos: request.at_unix_nanos,
            })
            .map_err(rpc_control_error)?;
        self.publish_contract_outputs(context);
        Ok(contract::circuit(&circuit))
    }

    async fn resize_reservation(
        &mut self,
        request: ResizeReservationRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<Reservation> {
        let amount = contract::amount_from(request.amount).map_err(rpc_invalid)?;
        let reservation = self
            .resize(ResizeReservation {
                reservation_id: request.reservation_id,
                amount,
                at_unix_nanos: request.at_unix_nanos,
            })
            .map_err(rpc_control_error)?;
        self.publish_contract_outputs(context);
        Ok(contract::reservation(&reservation))
    }

    async fn release_reservation(
        &mut self,
        request: ReleaseReservationRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<Reservation> {
        let reservation = self
            .release(ReleaseReservation {
                reservation_id: request.reservation_id,
                at_unix_nanos: request.at_unix_nanos,
            })
            .map_err(rpc_control_error)?;
        self.publish_contract_outputs(context);
        Ok(contract::reservation(&reservation))
    }

    async fn consume_reservation(
        &mut self,
        request: ConsumeReservationRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<Reservation> {
        let reservation = self
            .consume(ConsumeReservation {
                reservation_id: request.reservation_id,
                at_unix_nanos: request.at_unix_nanos,
            })
            .map_err(rpc_control_error)?;
        self.publish_contract_outputs(context);
        Ok(contract::reservation(&reservation))
    }

    async fn advance_time(
        &mut self,
        request: AdvanceRiskTimeRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<AdvanceRiskTimeResponse> {
        let expired = self
            .advance_business_time(request.event_time_unix_nanos)
            .map_err(rpc_control_error)?;
        self.publish_contract_outputs(context);
        Ok(AdvanceRiskTimeResponse {
            event_time_unix_nanos: request.event_time_unix_nanos,
            expired: expired as u64,
        })
    }
}

impl RiskApplication {
    fn publish_contract_outputs(&mut self, context: &mut Context<'_, Self>) {
        let view = contract::current_view(&self.current_view());
        match encode_indexed_current(&view) {
            Ok(next) => {
                let mut mutations = Vec::new();
                for ((database, key), _) in self
                    .published_indexed_values
                    .iter()
                    .filter(|(key, _)| !next.contains_key(*key))
                {
                    mutations.push(IndexedMutation::Delete {
                        database: database.clone(),
                        key: key.clone(),
                    });
                }
                for ((database, key), value) in &next {
                    if self
                        .published_indexed_values
                        .get(&(database.clone(), key.clone()))
                        == Some(value)
                    {
                        continue;
                    }
                    mutations.push(IndexedMutation::Put {
                        database: database.clone(),
                        key: key.clone(),
                        value: value.clone(),
                    });
                }
                match context.outputs().indexed.apply(
                    "risk-current",
                    &mutations,
                    view.event_sequence.get(),
                    now_unix_nanos(),
                ) {
                    Ok(()) => self.published_indexed_values = next,
                    Err(error) => {
                        tracing::error!(event = "current_view_publish_failed", component = "risk", error = %error);
                        return;
                    },
                }
            },
            Err(error) => {
                tracing::error!(event = "current_view_encode_failed", component = "risk", error = %error);
                return;
            },
        }

        while let Some(event) = self.pending_event().cloned() {
            let event = contract::event(&event);
            if !context.outputs().aeron.contains("risk-events") {
                self.acknowledge_event();
                continue;
            }
            let mut encoder = FlatbuffersRiskEventWriter::new_with_incarnation(
                view.actor_id.clone(),
                self.publication_identity.clone(),
                self.producer_incarnation,
            );
            if let Err(error) = encoder.publish(&event) {
                tracing::error!(
                    event = "risk_notification_encode_failed",
                    error = %error,
                );
                self.acknowledge_event();
                continue;
            }
            if let Some(payload) = encoder.last_payload.as_deref() {
                if let Err(error) = context.outputs().aeron.publish("risk-events", payload) {
                    tracing::warn!(
                        event = "risk_notification_publish_failed",
                        error = %error,
                    );
                }
            }
            self.acknowledge_event();
        }
    }

    fn authorize_request(
        &mut self,
        request: AuthorizeRequest,
        operation: RiskControlOperation,
    ) -> RpcResult<RiskDecision> {
        let identity = (
            request.account_id.clone(),
            request.strategy_id.clone(),
            request.instrument_id.clone(),
        );
        let request = contract::authorize_from(request).map_err(rpc_invalid)?;
        let decision = match operation {
            RiskControlOperation::AuthorizeAndReserve => self.authorize_and_reserve(request),
            RiskControlOperation::PreTradeCheck => self.pre_trade_check(request),
            RiskControlOperation::PostTradeCheck => self.post_trade_check(request),
        }
        .map_err(rpc_control_error)?;
        Ok(contract::decision(
            &decision,
            &identity.0,
            &identity.1,
            &identity.2,
        ))
    }
}

enum RiskControlOperation {
    AuthorizeAndReserve,
    PreTradeCheck,
    PostTradeCheck,
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u128::from(u64::MAX)) as u64
}

fn control_error(error: RiskError) -> RiskControlError {
    let (code, retryable) = match error {
        RiskError::Invalid(_) => ("risk.invalid", false),
        RiskError::Rejected(_) => ("risk.rejected", false),
        RiskError::Persistence(_) => ("risk.persistence", true),
        RiskError::Busy => ("risk.busy", true),
        RiskError::State(_) => ("risk.state", false),
    };
    RiskControlError {
        code: code.into(),
        message: error.to_string(),
        retryable,
        details: BTreeMap::new(),
    }
}

fn invalid(message: impl Into<String>) -> RiskControlError {
    RiskControlError {
        code: "risk.invalid".into(),
        message: message.into(),
        retryable: false,
        details: BTreeMap::new(),
    }
}

fn rpc_control_error(error: RiskError) -> ErrorObjectOwned {
    rpc_risk_error(control_error(error))
}

fn rpc_invalid(message: impl Into<String>) -> ErrorObjectOwned {
    rpc_risk_error(invalid(message))
}

fn rpc_risk_error(error: RiskControlError) -> ErrorObjectOwned {
    business_error(RISK_BUSINESS_ERROR_CODE, error.message.clone(), error)
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use kairos_conflux::{Conflux, ConfluxConfig, ConfluxSystem, ShutdownMode};
    use kairos_risk_contract::{Amount, AuthorizeRequest};

    use super::RiskRpcActor;

    #[tokio::test(flavor = "current_thread")]
    async fn rpc_actor_invocation_runs_through_the_single_actor_handle() {
        let actor = crate::composition::compose_risk_application("risk", Vec::new(), None).unwrap();
        let (conflux, handle) =
            Conflux::new(actor, ConfluxSystem::new(), ConfluxConfig::default()).unwrap();

        tokio::task::LocalSet::new()
            .run_until(async move {
                let process = tokio::task::spawn_local(conflux.run());
                let request = AuthorizeRequest {
                    request_id: kairos_primitives::runtime::RequestId::new("request-1").unwrap(),
                    idempotency_key: kairos_primitives::runtime::IdempotencyKey::new("key-1")
                        .unwrap(),
                    reservation_id: kairos_primitives::risk::ReservationId::new("reservation-1")
                        .unwrap(),
                    account_id: kairos_primitives::account::AccountId::new("account-1").unwrap(),
                    strategy_id: kairos_primitives::runtime::StrategyId::new("strategy-1").unwrap(),
                    instrument_id: kairos_primitives::reference::InstrumentId::new("instrument-1")
                        .unwrap(),
                    exchange_id: kairos_primitives::reference::ExchangeId::new("exchange-1")
                        .unwrap(),
                    proposal: kairos_risk_contract::TradeRiskProposal {
                        notional: Amount::new(10, 0).unwrap(),
                        initial_margin_rate_bps: 10_000.into(),
                        account_segment: kairos_primitives::account::SegmentKey::new("usd-m")
                            .unwrap(),
                        collateral_asset: kairos_primitives::reference::Currency::new("USDT")
                            .unwrap(),
                        reduce_only: false,
                        margin_rule_id: kairos_primitives::risk::MarginRuleCode::new(
                            "test:fully-funded",
                        )
                        .unwrap(),
                    },
                    at_unix_nanos: 1.into(),
                    reservation_ttl_nanos: 10.into(),
                    dependency_generation: 1.into(),
                    dependency_event_sequence: 1.into(),
                    context: None,
                };
                let decision = handle
                    .rpc_actor_invocation(Duration::from_secs(1))
                    .call(move |actor, context| {
                        Box::pin(async move {
                            RiskRpcActor::authorize_and_reserve(actor, request, context).await
                        })
                    })
                    .await
                    .unwrap();
                assert!(!decision.allowed);

                handle.shutdown(ShutdownMode::Drain);
                process.await.unwrap().unwrap();
            })
            .await;
    }
}
