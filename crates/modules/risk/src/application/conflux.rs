use std::collections::BTreeMap;
use std::convert::Infallible;

use kairos_conflux::{
    ConfluxActor, ConfluxEvent, Context, Contract, RestContract, SnapshotEnvelopeMetadata,
    SystemEvent,
};
use kairos_risk_contract::{
    AdvanceRiskTimeResponse, FlatbuffersRiskEventWriter, FlatbuffersRiskSnapshotWriter, Health,
    RiskCommandStatus, RiskControlError, RiskRestRequest, RiskRestResponse,
};

use super::{
    CloseCircuit, ConsumeReservation, OpenCircuit, PublishPolicy, ReleaseReservation,
    ResizeReservation, RiskApplication, RiskError,
};

/// The REST type pair of the Risk Contract. The Actor implements [`Contract`]
/// directly; this marker carries no client, endpoint, or runtime state.
pub struct RiskRest;

impl RestContract for RiskRest {
    type Request = RiskRestRequest;
    type Response = RiskRestResponse;
}

impl Contract for RiskApplication {
    type Rest = RiskRest;
}

impl ConfluxActor for RiskApplication {
    type FatalError = Infallible;
    type LocalEvent = Infallible;

    async fn started(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        if self.clock_mode() == super::RiskClockMode::Wall {
            context.spawn_timer("maintenance", self.maintenance_interval());
        }
        self.publish_contract_outputs(context);
        Ok(())
    }

    async fn handle(
        &mut self,
        event: ConfluxEvent<Self, Self::LocalEvent>,
        context: &mut Context<'_, Self>,
    ) -> Result<Option<RiskRestResponse>, Self::FatalError> {
        let response = match event {
            ConfluxEvent::Rest(request) => Some(self.handle_rest(request)),
            ConfluxEvent::System(SystemEvent::Timer {
                name,
                fired_at_unix_nanos,
            }) if name == "maintenance" => {
                let _ = self.maintenance_tick(fired_at_unix_nanos.into());
                None
            },
            ConfluxEvent::Local(value) => match value {},
            _ => None,
        };
        self.publish_contract_outputs(context);
        Ok(response)
    }
}

impl RiskApplication {
    fn publish_contract_outputs(&mut self, context: &mut Context<'_, Self>) {
        let view = super::contract::current_view(&self.current_view());
        let mut snapshot_encoder = FlatbuffersRiskSnapshotWriter::new(view.actor_id.to_string());
        if snapshot_encoder.publish(&view).is_ok() && context.outputs().mmap.contains("risk-latest")
        {
            let result = context.outputs().mmap.publish(
                "risk-latest",
                SnapshotEnvelopeMetadata {
                    resource_epoch: 1,
                    producer_incarnation: self.producer_incarnation,
                    generation: view.generation.get(),
                    applied_event_sequence: view.event_sequence.get(),
                    published_at_unix_nanos: now_unix_nanos(),
                },
                snapshot_encoder.last_payload.as_deref().unwrap_or_default(),
            );
            if let Err(error) = result {
                tracing::error!(event = "snapshot_publish_failed", component = "risk", error = %error);
            }
        }

        while let Some(event) = self.pending_event().cloned() {
            let event = super::contract::event(&event);
            if !context.outputs().aeron.contains("risk-events") {
                break;
            }
            let mut encoder = FlatbuffersRiskEventWriter::new_with_identity(
                view.actor_id.to_string(),
                self.publication_identity.clone(),
            );
            if encoder.publish(&event).is_err() {
                break;
            }
            if let Some(payload) = encoder.last_payload.as_deref() {
                if let Err(error) = context.outputs().aeron.publish("risk-events", payload) {
                    tracing::error!(event = "event_publish_failed", component = "risk", error = %error);
                    break;
                }
            }
            self.acknowledge_event();
        }
    }

