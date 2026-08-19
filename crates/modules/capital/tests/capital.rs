use kairos_capital::composition::{
    compose_capital_application, compose_capital_transfer_process,
    compose_persistent_capital_application,
};
use kairos_capital::{
    AuthorizeCapitalPlan, BeginCapitalOperation, CancelFundingObjective, CapitalApplication,
    CapitalDemand, CapitalDemandId, CapitalDemandReceipt, CapitalDemandStatus, CapitalError,
    CapitalFacts, CapitalGroupConfig, CapitalGroupId, CapitalGroupMember, CapitalOperationStatus,
    CapitalParticipantOperationState, CapitalPlanId, CapitalPlanStatus, CapitalPolicy,
    CapitalReadiness, CapitalReservationStatus, CapitalRouteId, CapitalRouteKind,
    CapitalSettlementClass, CapitalSubmissionOutcome, CapitalTransferRoute, EvaluateCapitalGroup,
    ExpireCapitalDemands, ExpireCapitalPlans, FundingLocation, FundingObjective,
    FundingObjectiveId, FundingObjectiveReceipt, FundingObjectiveStatus, FundingPriority,
    MarkCapitalDeliveryStarted, ObserveCapitalDemand, ObserveCapitalFacts,
    ObserveCapitalSettlement, PublishFundingObjective, RecordCapitalParticipantStatus,
    RecordCapitalSubmission, UpdateCapitalPolicy, UpdateCapitalRoute,
};
use kairos_integration::{
    AssetTransferCommand, AssetTransferQuery, AssetTransferRequest, AssetTransferState,
    AssetTransferStatus, AssetTransferStatusQuery, AssetTransferSubmission, CommandOutcome,
    IntegrationError,
};
use kairos_primitives::{
    AccountId, BasisPoints, BrokerId, Currency, Generation, IdempotencyKey, Quantity, SegmentKey,
    StrategyDecisionId, StrategyId, UnixNanos,
};

fn objective(version: u64) -> FundingObjective {
    FundingObjective {
        objective_id: FundingObjectiveId::new("usd-m-buffer").unwrap(),
        version: Generation::new(version),
        strategy_id: StrategyId::new("basis").unwrap(),
        destination: FundingLocation {
            broker: BrokerId::new("binance").unwrap(),
            account_id: AccountId::new("binance-subaccount-a").unwrap(),
            segment: SegmentKey::new("usd-m").unwrap(),
            asset: Currency::new("USDT").unwrap(),
        },
        desired_available: Quantity::new(80_000, 0).unwrap(),
        required_by: UnixNanos::new(200),
        expires_at: UnixNanos::new(500),
        priority: FundingPriority::High,
        confidence_bps: BasisPoints::new(8_000),
        strategy_decision_id: StrategyDecisionId::new("decision-7").unwrap(),
    }
}

fn group_config(group_id: &str) -> CapitalGroupConfig {
    CapitalGroupConfig {
        capital_group_id: CapitalGroupId::new(group_id).unwrap(),
        strategy_id: StrategyId::new("basis").unwrap(),
        environment: "live".into(),
        membership_version: Generation::new(1),
        members: vec![CapitalGroupMember {
            broker: BrokerId::new("binance").unwrap(),
            account_id: AccountId::new("binance-subaccount-a").unwrap(),
            permitted_segments: vec![
                SegmentKey::new("funding").unwrap(),
                SegmentKey::new("usd-m").unwrap(),
            ],
        }],
    }
}

fn policy() -> CapitalPolicy {
    CapitalPolicy {
        destination: objective(1).destination,
        version: Generation::new(1),
        minimum: Quantity::new(10, 0).unwrap(),
        default_target: Quantity::new(20, 0).unwrap(),
        maximum: Quantity::new(100, 0).unwrap(),
        stress_buffer: Quantity::new(5, 0).unwrap(),
        minimum_movement: Quantity::new(3, 0).unwrap(),
        hysteresis: Quantity::ZERO,
        deficit_dwell_nanos: 0,
        cooldown_nanos: 0,
        max_fact_age_nanos: 50,
    }
}

fn demand(id: &str, shortfall: i64, expires_at: u64) -> CapitalDemand {
    CapitalDemand {
        demand_id: CapitalDemandId::new(id).unwrap(),
        idempotency_key: IdempotencyKey::new(format!("demand:{id}")).unwrap(),
        strategy_id: StrategyId::new("basis").unwrap(),
        destination: objective(1).destination,
        observed_shortfall: Quantity::new(shortfall, 0).unwrap(),
        observed_at: UnixNanos::new(100),
        required_by: UnixNanos::new(120),
        expires_at: UnixNanos::new(expires_at),
        priority: FundingPriority::High,
        confidence_bps: BasisPoints::new(9_000),
        account_watermark: kairos_primitives::Sequence::new(11),
        risk_watermark: kairos_primitives::Sequence::new(17),
        launch_id: "basis-live".into(),
        instance_id: "instance-7".into(),
        destination_lease_fence: "account-a:lease:4".into(),
        causal_references: vec![format!("execution-plan:{id}")],
    }
}

