use std::collections::BTreeMap;
use std::convert::Infallible;
use std::time::Duration;

use kairos_capital_contract::{
    CancelFundingObjectiveRequest, CapitalAvailabilityResponse, CapitalControlError,
    CapitalControlResponse, CapitalDemandResponse, CapitalDemandStatus, CapitalHealthResponse,
    CapitalPlanReconcileStatus, CapitalReadinessStatus, FundingObjectiveStatus,
    ObserveCapitalDemandRequest, PublishFundingObjectiveRequest, QueryCapitalAvailabilityRequest,
    ReconcileCapitalPlanRequest, ReconcileCapitalPlanResponse,
};
use kairos_conflux::{
    AssetTransferCommand, AssetTransferStatusQuery, ConfluxActor, ConfluxEvent, Context,
    EarnActionStatusQuery, EarnCommand, EarnProductQuery, IndexedMutation, SystemEvent,
};
use kairos_primitives::runtime::ActorId;
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use kairos_protocol::control::jsonrpc::{ErrorObjectOwned, RpcResult, business_error};

use super::{CapitalProcess, CapitalProcessError};
use crate::application::contract::{capital_current_view, capital_event};
use crate::application::{
    AuthorizeCapitalPlan, CancelFundingObjective, CapitalDemandReceipt, CapitalRpcActor,
    EvaluateCapitalGroup, ExpireCapitalDemands, ExpireCapitalPlans, ExpireFundingObjectives,
    FundingObjectiveReceipt, ObserveCapitalDemand, ObserveCapitalFacts,
    ObserveCapitalMemberAccount, ObserveCapitalSettlement, PublishFundingObjective,
    RecordCapitalRecoveryRequired,
};
use crate::services::facts::{read_location_facts, read_member_account_observation};
use crate::{
    CapitalDemand, CapitalMemberReadinessRole, CapitalOperationStatus, CapitalPlanId,
    CapitalPlanStatus, CapitalReadiness, CapitalRouteKind, FundingLocation, FundingObjective,
    FundingPriority,
};

const CAPITAL_BUSINESS_ERROR_CODE: i32 = -31_003;

impl<C> ConfluxActor for CapitalProcess<C>
where
    C: AssetTransferCommand
        + AssetTransferStatusQuery
        + EarnCommand
        + EarnActionStatusQuery
        + EarnProductQuery
        + Send
        + 'static,
{
    type FatalError = CapitalProcessError;
    type LocalEvent = Infallible;

    async fn started(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        if self.conflux.is_none() {
            return Err(CapitalProcessError::Invalid(
                "Capital Conflux runtime was not configured".into(),
            ));
        }
        context.spawn_timer("capital-facts", Duration::from_millis(500));
        self.publish_contract_outputs(context)
    }

    async fn handle(
        &mut self,
        event: ConfluxEvent,
        context: &mut Context<'_, Self>,
    ) -> Result<(), Self::FatalError> {
        match event {
            ConfluxEvent::System(SystemEvent::Timer { name, .. }) if name == "capital-facts" => {
                if let Err(error) = self.refresh_facts(context).await {
                    tracing::warn!(event = "capital_facts_refresh_failed", error = %error);
                    let evaluated_at = UnixNanos::new(now_unix_nanos());
                    if let Err(evaluation_error) = self
                        .application_mut()
                        .evaluate(EvaluateCapitalGroup { evaluated_at })
                    {
                        tracing::warn!(event = "capital_degraded_evaluation_failed", error = %evaluation_error);
                    }
                }
            },
            _ => {},
        };
        self.publish_contract_outputs(context)?;
        Ok(())
    }

    async fn stopping(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        if let Some(state) = self.conflux.as_mut() {
            state.accepting_writes = false;
        }
        let plan_ids = self
            .application()
            .snapshot()
            .operations
            .into_iter()
            .filter(|operation| {
                matches!(
                    operation.status,
                    CapitalOperationStatus::Dispatching
                        | CapitalOperationStatus::AwaitingParticipant
                        | CapitalOperationStatus::Indeterminate
                )
            })
            .map(|operation| operation.plan_id)
            .collect::<std::collections::BTreeSet<_>>();
        let reconciliation = tokio::time::timeout(Duration::from_secs(5), async {
            for plan_id in plan_ids {
                if let Some(plan) = self.application().plan(&plan_id).cloned() {
                    if let Err(error) = self.validate_current_transfer_leases(
                        plan.source.account_id.as_str(),
                        plan.destination.account_id.as_str(),
                    ) {
                        tracing::warn!(event = "capital_shutdown_fence_invalid", plan_id = %plan_id, error = %error);
                        continue;
                    }
                }
                let at = UnixNanos::new(now_unix_nanos());
                if let Err(error) = self.reconcile_capital_plan(plan_id.clone(), at).await {
                    tracing::warn!(event = "capital_shutdown_reconcile_failed", plan_id = %plan_id, error = %error);
                }
            }
        })
        .await;
        if reconciliation.is_err() {
            tracing::warn!(event = "capital_shutdown_reconcile_timed_out");
        }

        // Shutdown never creates a reverse operation. Persist a recovery
        // decision so the next instance can only reconcile the same operation.
        let stopped_at = UnixNanos::new(now_unix_nanos());
        let group_id = self.application().snapshot().capital_group_id;
        let unresolved = self
            .application()
            .snapshot()
            .operations
            .into_iter()
            .filter(|operation| {
                matches!(
                    operation.status,
                    CapitalOperationStatus::Dispatching
                        | CapitalOperationStatus::AwaitingParticipant
                        | CapitalOperationStatus::Indeterminate
                        | CapitalOperationStatus::AwaitingAccountObservation
                )
            })
            .map(|operation| (operation.plan_id, operation.updated_at))
            .collect::<BTreeMap<_, _>>();
        for (plan_id, operation_updated_at) in unresolved {
            self.application_mut()
                .record_recovery_required(RecordCapitalRecoveryRequired {
                    capital_group_id: group_id.clone(),
                    plan_id,
                    reason: "Capital stopped with an unresolved delivered operation; reconcile the original operation before any new movement".into(),
                    at: stopped_at.max(operation_updated_at),
                })?;
        }
        self.publish_contract_outputs(context)
    }
}