    fn handle_rest(&mut self, request: RiskRestRequest) -> RiskRestResponse {
        match request {
            RiskRestRequest::Health => {
                let snapshot = self.snapshot();
                RiskRestResponse::Health(Ok(Health {
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
                }))
            },
            RiskRestRequest::PublishPolicy(request) => {
                let result = super::contract::policy_from(request.policy)
                    .map_err(invalid)
                    .map(|policy| PublishPolicy { policy })
                    .and_then(|request| self.publish_policy(request).map_err(control_error))
                    .map(|()| RiskCommandStatus {
                        status: "active".into(),
                    });
                RiskRestResponse::PublishPolicy(result)
            },
            RiskRestRequest::AuthorizeAndReserve(request) => {
                let result = super::contract::authorize_from(request)
                    .map_err(invalid)
                    .and_then(|request| {
                        let identity = (
                            request.account_id.clone(),
                            request.strategy_id.clone(),
                            request.instrument_id.clone(),
                        );
                        self.authorize_and_reserve(request)
                            .map_err(control_error)
                            .map(|decision| {
                                super::contract::decision(
                                    &decision,
                                    &identity.0,
                                    &identity.1,
                                    &identity.2,
                                )
                            })
                    });
                RiskRestResponse::AuthorizeAndReserve(result)
            },
            RiskRestRequest::PreTradeCheck(request) => {
                let result = super::contract::authorize_from(request)
                    .map_err(invalid)
                    .and_then(|request| {
                        let identity = (
                            request.account_id.clone(),
                            request.strategy_id.clone(),
                            request.instrument_id.clone(),
                        );
                        self.pre_trade_check(request)
                            .map_err(control_error)
                            .map(|decision| {
                                super::contract::decision(
                                    &decision,
                                    &identity.0,
                                    &identity.1,
                                    &identity.2,
                                )
                            })
                    });
                RiskRestResponse::PreTradeCheck(result)
            },
            RiskRestRequest::PostTradeCheck(request) => {
                let result = super::contract::authorize_from(request)
                    .map_err(invalid)
                    .and_then(|request| {
                        let identity = (
                            request.account_id.clone(),
                            request.strategy_id.clone(),
                            request.instrument_id.clone(),
                        );
                        self.post_trade_check(request)
                            .map_err(control_error)
                            .map(|decision| {
                                super::contract::decision(
                                    &decision,
                                    &identity.0,
                                    &identity.1,
                                    &identity.2,
                                )
                            })
                    });
                RiskRestResponse::PostTradeCheck(result)
            },
            RiskRestRequest::OpenCircuit(request) => {
                let result = super::contract::circuit_scope_from(request.scope)
                    .map_err(invalid)
                    .map(|scope| OpenCircuit {
                        scope,
                        at_unix_nanos: request.at_unix_nanos,
                        reset_at_unix_nanos: request.reset_at_unix_nanos,
                        reason: request.reason,
                    })
                    .and_then(|request| self.open_circuit(request).map_err(control_error))
                    .map(|value| super::contract::circuit(&value));
                RiskRestResponse::OpenCircuit(result)
            },
            RiskRestRequest::CloseCircuit(request) => {
                let result = super::contract::circuit_scope_from(request.scope)
                    .map_err(invalid)
                    .map(|scope| CloseCircuit {
                        scope,
                        at_unix_nanos: request.at_unix_nanos,
                    })
                    .and_then(|request| self.close_circuit(request).map_err(control_error))
                    .map(|value| super::contract::circuit(&value));
                RiskRestResponse::CloseCircuit(result)
            },
            RiskRestRequest::ResizeReservation(request) => {
                let result = super::contract::amount_from(request.amount)
                    .map_err(invalid)
                    .and_then(|amount| {
                        Ok(ResizeReservation {
                            reservation_id: request.reservation_id,
                            amount,
                            at_unix_nanos: request.at_unix_nanos,
                        })
                    })
                    .and_then(|request| self.resize(request).map_err(control_error))
                    .map(|value| super::contract::reservation(&value));
                RiskRestResponse::ResizeReservation(result)
            },
            RiskRestRequest::ReleaseReservation(request) => {
                let result = Ok(ReleaseReservation {
                    reservation_id: request.reservation_id,
                    at_unix_nanos: request.at_unix_nanos,
                })
                .and_then(|request| self.release(request).map_err(control_error))
                .map(|value| super::contract::reservation(&value));
                RiskRestResponse::ReleaseReservation(result)
            },
            RiskRestRequest::ConsumeReservation(request) => {
                let result = Ok(ConsumeReservation {
                    reservation_id: request.reservation_id,
                    at_unix_nanos: request.at_unix_nanos,
                })
                .and_then(|request| self.consume(request).map_err(control_error))
                .map(|value| super::contract::reservation(&value));
                RiskRestResponse::ConsumeReservation(result)
            },
            RiskRestRequest::AdvanceTime(request) => {
                let result = self
                    .advance_business_time(request.event_time_unix_nanos)
                    .map_err(control_error)
                    .map(|expired| AdvanceRiskTimeResponse {
                        event_time_unix_nanos: request.event_time_unix_nanos,
                        expired: expired as u64,
                    });
                RiskRestResponse::AdvanceTime(result)
            },
        }
    }
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

#[cfg(test)]
mod tests {
    use kairos_conflux::{Conflux, ConfluxConfig, ConfluxEvent, ConfluxSystem, ShutdownMode};
    use kairos_risk_contract::{Amount, AuthorizeRequest, RiskRestRequest, RiskRestResponse};