fn facts(available: i64, risk_capacity: i64, observed_at: u64) -> CapitalFacts {
    CapitalFacts {
        destination: objective(1).destination,
        observed_available: Quantity::new(available, 0).unwrap(),
        account_watermark: kairos_primitives::Sequence::new(11),
        account_observed_at: UnixNanos::new(observed_at),
        account_complete: true,
        risk_capacity: Quantity::new(risk_capacity, 0).unwrap(),
        risk_policy_version: Generation::new(3),
        risk_watermark: kairos_primitives::Sequence::new(17),
    }
}

fn source_facts(available: i64, observed_at: u64) -> CapitalFacts {
    let mut source = facts(available, 100_000, observed_at);
    source.destination.segment = SegmentKey::new("funding").unwrap();
    source
}

fn route() -> CapitalTransferRoute {
    CapitalTransferRoute {
        route_id: CapitalRouteId::new("funding-to-usdm").unwrap(),
        version: Generation::new(1),
        source: source_facts(1, 1).destination,
        destination: objective(1).destination,
        kind: CapitalRouteKind::InternalTransfer,
        per_operation_limit: Quantity::new(30, 0).unwrap(),
        daily_limit: Quantity::new(60, 0).unwrap(),
        required_source_authority: "lease:account-a:7".into(),
        settlement_class: CapitalSettlementClass::ParticipantHistoryThenAccountObservation,
        enabled: true,
    }
}

fn configure_ready_planner(application: &mut CapitalApplication, group_id: &CapitalGroupId) {
    application
        .update_policy(UpdateCapitalPolicy {
            capital_group_id: group_id.clone(),
            policy: policy(),
        })
        .unwrap();
    application
        .publish_funding_objective(PublishFundingObjective {
            capital_group_id: group_id.clone(),
            objective: objective(1),
            observed_at: UnixNanos::new(100),
        })
        .unwrap();
    for facts in [facts(30, 70, 100), source_facts(35, 100)] {
        application
            .observe_facts(ObserveCapitalFacts {
                capital_group_id: group_id.clone(),
                facts,
            })
            .unwrap();
    }
    application
        .update_route(UpdateCapitalRoute {
            capital_group_id: group_id.clone(),
            route: route(),
        })
        .unwrap();
    application
        .evaluate(EvaluateCapitalGroup {
            evaluated_at: UnixNanos::new(120),
        })
        .unwrap();
}

#[test]
fn objective_is_versioned_and_idempotent_without_becoming_a_transfer_command() {
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    let mut application = compose_capital_application(group_config(group_id.as_str()));
    let command = PublishFundingObjective {
        capital_group_id: group_id,
        objective: objective(1),
        observed_at: UnixNanos::new(100),
    };

    assert!(matches!(
        application
            .publish_funding_objective(command.clone())
            .unwrap(),
        FundingObjectiveReceipt::Accepted(_)
    ));
    assert!(matches!(
        application.publish_funding_objective(command).unwrap(),
        FundingObjectiveReceipt::Duplicate(_)
    ));
    assert_eq!(application.snapshot().objectives.len(), 1);
}

#[test]
fn demands_are_deduplicated_netted_by_maximum_and_expire_without_direct_plans() {
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    let mut application = compose_capital_application(group_config(group_id.as_str()));
    application
        .update_policy(UpdateCapitalPolicy {
            capital_group_id: group_id.clone(),
            policy: policy(),
        })
        .unwrap();
    application
        .observe_facts(ObserveCapitalFacts {
            capital_group_id: group_id.clone(),
            facts: facts(20, 100, 100),
        })
        .unwrap();
    let first = ObserveCapitalDemand {
        capital_group_id: group_id.clone(),
        demand: demand("risk-1", 10, 200),
    };
    assert!(matches!(
        application.observe_demand(first.clone()).unwrap(),
        CapitalDemandReceipt::Accepted(_)
    ));
    assert!(matches!(
        application.observe_demand(first).unwrap(),
        CapitalDemandReceipt::Duplicate(_)
    ));
    application
        .observe_demand(ObserveCapitalDemand {
            capital_group_id: group_id.clone(),
            demand: demand("risk-2", 15, 200),
        })
        .unwrap();

    let view = application
        .evaluate(EvaluateCapitalGroup {
            evaluated_at: UnixNanos::new(150),
        })
        .unwrap()
        .pop()
        .unwrap();
    assert_eq!(view.active_demand_ids.len(), 2);
    assert_eq!(view.desired_target, Quantity::new(35, 0).unwrap());
    assert_eq!(view.deficit, Quantity::new(20, 0).unwrap());
    assert!(application.snapshot().plans.is_empty());

    assert_eq!(
        application
            .expire_demands(ExpireCapitalDemands {
                observed_at: UnixNanos::new(200),
            })
            .unwrap(),
        2
    );
    assert_eq!(
        application
            .demand(&CapitalDemandId::new("risk-1").unwrap())
            .unwrap()
            .status,
        CapitalDemandStatus::Expired
    );
}