impl<C> CapitalRpcActor for CapitalProcess<C>
where
    C: AssetTransferCommand
        + AssetTransferStatusQuery
        + EarnCommand
        + EarnActionStatusQuery
        + EarnProductQuery
        + Send
        + 'static,
{
    async fn health(
        &mut self,
        (): (),
        context: &mut Context<'_, Self>,
    ) -> RpcResult<CapitalHealthResponse> {
        let response = CapitalHealthResponse {
            status: if self.conflux.is_some() {
                "ready".into()
            } else {
                "degraded".into()
            },
        };
        self.publish_rpc_outputs(context)?;
        Ok(response)
    }

    async fn publish_funding_objective(
        &mut self,
        request: PublishFundingObjectiveRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<CapitalControlResponse> {
        let response = self.publish_funding_objective_control(request);
        self.publish_rpc_outputs(context)?;
        Ok(response)
    }

    async fn cancel_funding_objective(
        &mut self,
        request: CancelFundingObjectiveRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<CapitalControlResponse> {
        let response = self.cancel_funding_objective_control(request);
        self.publish_rpc_outputs(context)?;
        Ok(response)
    }

    async fn observe_capital_demand(
        &mut self,
        request: ObserveCapitalDemandRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<CapitalDemandResponse> {
        let response = self.observe_capital_demand_control(request);
        self.publish_rpc_outputs(context)?;
        Ok(response)
    }

    async fn query_capital_availability(
        &mut self,
        request: QueryCapitalAvailabilityRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<CapitalAvailabilityResponse> {
        let response = self
            .query_availability(request)
            .map_err(rpc_capital_error)?;
        self.publish_rpc_outputs(context)?;
        Ok(response)
    }

    async fn reconcile_capital_plan(
        &mut self,
        request: ReconcileCapitalPlanRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReconcileCapitalPlanResponse> {
        let response = self.reconcile_capital_plan_control(request).await;
        self.publish_rpc_outputs(context)?;
        Ok(response)
    }
}

impl<C> CapitalProcess<C>
where
    C: AssetTransferCommand
        + AssetTransferStatusQuery
        + EarnCommand
        + EarnActionStatusQuery
        + EarnProductQuery
        + Send
        + 'static,
{
    fn publish_rpc_outputs(&mut self, context: &mut Context<'_, Self>) -> RpcResult<()> {
        self.publish_contract_outputs(context)
            .map_err(rpc_process_error)
    }

    fn publish_funding_objective_control(
        &mut self,
        request: PublishFundingObjectiveRequest,
    ) -> CapitalControlResponse {
        if !self.accepting_writes() {
            return rejected_objective(
                request.request_id,
                request.objective_id,
                request.version,
                admission_closed(),
            );
        }
        let request_id = request.request_id.clone();
        let objective_id = request.objective_id.clone();
        let version = request.version;
        let command = PublishFundingObjective {
            capital_group_id: request.capital_group_id,
            objective: FundingObjective {
                objective_id: request.objective_id,
                version: request.version,
                strategy_id: request.strategy_id,
                destination: location(request.destination),
                desired_available: request.desired_available,
                required_by: request.required_by_unix_nanos,
                expires_at: request.expires_at_unix_nanos,
                priority: priority(request.priority),
                confidence_bps: request.confidence_bps,
                strategy_decision_id: request.strategy_decision_id,
            },
            observed_at: request.observed_at_unix_nanos,
        };
        match self.application_mut().publish_funding_objective(command) {
            Ok(receipt) => objective_response(request_id, receipt),
            Err(error) => rejected_objective(
                request_id,
                objective_id,
                version,
                control_error("capital_rejected", error.to_string(), false),
            ),
        }
    }

    fn cancel_funding_objective_control(
        &mut self,
        request: CancelFundingObjectiveRequest,
    ) -> CapitalControlResponse {
        let request_id = request.request_id.clone();
        let objective_id = request.objective_id.clone();
        let version = request.expected_version;
        let command = CancelFundingObjective {
            capital_group_id: request.capital_group_id,
            objective_id: request.objective_id,
            expected_version: request.expected_version,
            observed_at: request.observed_at_unix_nanos,
        };
        match self.application_mut().cancel_funding_objective(command) {
            Ok(receipt) => objective_response(request_id, receipt),
            Err(error) => rejected_objective(
                request_id,
                objective_id,
                version,
                control_error("capital_rejected", error.to_string(), false),
            ),
        }
    }

    fn observe_capital_demand_control(
        &mut self,
        request: ObserveCapitalDemandRequest,
    ) -> CapitalDemandResponse {
        let request_id = request.request_id.clone();
        let demand_id = request.demand_id.clone();
        let result = if !self.accepting_writes() {
            Err(admission_closed())
        } else {
            self.validate_lease_fence(
                request.destination.account_id.as_str(),
                &request.destination_lease_fence,
            )
            .map_err(|message| control_error("capital_fence_invalid", message, false))
            .and_then(|()| {
                self.application_mut()
                    .observe_demand(ObserveCapitalDemand {
                        capital_group_id: request.capital_group_id,
                        demand: CapitalDemand {
                            demand_id: request.demand_id,
                            idempotency_key: request.idempotency_key,
                            strategy_id: request.strategy_id,
                            destination: location(request.destination),
                            observed_shortfall: request.observed_shortfall,
                            observed_at: request.observed_at_unix_nanos,
                            required_by: request.required_by_unix_nanos,
                            expires_at: request.expires_at_unix_nanos,
                            priority: priority(request.priority),
                            confidence_bps: request.confidence_bps,
                            account_watermark: request.account_watermark,
                            risk_watermark: request.risk_watermark,
                            launch_id: request.launch_id,
                            instance_id: request.instance_id,
                            destination_lease_fence: request.destination_lease_fence,
                            causal_references: request.causal_references,
                        },
                    })
                    .map_err(|error| control_error("capital_rejected", error.to_string(), false))
            })
        };
        let (status, error) = match result {
            Ok(CapitalDemandReceipt::Accepted(_)) => (CapitalDemandStatus::Accepted, None),
            Ok(CapitalDemandReceipt::Duplicate(_)) => (CapitalDemandStatus::Duplicate, None),
            Err(error) => (CapitalDemandStatus::Rejected, Some(error)),
        };
        CapitalDemandResponse {
            request_id,
            demand_id,
            status,
            error,
        }
    }

    async fn reconcile_capital_plan_control(
        &mut self,
        request: ReconcileCapitalPlanRequest,
    ) -> ReconcileCapitalPlanResponse {
        let request_id = request.request_id.clone();
        let plan_id = request.plan_id.clone();
        let before = self.application().plan(&plan_id).cloned();
        let result = if request.capital_group_id != self.application().snapshot().capital_group_id {
            Err(control_error(
                "capital_group_mismatch",
                "reconcile request does not address this Capital group",
                false,
            ))
        } else if let Some(plan) = before.as_ref() {
            self.validate_current_transfer_leases(
                plan.source.account_id.as_str(),
                plan.destination.account_id.as_str(),
            )
            .map_err(|message| control_error("capital_fence_invalid", message, false))
        } else {
            Err(control_error(
                "capital_plan_not_found",
                "Capital plan was not found",
                false,
            ))
        };
        match result {
            Ok(()) => match self
                .reconcile_capital_plan(plan_id.clone(), request.observed_at_unix_nanos)
                .await
            {
                Ok(after) => ReconcileCapitalPlanResponse {
                    request_id,
                    plan_id,
                    status: if before.as_ref() == Some(&after) {
                        CapitalPlanReconcileStatus::Unchanged
                    } else {
                        CapitalPlanReconcileStatus::Reconciled
                    },
                    error: None,
                },
                Err(error) => rejected_reconcile(
                    request_id,
                    plan_id,
                    control_error("capital_reconcile_rejected", error.to_string(), false),
                ),
            },
            Err(error) => rejected_reconcile(request_id, plan_id, error),
        }
    }

    async fn refresh_facts(
        &mut self,
        context: &mut Context<'_, Self>,
    ) -> Result<(), CapitalProcessError> {
        let state = self.conflux.as_ref().ok_or_else(|| {
            CapitalProcessError::Invalid("Capital Conflux runtime was not configured".into())
        })?;
        let instance_id = state.config.instance_id.clone();
        let automatic_execution = state.config.automatic_execution;
        let plan_ttl_nanos = state.config.plan_ttl_nanos;
        let identity = state.config.identity.clone();
        let accepting_writes = state.accepting_writes;
        let snapshot = self.application().snapshot();
        let capital_group_id = snapshot.capital_group_id.clone();
        let policies = snapshot.policies;
        let routes = snapshot.routes;
        let strategy_id = snapshot.strategy_id;
        let members = snapshot.members;
        if policies.is_empty() {
            return Ok(());
        }

        let evaluated_at = UnixNanos::new(now_unix_nanos());
        let member_observations = members
            .iter()
            .map(|member| {
                context
                    .account_client(member.account_id.as_str())
                    .ok_or_else(|| {
                        format!(
                            "Account contract client is not configured: {}",
                            member.account_id
                        )
                    })
                    .and_then(|account| read_member_account_observation(account, &identity, member))
                    .unwrap_or_else(|error| {
                        tracing::warn!(
                            event = "capital_member_account_unavailable",
                            account_id = %member.account_id,
                            error = %error
                        );
                        crate::CapitalMemberAccountObservation {
                            broker: member.broker.clone(),
                            account_id: member.account_id.clone(),
                            account_watermark: Sequence::new(0),
                            account_observed_at: evaluated_at,
                            account_complete: false,
                        }
                    })
            })
            .collect::<Vec<_>>();
        for observation in member_observations {
            self.application_mut()
                .observe_member_account(ObserveCapitalMemberAccount {
                    capital_group_id: capital_group_id.clone(),
                    observation,
                })?;
        }

        let risk_current = context
            .risk_client("risk")
            .ok_or_else(|| {
                CapitalProcessError::Connection(
                    "Risk contract client is not configured: risk".into(),
                )
            })?
            .indexed_current(
                &identity,
                ActorId::new(format!("risk:{instance_id}"))
                    .map_err(|error| CapitalProcessError::Connection(error.to_string()))?,
            )
            .map_err(|error| CapitalProcessError::Connection(error.to_string()))?;
        let risk_snapshot = risk_current
            .snapshot()
            .map_err(|error| CapitalProcessError::Connection(error.to_string()))?;
        let risk_state = risk_snapshot
            .state()
            .map_err(|error| CapitalProcessError::Connection(error.to_string()))?;
        let risk_metadata = risk_snapshot.metadata();
        let risk_watermark = Sequence::new(risk_metadata.applied_event_sequence);
        let risk_policy_version = Generation::new(risk_state.policy_version());

        let locations = policies
            .iter()
            .map(|policy| policy.destination.clone())
            .chain(
                routes
                    .iter()
                    .flat_map(|route| [route.source.clone(), route.destination.clone()]),
            )
            .collect::<std::collections::BTreeSet<_>>();
        let mut facts = Vec::new();
        for location in &locations {
            let account = context
                .account_client(location.account_id.as_str())
                .ok_or_else(|| {
                    CapitalProcessError::Connection(format!(
                        "Account contract client is not configured: {}",
                        location.account_id
                    ))
                })?;
            match read_location_facts(
                account,
                &identity,
                location,
                strategy_id.as_str(),
                &risk_snapshot,
                risk_policy_version,
                risk_watermark,
            ) {
                Ok(value) => facts.push(value),
                Err(error) => {
                    let optional = members.iter().any(|member| {
                        member.broker == location.broker
                            && member.account_id == location.account_id
                            && member.readiness_role == CapitalMemberReadinessRole::Optional
                    });
                    if optional {
                        tracing::warn!(
                            event = "capital_optional_location_unavailable",
                            account_id = %location.account_id,
                            segment = %location.segment,
                            asset = %location.asset,
                            error = %error
                        );
                        continue;
                    }
                    return Err(CapitalProcessError::Connection(error));
                },
            }
        }
        let facts_by_location = facts
            .iter()
            .cloned()
            .map(|fact| (fact.destination.clone(), fact))
            .collect::<BTreeMap<_, _>>();
        for fact in facts {
            self.application_mut().observe_facts(ObserveCapitalFacts {
                capital_group_id: capital_group_id.clone(),
                facts: fact,
            })?;
        }
        self.application_mut()
            .expire_funding_objectives(ExpireFundingObjectives {
                observed_at: evaluated_at,
            })?;
        self.application_mut()
            .expire_demands(ExpireCapitalDemands {
                observed_at: evaluated_at,
            })?;
        self.application_mut().expire_plans(ExpireCapitalPlans {
            observed_at: evaluated_at,
        })?;
        self.application_mut()
            .evaluate(EvaluateCapitalGroup { evaluated_at })?;
        let settling = self
            .application()
            .snapshot()
            .plans
            .into_iter()
            .filter(|plan| plan.status == CapitalPlanStatus::Reconciling)
            .collect::<Vec<_>>();
        for plan in settling {
            let (Some(source), Some(destination)) = (
                facts_by_location.get(&plan.source),
                facts_by_location.get(&plan.destination),
            ) else {
                continue;
            };
            self.application_mut()
                .observe_settlement(ObserveCapitalSettlement {
                    capital_group_id: capital_group_id.clone(),
                    plan_id: plan.plan_id,
                    source: source.clone(),
                    destination: destination.clone(),
                    observed_at: evaluated_at,
                })?;
        }
        if !automatic_execution || !accepting_writes {
            return Ok(());
        }

        let active_plans = self
            .application()
            .snapshot()
            .plans
            .into_iter()
            .filter(|plan| {
                matches!(
                    plan.status,
                    CapitalPlanStatus::Authorized
                        | CapitalPlanStatus::Redeeming
                        | CapitalPlanStatus::AwaitingRedemption
                        | CapitalPlanStatus::Transferring
                        | CapitalPlanStatus::AwaitingTransfer
                        | CapitalPlanStatus::Subscribing
                        | CapitalPlanStatus::AwaitingSubscription
                        | CapitalPlanStatus::Available
                        | CapitalPlanStatus::Indeterminate
                )
            })
            .collect::<Vec<_>>();
        for plan in active_plans {
            if !self.reconciliation_is_due(&plan.plan_id, evaluated_at) {
                continue;
            }
            self.validate_current_transfer_leases(
                plan.source.account_id.as_str(),
                plan.destination.account_id.as_str(),
            )
            .map_err(CapitalProcessError::Invalid)?;
            let plan_id = plan.plan_id.clone();
            let result = self.execute_capital_plan(plan.plan_id, evaluated_at).await;
            self.schedule_reconciliation(&plan_id, evaluated_at);
            result?;
        }

        let snapshot = self.application().snapshot();
        for availability in snapshot
            .availability
            .iter()
            .filter(|view| view.readiness == CapitalReadiness::Ready && !view.deficit.is_zero())
        {
            let Some(route) = snapshot.routes.iter().find(|route| {
                route.enabled
                    && matches!(
                        route.kind,
                        CapitalRouteKind::InternalTransfer
                            | CapitalRouteKind::AccountTransfer
                            | CapitalRouteKind::EarnRedemptionThenTransfer
                    )
                    && route.destination == availability.destination
            }) else {
                continue;
            };
            self.validate_current_transfer_leases(
                route.source.account_id.as_str(),
                route.destination.account_id.as_str(),
            )
            .map_err(CapitalProcessError::Invalid)?;
            let plan_id = CapitalPlanId::new(format!(
                "capital-plan:{}:{}:{}",
                route.route_id,
                availability.account_watermark.get(),
                availability.risk_watermark.get()
            ))
            .map_err(|error| CapitalProcessError::Invalid(error.to_string()))?;
            let expires_at =
                UnixNanos::new(evaluated_at.get().checked_add(plan_ttl_nanos).ok_or_else(
                    || {
                        CapitalProcessError::Invalid(
                            "Capital plan expiry overflows UnixNanos".into(),
                        )
                    },
                )?);
            let plan = match self.application_mut().authorize_plan(AuthorizeCapitalPlan {
                capital_group_id: capital_group_id.clone(),
                plan_id,
                rebalance_decision_id: kairos_primitives::runtime::StrategyDecisionId::new(
                    format!(
                        "capital-rebalance:{}:{}:{}",
                        route.route_id,
                        availability.account_watermark.get(),
                        availability.risk_watermark.get()
                    ),
                )
                .map_err(|error| CapitalProcessError::Invalid(error.to_string()))?,
                route_id: route.route_id.clone(),
                source_authority: route.required_source_authority.clone(),
                created_at: evaluated_at,
                expires_at,
            }) {
                Ok(plan) => plan,
                Err(error) => {
                    tracing::debug!(event = "capital_plan_not_authorized", route_id = %route.route_id, error = %error);
                    continue;
                },
            };
            if plan.status == CapitalPlanStatus::Authorized {
                let plan_id = plan.plan_id.clone();
                self.execute_capital_plan(plan.plan_id, evaluated_at)
                    .await?;
                self.schedule_reconciliation(&plan_id, evaluated_at);
            }
        }

        for route in snapshot
            .routes
            .iter()
            .filter(|route| route.enabled && route.kind == CapitalRouteKind::EarnSubscription)
        {
            let Some(availability) = snapshot
                .availability
                .iter()
                .find(|view| view.destination == route.source)
            else {
                continue;
            };
            self.validate_current_transfer_leases(
                route.source.account_id.as_str(),
                route.destination.account_id.as_str(),
            )
            .map_err(CapitalProcessError::Invalid)?;
            let plan_id = CapitalPlanId::new(format!(
                "capital-yield-plan:{}:{}:{}",
                route.route_id,
                availability.account_watermark.get(),
                availability.risk_watermark.get()
            ))
            .map_err(|error| CapitalProcessError::Invalid(error.to_string()))?;
            let expires_at =
                UnixNanos::new(evaluated_at.get().checked_add(plan_ttl_nanos).ok_or_else(
                    || {
                        CapitalProcessError::Invalid(
                            "Capital plan expiry overflows UnixNanos".into(),
                        )
                    },
                )?);
            let command = AuthorizeCapitalPlan {
                capital_group_id: capital_group_id.clone(),
                plan_id,
                rebalance_decision_id: kairos_primitives::runtime::StrategyDecisionId::new(
                    format!(
                        "capital-yield-deployment:{}:{}:{}",
                        route.route_id,
                        availability.account_watermark.get(),
                        availability.risk_watermark.get()
                    ),
                )
                .map_err(|error| CapitalProcessError::Invalid(error.to_string()))?,
                route_id: route.route_id.clone(),
                source_authority: route.required_source_authority.clone(),
                created_at: evaluated_at,
                expires_at,
            };
            let plan = match self.authorize_earn_subscription_plan(command).await {
                Ok(Some(plan)) => plan,
                Ok(None) => continue,
                Err(error) => {
                    tracing::debug!(event = "capital_yield_plan_not_authorized", route_id = %route.route_id, error = %error);
                    continue;
                },
            };
            let plan_id = plan.plan_id.clone();
            self.execute_capital_plan(plan.plan_id, evaluated_at)
                .await?;
            self.schedule_reconciliation(&plan_id, evaluated_at);
        }
        Ok(())
    }

    fn reconciliation_is_due(&self, plan_id: &CapitalPlanId, now: UnixNanos) -> bool {
        self.conflux
            .as_ref()
            .and_then(|state| state.reconcile_after.get(plan_id.as_str()))
            .is_none_or(|next| now >= *next)
    }

    fn schedule_reconciliation(&mut self, plan_id: &CapitalPlanId, now: UnixNanos) {
        if let Some(state) = self.conflux.as_mut() {
            state.reconcile_after.insert(
                plan_id.to_string(),
                UnixNanos::new(now.get().saturating_add(5_000_000_000)),
            );
        }
    }

    fn query_availability(
        &self,
        request: kairos_capital_contract::QueryCapitalAvailabilityRequest,
    ) -> Result<CapitalAvailabilityResponse, CapitalControlError> {
        let location = location(request.location.clone());
        let snapshot = self.application().snapshot();
        if snapshot.capital_group_id != request.capital_group_id {
            return Err(control_error(
                "capital_group_mismatch",
                "Capital availability belongs to another group",
                false,
            ));
        }
        let view = self.application().availability(&location).ok_or_else(|| {
            control_error(
                "capital_availability_not_found",
                "Capital location has not been evaluated",
                true,
            )
        })?;
        let policy = snapshot
            .policies
            .iter()
            .find(|policy| policy.destination == location)
            .ok_or_else(|| {
                control_error(
                    "capital_internal",
                    "Capital availability policy disappeared",
                    true,
                )
            })?;
        Ok(CapitalAvailabilityResponse {
            request_id: request.request_id,
            capital_group_id: request.capital_group_id,
            location: request.location,
            readiness: match view.readiness {
                CapitalReadiness::WaitingForFacts => CapitalReadinessStatus::WaitingForFacts,
                CapitalReadiness::WaitingForAccounts => CapitalReadinessStatus::WaitingForAccounts,
                CapitalReadiness::Degraded => CapitalReadinessStatus::Degraded,
                CapitalReadiness::Ready => CapitalReadinessStatus::Ready,
            },
            policy_minimum: policy.minimum,
            policy_default_target: policy.default_target,
            policy_maximum: policy.maximum,
            policy_version: view.policy_version,
            active_objective_ids: view.active_objective_ids.clone(),
            active_demand_ids: view.active_demand_ids.clone(),
            desired_target: view.desired_target,
            observed_available: view.observed_available,
            effective_target: view.effective_target,
            deficit: view.deficit,
            account_watermark: view.account_watermark,
            risk_policy_version: view.risk_policy_version,
            risk_watermark: view.risk_watermark,
            evaluated_at_unix_nanos: view.evaluated_at,
            reason: view.reason.clone(),
        })
    }

    fn accepting_writes(&self) -> bool {
        self.conflux
            .as_ref()
            .is_some_and(|state| state.accepting_writes)
    }

    fn validate_lease_fence(&self, account_id: &str, provided: &str) -> Result<(), String> {
        let expected = self
            .conflux
            .as_ref()
            .and_then(|state| state.config.account_lease_fences.get(account_id))
            .ok_or_else(|| format!("Capital Account '{account_id}' has no lease fence"))?;
        if expected != provided {
            return Err(format!(
                "Capital Account '{account_id}' lease fence mismatch"
            ));
        }
        Ok(())
    }

    fn validate_current_transfer_leases(
        &self,
        source_account_id: &str,
        destination_account_id: &str,
    ) -> Result<(), String> {
        self.validate_current_source_lease(source_account_id)?;
        self.validate_current_source_lease(destination_account_id)?;
        let state = self
            .conflux
            .as_ref()
            .ok_or_else(|| "Capital Conflux runtime was not configured".to_string())?;
        if source_account_id == destination_account_id {
            return Ok(());
        }
        let source_controller = state.config.account_controllers.get(source_account_id);
        let destination_controller = state.config.account_controllers.get(destination_account_id);
        if source_controller != destination_controller {
            return Err("cross-Account transfer members do not share a controller lease".into());
        }
        let controller = source_controller
            .ok_or_else(|| "cross-Account transfer has no controller lease".to_string())?;
        if controller != source_account_id {
            self.validate_current_source_lease(controller)?;
        }
        Ok(())
    }

    fn validate_current_source_lease(&self, account_id: &str) -> Result<(), String> {
        let state = self
            .conflux
            .as_ref()
            .ok_or_else(|| "Capital Conflux runtime was not configured".to_string())?;
        let expected_fence = state
            .config
            .account_lease_fences
            .get(account_id)
            .ok_or_else(|| format!("Capital Account '{account_id}' has no lease fence"))?;
        let expected_broker = state
            .config
            .account_brokers
            .get(account_id)
            .ok_or_else(|| format!("Capital Account '{account_id}' has no broker metadata"))?;
        let entries = std::fs::read_dir(&state.config.account_lease_root).map_err(|error| {
            format!(
                "read Account lease directory '{}': {error}",
                state.config.account_lease_root.display()
            )
        })?;
        for entry in entries {
            let path = entry.map_err(|error| error.to_string())?.path();
            if path.extension().and_then(|value| value.to_str()) != Some("json") {
                continue;
            }
            let record: AccountLeaseRecord =
                serde_json::from_slice(&std::fs::read(&path).map_err(|error| error.to_string())?)
                    .map_err(|error| error.to_string())?;
            if record.account_id == account_id && record.broker == *expected_broker {
                if record.launch_instance_id != state.config.instance_id.as_str()
                    || record.fencing_token != *expected_fence
                {
                    return Err(format!("Capital Account '{account_id}' lease is stale"));
                }
                return Ok(());
            }
        }
        Err(format!(
            "Capital Account '{account_id}' has no current lease record"
        ))
    }

    fn publish_contract_outputs(
        &mut self,
        context: &mut Context<'_, Self>,
    ) -> Result<(), CapitalProcessError> {
        let state = self.conflux.as_ref().ok_or_else(|| {
            CapitalProcessError::Invalid("Capital Conflux runtime was not configured".into())
        })?;
        let identity = state.config.identity.clone();
        let producer_incarnation = state.producer_incarnation;
        let previous_indexed_values = state.published_indexed_values.clone();
        let snapshot = self.application().snapshot();
        let owner_id = format!("capital:{}", snapshot.capital_group_id);
        let view = capital_current_view(&self.application().snapshot());
        let next = kairos_capital_contract::encode_indexed_current(&view)
            .map_err(|error| CapitalProcessError::Connection(error.to_string()))?;
        let mut mutations = Vec::new();
        for ((database, key), _) in previous_indexed_values
            .iter()
            .filter(|(key, _)| !next.contains_key(*key))
        {
            mutations.push(IndexedMutation::Delete {
                database: database.clone(),
                key: key.clone(),
            });
        }
        for ((database, key), value) in &next {
            if previous_indexed_values.get(&(database.clone(), key.clone())) == Some(value) {
                continue;
            }
            mutations.push(IndexedMutation::Put {
                database: database.clone(),
                key: key.clone(),
                value: value.clone(),
            });
        }
        context
            .outputs()
            .indexed
            .apply(
                "capital-current",
                &mutations,
                view.event_sequence.get(),
                now_unix_nanos(),
            )
            .map_err(|error| CapitalProcessError::Connection(error.to_string()))?;
        self.conflux
            .as_mut()
            .expect("Capital Conflux checked above")
            .published_indexed_values = next;
        while let Some(event) = self.application().pending_event().cloned() {
            let event = capital_event(&event);
            let mut writer =
                kairos_capital_contract::FlatbuffersCapitalEventWriter::new_with_incarnation(
                    owner_id.clone(),
                    identity.clone(),
                    producer_incarnation,
                );
            if let Err(error) = writer.publish(&event) {
                tracing::warn!(
                    event = "capital_notification_encode_failed",
                    sequence = event.sequence().get(),
                    error = %error,
                );
                self.application_mut().acknowledge_event()?;
                continue;
            }
            if let Err(error) = context.outputs().aeron.publish(
                "capital-events",
                writer.last_payload.as_deref().unwrap_or_default(),
            ) {
                tracing::warn!(
                    event = "capital_notification_publish_failed",
                    sequence = event.sequence().get(),
                    error = %error,
                );
            }
            self.application_mut().acknowledge_event()?;
        }
        Ok(())
    }
}

#[derive(serde::Deserialize)]
struct AccountLeaseRecord {
    broker: String,
    account_id: String,
    launch_instance_id: String,
    fencing_token: String,
}

fn objective_response(
    request_id: kairos_primitives::runtime::RequestId,
    receipt: FundingObjectiveReceipt,
) -> CapitalControlResponse {
    let (record, status) = match receipt {
        FundingObjectiveReceipt::Accepted(record) => (record, FundingObjectiveStatus::Accepted),
        FundingObjectiveReceipt::Duplicate(record) => (record, FundingObjectiveStatus::Duplicate),
        FundingObjectiveReceipt::Cancelled(record) => (record, FundingObjectiveStatus::Cancelled),
    };
    CapitalControlResponse {
        request_id,
        objective_id: record.objective.objective_id,
        version: record.objective.version,
        status,
        error: None,
    }
}

fn rejected_objective(
    request_id: kairos_primitives::runtime::RequestId,
    objective_id: kairos_primitives::capital::FundingObjectiveId,
    version: kairos_primitives::time::Generation,
    error: CapitalControlError,
) -> CapitalControlResponse {
    CapitalControlResponse {
        request_id,
        objective_id,
        version,
        status: FundingObjectiveStatus::Rejected,
        error: Some(error),
    }
}

fn rejected_reconcile(
    request_id: kairos_primitives::runtime::RequestId,
    plan_id: kairos_primitives::capital::CapitalPlanId,
    error: CapitalControlError,
) -> ReconcileCapitalPlanResponse {
    ReconcileCapitalPlanResponse {
        request_id,
        plan_id,
        status: CapitalPlanReconcileStatus::Rejected,
        error: Some(error),
    }
}

fn admission_closed() -> CapitalControlError {
    control_error(
        "capital_admission_closed",
        "Capital is stopping and no longer accepts new work",
        true,
    )
}

fn control_error(
    code: impl Into<String>,
    message: impl Into<String>,
    retryable: bool,
) -> CapitalControlError {
    CapitalControlError {
        code: code.into(),
        message: message.into(),
        retryable,
        details: BTreeMap::new(),
    }
}

fn rpc_process_error(error: CapitalProcessError) -> ErrorObjectOwned {
    rpc_capital_error(control_error("capital_internal", error.to_string(), true))
}

fn rpc_capital_error(error: CapitalControlError) -> ErrorObjectOwned {
    business_error(CAPITAL_BUSINESS_ERROR_CODE, error.message.clone(), error)
}

fn location(value: kairos_capital_contract::FundingLocation) -> FundingLocation {
    FundingLocation {
        broker: value.broker,
        account_id: value.account_id,
        segment: value.segment,
        asset: value.asset,
    }
}

fn priority(value: kairos_capital_contract::FundingObjectivePriority) -> FundingPriority {
    match value {
        kairos_capital_contract::FundingObjectivePriority::Low => FundingPriority::Low,
        kairos_capital_contract::FundingObjectivePriority::Normal => FundingPriority::Normal,
        kairos_capital_contract::FundingObjectivePriority::High => FundingPriority::High,
        kairos_capital_contract::FundingObjectivePriority::Critical => FundingPriority::Critical,
    }
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| u64::try_from(duration.as_nanos()).unwrap_or(u64::MAX))
        .unwrap_or_default()
}