    #[tokio::test(flavor = "current_thread")]
    async fn typed_rest_pair_runs_through_the_single_actor_handle() {
        let actor = crate::composition::compose_risk_application("risk", Vec::new(), None).unwrap();
        let (conflux, handle) =
            Conflux::new(actor, ConfluxSystem::new(), ConfluxConfig::default()).unwrap();

        tokio::task::LocalSet::new()
            .run_until(async move {
                let process = tokio::task::spawn_local(conflux.run());
                let response = handle
                    .handle(ConfluxEvent::Rest(RiskRestRequest::AuthorizeAndReserve(
                        AuthorizeRequest {
                            request_id: kairos_primitives::runtime::RequestId::new("request-1")
                                .unwrap(),
                            idempotency_key: kairos_primitives::runtime::IdempotencyKey::new(
                                "key-1",
                            )
                            .unwrap(),
                            reservation_id: kairos_primitives::risk::ReservationId::new(
                                "reservation-1",
                            )
                            .unwrap(),
                            account_id: kairos_primitives::account::AccountId::new("account-1")
                                .unwrap(),
                            strategy_id: kairos_primitives::runtime::StrategyId::new("strategy-1")
                                .unwrap(),
                            instrument_id: kairos_primitives::reference::InstrumentId::new(
                                "instrument-1",
                            )
                            .unwrap(),
                            exchange_id: kairos_primitives::reference::Exchange::new("exchange-1")
                                .unwrap(),
                            proposal: kairos_risk_contract::TradeRiskProposal {
                                notional: Amount::new(10, 0).unwrap(),
                                initial_margin_rate_bps: 10_000.into(),
                                account_segment: kairos_primitives::account::SegmentKey::new(
                                    "usd-m",
                                )
                                .unwrap(),
                                collateral_asset: kairos_primitives::reference::Currency::new(
                                    "USDT",
                                )
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
                        },
                    )))
                    .await
                    .unwrap()
                    .unwrap();
                let RiskRestResponse::AuthorizeAndReserve(result) = response else {
                    panic!("unexpected Risk response variant");
                };
                let decision = result.expect("risk request is evaluated successfully");
                assert!(!decision.allowed);

                handle.shutdown(ShutdownMode::Drain);
                process.await.unwrap().unwrap();
            })
            .await;
    }
}