#[test]
fn objective_version_cannot_move_backwards() {
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    let mut application = compose_capital_application(group_config(group_id.as_str()));
    application
        .publish_funding_objective(PublishFundingObjective {
            capital_group_id: group_id.clone(),
            objective: objective(2),
            observed_at: UnixNanos::new(100),
        })
        .unwrap();

    assert!(matches!(
        application.publish_funding_objective(PublishFundingObjective {
            capital_group_id: group_id,
            objective: objective(1),
            observed_at: UnixNanos::new(101),
        }),
        Err(CapitalError::Rejected(_))
    ));
}

#[test]
fn cancel_requires_the_observed_objective_version() {
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    let mut application = compose_capital_application(group_config(group_id.as_str()));
    application
        .publish_funding_objective(PublishFundingObjective {
            capital_group_id: group_id.clone(),
            objective: objective(3),
            observed_at: UnixNanos::new(100),
        })
        .unwrap();

    let receipt = application
        .cancel_funding_objective(CancelFundingObjective {
            capital_group_id: group_id,
            objective_id: FundingObjectiveId::new("usd-m-buffer").unwrap(),
            expected_version: Generation::new(3),
            observed_at: UnixNanos::new(150),
        })
        .unwrap();

    let FundingObjectiveReceipt::Cancelled(record) = receipt else {
        panic!("expected cancelled receipt")
    };
    assert_eq!(record.status, FundingObjectiveStatus::Cancelled);
}

#[test]
fn another_strategy_or_group_cannot_write_the_actor() {
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    let mut application = compose_capital_application(group_config(group_id.as_str()));
    let mut foreign = objective(1);
    foreign.strategy_id = StrategyId::new("momentum").unwrap();

    assert!(matches!(
        application.publish_funding_objective(PublishFundingObjective {
            capital_group_id: CapitalGroupId::new("capital-group-b").unwrap(),
            objective: foreign,
            observed_at: UnixNanos::new(100),
        }),
        Err(CapitalError::Rejected(_))
    ));
}

#[test]
fn group_id_is_not_enough_to_write_an_unconfigured_account_or_segment() {
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    let mut application = compose_capital_application(group_config(group_id.as_str()));
    let mut foreign_account = objective(1);
    foreign_account.destination.account_id = AccountId::new("other-account").unwrap();
    let mut foreign_segment = objective(2);
    foreign_segment.destination.segment = SegmentKey::new("coin-m").unwrap();

    for objective in [foreign_account, foreign_segment] {
        assert!(matches!(
            application.publish_funding_objective(PublishFundingObjective {
                capital_group_id: group_id.clone(),
                objective,
                observed_at: UnixNanos::new(100),
            }),
            Err(CapitalError::Rejected(_))
        ));
    }
}

#[test]
fn restart_recovers_objectives_and_unacknowledged_outbox_events() {
    let directory = tempfile::tempdir().unwrap();
    let state_path = directory.path().join("capital-state.json");
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    {
        let mut application = compose_persistent_capital_application(
            group_config(group_id.as_str()),
            state_path.clone(),
        )
        .unwrap();
        application
            .publish_funding_objective(PublishFundingObjective {
                capital_group_id: group_id.clone(),
                objective: objective(1),
                observed_at: UnixNanos::new(100),
            })
            .unwrap();
        assert!(application.pending_event().is_some());
    }

    let mut recovered =
        compose_persistent_capital_application(group_config(group_id.as_str()), state_path.clone())
            .unwrap();
    assert_eq!(recovered.snapshot().objectives.len(), 1);
    assert!(recovered.pending_event().is_some());
    recovered.acknowledge_event().unwrap();
    drop(recovered);

    let recovered =
        compose_persistent_capital_application(group_config(group_id.as_str()), state_path)
            .unwrap();
    assert!(recovered.pending_event().is_none());
    assert_eq!(recovered.snapshot().event_sequence.get(), 1);
}

#[test]
fn restart_rejects_a_state_file_owned_by_another_group() {
    let directory = tempfile::tempdir().unwrap();
    let state_path = directory.path().join("capital-state.json");
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    let mut application =
        compose_persistent_capital_application(group_config(group_id.as_str()), state_path.clone())
            .unwrap();
    application
        .publish_funding_objective(PublishFundingObjective {
            capital_group_id: group_id,
            objective: objective(1),
            observed_at: UnixNanos::new(100),
        })
        .unwrap();
    drop(application);

    let error = compose_persistent_capital_application(group_config("capital-group-b"), state_path)
        .err()
        .expect("foreign group state must be rejected");
    assert!(error.contains("identity"));
}

#[test]
fn restart_requires_the_same_membership_version() {
    let directory = tempfile::tempdir().unwrap();
    let state_path = directory.path().join("capital-state.json");
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    let mut application =
        compose_persistent_capital_application(group_config(group_id.as_str()), state_path.clone())
            .unwrap();
    application
        .publish_funding_objective(PublishFundingObjective {
            capital_group_id: group_id,
            objective: objective(1),
            observed_at: UnixNanos::new(100),
        })
        .unwrap();
    drop(application);

    let mut changed = group_config("capital-group-a");
    changed.membership_version = Generation::new(2);
    let error = compose_persistent_capital_application(changed, state_path)
        .err()
        .expect("membership changes require explicit migration/reconciliation");
    assert!(error.contains("identity"));
}

#[test]
fn journal_alone_recovers_a_command_committed_before_checkpoint_loss() {
    let directory = tempfile::tempdir().unwrap();
    let state_path = directory.path().join("capital-state.json");
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    let mut application =
        compose_persistent_capital_application(group_config(group_id.as_str()), state_path.clone())
            .unwrap();
    application
        .publish_funding_objective(PublishFundingObjective {
            capital_group_id: group_id.clone(),
            objective: objective(4),
            observed_at: UnixNanos::new(100),
        })
        .unwrap();
    drop(application);
    std::fs::remove_file(&state_path).unwrap();

    let recovered =
        compose_persistent_capital_application(group_config(group_id.as_str()), state_path)
            .unwrap();

    assert_eq!(
        recovered.snapshot().objectives[0].objective.version.get(),
        4
    );
    assert!(recovered.pending_event().is_some());
}

#[test]
fn effective_target_combines_objective_buffer_policy_and_risk_cap() {
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    let mut application = compose_capital_application(group_config(group_id.as_str()));
    application
        .update_policy(UpdateCapitalPolicy {
            capital_group_id: group_id.clone(),
            policy: policy(),
        })
        .unwrap();
    application
        .publish_funding_objective(PublishFundingObjective {
            capital_group_id: group_id.clone(),
            objective: objective(1),
            observed_at: UnixNanos::new(100),
        })
        .unwrap();
    application
        .observe_facts(ObserveCapitalFacts {
            capital_group_id: group_id,
            facts: facts(30, 70, 100),
        })
        .unwrap();

    let views = application
        .evaluate(EvaluateCapitalGroup {
            evaluated_at: UnixNanos::new(120),
        })
        .unwrap();

    assert_eq!(views.len(), 1);
    assert_eq!(views[0].desired_target, Quantity::new(80_000, 0).unwrap());
    assert_eq!(views[0].effective_target, Quantity::new(70, 0).unwrap());
    assert_eq!(views[0].deficit, Quantity::new(40, 0).unwrap());
    assert_eq!(views[0].readiness, CapitalReadiness::Ready);
    assert_eq!(views[0].active_objective_ids.len(), 1);
}

#[test]
fn deficit_requires_continuous_dwell_and_respects_hysteresis() {
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    let mut application = compose_capital_application(group_config(group_id.as_str()));
    let mut governed = policy();
    governed.deficit_dwell_nanos = 5;
    governed.hysteresis = Quantity::new(10, 0).unwrap();
    application
        .update_policy(UpdateCapitalPolicy {
            capital_group_id: group_id.clone(),
            policy: governed,
        })
        .unwrap();
    application
        .observe_facts(ObserveCapitalFacts {
            capital_group_id: group_id,
            facts: facts(4, 100, 100),
        })
        .unwrap();

    let first = application
        .evaluate(EvaluateCapitalGroup {
            evaluated_at: UnixNanos::new(120),
        })
        .unwrap()
        .pop()
        .unwrap();
    assert_eq!(first.deficit, Quantity::ZERO);
    assert_eq!(first.deficit_observed_since, Some(UnixNanos::new(120)));

    let mature = application
        .evaluate(EvaluateCapitalGroup {
            evaluated_at: UnixNanos::new(125),
        })
        .unwrap()
        .pop()
        .unwrap();
    assert_eq!(mature.deficit, Quantity::new(21, 0).unwrap());
}

#[test]
fn stale_or_incomplete_facts_never_authorize_a_funding_deficit() {
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    let mut application = compose_capital_application(group_config(group_id.as_str()));
    application
        .update_policy(UpdateCapitalPolicy {
            capital_group_id: group_id.clone(),
            policy: policy(),
        })
        .unwrap();
    application
        .observe_facts(ObserveCapitalFacts {
            capital_group_id: group_id,
            facts: facts(0, 100, 100),
        })
        .unwrap();

    let view = application
        .evaluate(EvaluateCapitalGroup {
            evaluated_at: UnixNanos::new(151),
        })
        .unwrap()
        .pop()
        .unwrap();

    assert_eq!(view.readiness, CapitalReadiness::Degraded);
    assert_eq!(view.deficit, Quantity::ZERO);
    assert_eq!(view.reason.as_deref(), Some("Account facts are stale"));
}

#[test]
fn target_state_and_source_watermarks_survive_restart() {
    let directory = tempfile::tempdir().unwrap();
    let state_path = directory.path().join("capital-state.json");
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    let location = objective(1).destination;
    {
        let mut application = compose_persistent_capital_application(
            group_config(group_id.as_str()),
            state_path.clone(),
        )
        .unwrap();
        application
            .update_policy(UpdateCapitalPolicy {
                capital_group_id: group_id.clone(),
                policy: policy(),
            })
            .unwrap();
        application
            .observe_facts(ObserveCapitalFacts {
                capital_group_id: group_id.clone(),
                facts: facts(4, 90, 100),
            })
            .unwrap();
        application
            .evaluate(EvaluateCapitalGroup {
                evaluated_at: UnixNanos::new(120),
            })
            .unwrap();
    }

    let recovered =
        compose_persistent_capital_application(group_config(group_id.as_str()), state_path)
            .unwrap();
    let view = recovered.availability(&location).unwrap();
    assert_eq!(view.account_watermark.get(), 11);
    assert_eq!(view.risk_watermark.get(), 17);
    assert_eq!(view.deficit, Quantity::new(21, 0).unwrap());
}

#[test]
fn planner_atomically_reserves_source_and_unfilled_destination_demand() {
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    let mut application = compose_capital_application(group_config(group_id.as_str()));
    application
        .update_policy(UpdateCapitalPolicy {
            capital_group_id: group_id.clone(),
            policy: policy(),
        })
        .unwrap();
    application
        .publish_funding_objective(PublishFundingObjective {
            capital_group_id: group_id.clone(),
            objective: objective(1),
            observed_at: UnixNanos::new(100),
        })
        .unwrap();
    for facts in [facts(30, 70, 100), source_facts(35, 100)] {
        application
            .observe_facts(ObserveCapitalFacts {
                capital_group_id: group_id.clone(),
                facts,
            })
            .unwrap();
    }
    application
        .update_route(UpdateCapitalRoute {
            capital_group_id: group_id.clone(),
            route: route(),
        })
        .unwrap();
    application
        .evaluate(EvaluateCapitalGroup {
            evaluated_at: UnixNanos::new(120),
        })
        .unwrap();

    let authorize = |plan_id: &str| AuthorizeCapitalPlan {
        capital_group_id: group_id.clone(),
        plan_id: CapitalPlanId::new(plan_id).unwrap(),
        rebalance_decision_id: format!("decision:{plan_id}"),
        route_id: route().route_id,
        source_authority: "lease:account-a:7".into(),
        created_at: UnixNanos::new(120),
        expires_at: UnixNanos::new(200),
    };
    let first = application.authorize_plan(authorize("plan-1")).unwrap();
    let duplicate = application.authorize_plan(authorize("plan-1")).unwrap();
    let second = application.authorize_plan(authorize("plan-2")).unwrap();

    assert_eq!(first.amount, Quantity::new(30, 0).unwrap());
    assert_eq!(duplicate, first);
    assert_eq!(second.amount, Quantity::new(5, 0).unwrap());
    assert_eq!(application.snapshot().reservations.len(), 2);
    assert!(matches!(
        application.authorize_plan(authorize("plan-3")),
        Err(CapitalError::Rejected(_))
    ));
}

#[test]
fn planner_rejects_wrong_source_authority_before_reserving() {
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    let mut application = compose_capital_application(group_config(group_id.as_str()));
    application
        .update_policy(UpdateCapitalPolicy {
            capital_group_id: group_id.clone(),
            policy: policy(),
        })
        .unwrap();
    for facts in [facts(0, 70, 100), source_facts(100, 100)] {
        application
            .observe_facts(ObserveCapitalFacts {
                capital_group_id: group_id.clone(),
                facts,
            })
            .unwrap();
    }
    application
        .update_route(UpdateCapitalRoute {
            capital_group_id: group_id.clone(),
            route: route(),
        })
        .unwrap();
    application
        .evaluate(EvaluateCapitalGroup {
            evaluated_at: UnixNanos::new(120),
        })
        .unwrap();

    assert!(matches!(
        application.authorize_plan(AuthorizeCapitalPlan {
            capital_group_id: group_id,
            plan_id: CapitalPlanId::new("plan-foreign-writer").unwrap(),
            rebalance_decision_id: "decision-foreign-writer".into(),
            route_id: route().route_id,
            source_authority: "stale-lease".into(),
            created_at: UnixNanos::new(120),
            expires_at: UnixNanos::new(200),
        }),
        Err(CapitalError::Rejected(_))
    ));
    assert!(application.snapshot().reservations.is_empty());
}

#[test]
fn indeterminate_transfer_reconciles_before_account_observed_completion() {
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    let mut application = compose_capital_application(group_config(group_id.as_str()));
    application
        .update_policy(UpdateCapitalPolicy {
            capital_group_id: group_id.clone(),
            policy: policy(),
        })
        .unwrap();
    application
        .publish_funding_objective(PublishFundingObjective {
            capital_group_id: group_id.clone(),
            objective: objective(1),
            observed_at: UnixNanos::new(100),
        })
        .unwrap();
    for facts in [facts(30, 70, 100), source_facts(35, 100)] {
        application
            .observe_facts(ObserveCapitalFacts {
                capital_group_id: group_id.clone(),
                facts,
            })
            .unwrap();
    }
    application
        .update_route(UpdateCapitalRoute {
            capital_group_id: group_id.clone(),
            route: route(),
        })
        .unwrap();
    application
        .evaluate(EvaluateCapitalGroup {
            evaluated_at: UnixNanos::new(120),
        })
        .unwrap();
    let plan_id = CapitalPlanId::new("plan-reconcile").unwrap();
    application
        .authorize_plan(AuthorizeCapitalPlan {
            capital_group_id: group_id.clone(),
            plan_id: plan_id.clone(),
            rebalance_decision_id: "decision-reconcile".into(),
            route_id: route().route_id,
            source_authority: "lease:account-a:7".into(),
            created_at: UnixNanos::new(120),
            expires_at: UnixNanos::new(300),
        })
        .unwrap();
    let prepared = application
        .begin_operation(BeginCapitalOperation {
            capital_group_id: group_id.clone(),
            plan_id: plan_id.clone(),
            at: UnixNanos::new(121),
        })
        .unwrap();
    assert_eq!(prepared.status, CapitalOperationStatus::Prepared);
    application
        .mark_delivery_started(MarkCapitalDeliveryStarted {
            capital_group_id: group_id.clone(),
            plan_id: plan_id.clone(),
            at: UnixNanos::new(121),
        })
        .unwrap();

    let indeterminate = application
        .record_submission(RecordCapitalSubmission {
            capital_group_id: group_id.clone(),
            plan_id: plan_id.clone(),
            outcome: CapitalSubmissionOutcome::Indeterminate,
            participant_operation_id: None,
            failure_reason: Some("request timed out".into()),
            at: UnixNanos::new(122),
        })
        .unwrap();
    assert_eq!(indeterminate.status, CapitalPlanStatus::Indeterminate);
    let same_operation = application
        .begin_operation(BeginCapitalOperation {
            capital_group_id: group_id.clone(),
            plan_id: plan_id.clone(),
            at: UnixNanos::new(123),
        })
        .unwrap();
    assert_eq!(same_operation.idempotency_key, prepared.idempotency_key);
    assert_eq!(same_operation.attempt_count, 1);

    let reconciling = application
        .record_participant_status(RecordCapitalParticipantStatus {
            capital_group_id: group_id.clone(),
            plan_id: plan_id.clone(),
            state: CapitalParticipantOperationState::Succeeded,
            participant_operation_id: Some("transfer-7".into()),
            participant_state: Some("SUCCESS".into()),
            failure_reason: None,
            at: UnixNanos::new(130),
        })
        .unwrap();
    assert_eq!(reconciling.status, CapitalPlanStatus::Reconciling);

    let mut source = source_facts(5, 131);
    source.account_watermark = kairos_primitives::Sequence::new(12);
    let destination_not_yet_observed = facts(30, 70, 131);
    let still_reconciling = application
        .observe_settlement(ObserveCapitalSettlement {
            capital_group_id: group_id.clone(),
            plan_id: plan_id.clone(),
            source: source.clone(),
            destination: destination_not_yet_observed,
            observed_at: UnixNanos::new(131),
        })
        .unwrap();
    assert_eq!(still_reconciling.status, CapitalPlanStatus::Reconciling);

    let mut destination = facts(60, 70, 132);
    destination.account_watermark = kairos_primitives::Sequence::new(12);
    let completed = application
        .observe_settlement(ObserveCapitalSettlement {
            capital_group_id: group_id,
            plan_id,
            source,
            destination,
            observed_at: UnixNanos::new(132),
        })
        .unwrap();
    assert_eq!(completed.status, CapitalPlanStatus::Completed);
    let snapshot = application.snapshot();
    assert_eq!(
        snapshot.operations[0].status,
        CapitalOperationStatus::Settled
    );
    assert_eq!(
        snapshot.reservations[0].status,
        CapitalReservationStatus::Consumed
    );
}

#[test]
fn restart_preserves_indeterminate_operation_and_original_idempotency_key() {
    let directory = tempfile::tempdir().unwrap();
    let state_path = directory.path().join("capital-state.json");
    let group_id = CapitalGroupId::new("capital-group-a").unwrap();
    let plan_id = CapitalPlanId::new("plan-indeterminate-restart").unwrap();
    let original_key;
    {
        let mut application = compose_persistent_capital_application(
            group_config(group_id.as_str()),
            state_path.clone(),
        )
        .unwrap();
        configure_ready_planner(&mut application, &group_id);
        application
            .authorize_plan(AuthorizeCapitalPlan {
                capital_group_id: group_id.clone(),
                plan_id: plan_id.clone(),
                rebalance_decision_id: "decision-indeterminate-restart".into(),
                route_id: route().route_id,
                source_authority: "lease:account-a:7".into(),
                created_at: UnixNanos::new(120),
                expires_at: UnixNanos::new(300),
            })
            .unwrap();
        original_key = application
            .begin_operation(BeginCapitalOperation {
                capital_group_id: group_id.clone(),
                plan_id: plan_id.clone(),
                at: UnixNanos::new(121),
            })
            .unwrap()
            .idempotency_key;
        application
            .mark_delivery_started(MarkCapitalDeliveryStarted {
                capital_group_id: group_id.clone(),
                plan_id: plan_id.clone(),
                at: UnixNanos::new(121),
            })
            .unwrap();
        application
            .record_submission(RecordCapitalSubmission {
                capital_group_id: group_id.clone(),
                plan_id: plan_id.clone(),
                outcome: CapitalSubmissionOutcome::Indeterminate,
                participant_operation_id: None,
                failure_reason: Some("timeout".into()),
                at: UnixNanos::new(122),
            })
            .unwrap();
    }

    let mut recovered =
        compose_persistent_capital_application(group_config(group_id.as_str()), state_path)
            .unwrap();
    let snapshot = recovered.snapshot();
    assert_eq!(snapshot.plans[0].status, CapitalPlanStatus::Indeterminate);
    assert_eq!(
        snapshot.reservations[0].status,
        CapitalReservationStatus::Active
    );
    assert_eq!(
        snapshot.operations[0].status,
        CapitalOperationStatus::Indeterminate
    );
    let operation = recovered
        .begin_operation(BeginCapitalOperation {
            capital_group_id: group_id,
            plan_id,
            at: UnixNanos::new(200),
        })
        .unwrap();
    assert_eq!(operation.idempotency_key, original_key);
    assert_eq!(operation.attempt_count, 1);
}

#[test]
fn restart_after_delivery_fence_never_returns_a_prepared_operation() {
    let directory = tempfile::tempdir().unwrap();
    let state_path = directory.path().join("capital-delivery-fence.json");
    let group_id = CapitalGroupId::new("capital-group-delivery-fence").unwrap();
    let plan_id = CapitalPlanId::new("plan-delivery-fence").unwrap();
    {
        let mut application = compose_persistent_capital_application(
            group_config(group_id.as_str()),
            state_path.clone(),
        )
        .unwrap();
        configure_ready_planner(&mut application, &group_id);
        application
            .authorize_plan(AuthorizeCapitalPlan {
                capital_group_id: group_id.clone(),
                plan_id: plan_id.clone(),
                rebalance_decision_id: "decision-delivery-fence".into(),
                route_id: route().route_id,
                source_authority: "lease:account-a:7".into(),
                created_at: UnixNanos::new(120),
                expires_at: UnixNanos::new(300),
            })
            .unwrap();
        application
            .begin_operation(BeginCapitalOperation {
                capital_group_id: group_id.clone(),
                plan_id: plan_id.clone(),
                at: UnixNanos::new(121),
            })
            .unwrap();
        let fenced = application
            .mark_delivery_started(MarkCapitalDeliveryStarted {
                capital_group_id: group_id.clone(),
                plan_id: plan_id.clone(),
                at: UnixNanos::new(122),
            })
            .unwrap();
        assert_eq!(fenced.status, CapitalOperationStatus::Dispatching);
    }

    let mut recovered =
        compose_persistent_capital_application(group_config(group_id.as_str()), state_path)
            .unwrap();
    let operation = recovered
        .begin_operation(BeginCapitalOperation {
            capital_group_id: group_id,
            plan_id,
            at: UnixNanos::new(200),
        })
        .unwrap();
    assert_eq!(operation.status, CapitalOperationStatus::Dispatching);
    assert_eq!(operation.dispatch_started_at, Some(UnixNanos::new(122)));
    assert_eq!(operation.attempt_count, 1);
}

#[derive(Clone)]
struct ConfirmedTransferConnection {
    submissions: std::sync::Arc<std::sync::atomic::AtomicUsize>,
}

impl AssetTransferCommand for ConfirmedTransferConnection {
    async fn submit_transfer(
        &mut self,
        _request: &AssetTransferRequest,
    ) -> Result<CommandOutcome<AssetTransferSubmission>, IntegrationError> {
        self.submissions
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        Ok(CommandOutcome::Confirmed(AssetTransferSubmission {
            participant_transfer_id: Some("transfer-1".into()),
            acknowledged_at_unix_nanos: None,
        }))
    }
}

impl AssetTransferStatusQuery for ConfirmedTransferConnection {
    async fn transfer_status(
        &mut self,
        query: &AssetTransferQuery,
    ) -> Result<Option<AssetTransferStatus>, IntegrationError> {
        Ok(Some(AssetTransferStatus {
            idempotency_key: query.request.idempotency_key.clone(),
            participant_transfer_id: query.participant_transfer_id.clone(),
            source: query.request.source.clone(),
            destination: query.request.destination.clone(),
            asset: query.request.asset.clone(),
            requested_amount: query.request.amount,
            settled_amount: Some(query.request.amount),
            state: AssetTransferState::Succeeded,
            participant_state: Some("CONFIRMED".into()),
            updated_at_unix_nanos: Some(UnixNanos::new(130)),
            failure_reason: None,
        }))
    }
}

#[tokio::test]
async fn transfer_process_submits_once_and_reconciles_repeated_calls() {
    let group_id = CapitalGroupId::new("capital-group-process").unwrap();
    let plan_id = CapitalPlanId::new("plan-process").unwrap();
    let submissions = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let connection = ConfirmedTransferConnection {
        submissions: submissions.clone(),
    };
    let mut process =
        compose_capital_transfer_process(group_config(group_id.as_str()), connection).unwrap();
    configure_ready_planner(process.application_mut(), &group_id);
    process
        .application_mut()
        .authorize_plan(AuthorizeCapitalPlan {
            capital_group_id: group_id,
            plan_id: plan_id.clone(),
            rebalance_decision_id: "decision-process".into(),
            route_id: route().route_id,
            source_authority: "lease:account-a:7".into(),
            created_at: UnixNanos::new(120),
            expires_at: UnixNanos::new(300),
        })
        .unwrap();

    let submitted = process
        .execute_transfer(plan_id.clone(), UnixNanos::new(121))
        .await
        .unwrap();
    assert_eq!(submitted.status, CapitalPlanStatus::AwaitingTransfer);
    assert_eq!(submissions.load(std::sync::atomic::Ordering::SeqCst), 1);

    let reconciled = process
        .execute_transfer(plan_id, UnixNanos::new(130))
        .await
        .unwrap();
    assert_eq!(reconciled.status, CapitalPlanStatus::Reconciling);
    assert_eq!(submissions.load(std::sync::atomic::Ordering::SeqCst), 1);
}

#[test]
fn expiry_releases_only_operations_that_never_crossed_the_delivery_fence() {
    let group_id = CapitalGroupId::new("capital-group-expiry").unwrap();
    let safe_plan_id = CapitalPlanId::new("plan-safe-expiry").unwrap();
    let uncertain_plan_id = CapitalPlanId::new("plan-uncertain-expiry").unwrap();
    let mut application = compose_capital_application(group_config(group_id.as_str()));
    configure_ready_planner(&mut application, &group_id);

    application
        .authorize_plan(AuthorizeCapitalPlan {
            capital_group_id: group_id.clone(),
            plan_id: safe_plan_id.clone(),
            rebalance_decision_id: "decision-safe-expiry".into(),
            route_id: route().route_id.clone(),
            source_authority: "lease:account-a:7".into(),
            created_at: UnixNanos::new(120),
            expires_at: UnixNanos::new(125),
        })
        .unwrap();
    assert_eq!(
        application
            .expire_plans(ExpireCapitalPlans {
                observed_at: UnixNanos::new(126),
            })
            .unwrap(),
        1
    );
    assert_eq!(
        application.plan(&safe_plan_id).unwrap().status,
        CapitalPlanStatus::Expired
    );

    application
        .authorize_plan(AuthorizeCapitalPlan {
            capital_group_id: group_id.clone(),
            plan_id: uncertain_plan_id.clone(),
            rebalance_decision_id: "decision-uncertain-expiry".into(),
            route_id: route().route_id,
            source_authority: "lease:account-a:7".into(),
            created_at: UnixNanos::new(127),
            expires_at: UnixNanos::new(130),
        })
        .unwrap();
    application
        .begin_operation(BeginCapitalOperation {
            capital_group_id: group_id.clone(),
            plan_id: uncertain_plan_id.clone(),
            at: UnixNanos::new(128),
        })
        .unwrap();
    application
        .mark_delivery_started(MarkCapitalDeliveryStarted {
            capital_group_id: group_id,
            plan_id: uncertain_plan_id.clone(),
            at: UnixNanos::new(129),
        })
        .unwrap();

    assert_eq!(
        application
            .expire_plans(ExpireCapitalPlans {
                observed_at: UnixNanos::new(131),
            })
            .unwrap(),
        0
    );
    let snapshot = application.snapshot();
    assert_eq!(
        application.plan(&uncertain_plan_id).unwrap().status,
        CapitalPlanStatus::Transferring
    );
    assert_eq!(
        snapshot
            .reservations
            .iter()
            .find(|reservation| reservation.plan_id == uncertain_plan_id)
            .unwrap()
            .status,
        CapitalReservationStatus::Active
    );
}
