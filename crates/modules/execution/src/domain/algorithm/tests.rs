use kairos_primitives::decimal::{Quantity, Ratio};
use kairos_primitives::execution::{ExecutionRouteId, IntentId, LegId, OrderId};
use kairos_primitives::time::{DurationNanos, UnixNanos};

use super::*;

fn run() -> AlgorithmRun {
    AlgorithmRun::immediate(
        IntentId::new("intent:immediate").unwrap(),
        [(
            LegId::new("leg:primary").unwrap(),
            Quantity::new(5, 0).unwrap(),
        )],
    )
    .unwrap()
}

fn input(business_time: u64) -> AlgorithmInput {
    AlgorithmInput {
        business_time: UnixNanos::new(business_time),
        ready_children: vec![AlgorithmChildCandidate {
            order_id: kairos_primitives::execution::OrderId::new("order:primary").unwrap(),
            leg_id: LegId::new("leg:primary").unwrap(),
            quantity: Quantity::new(5, 0).unwrap(),
            execution_style: AlgorithmExecutionStyle::Immediate,
            execution_route_id: None,
        }],
    }
}

#[test]
fn immediate_decision_is_deterministic_and_action_identity_is_stable() {
    let original = run();
    let input = input(100);
    let left = decide_immediate(&original, input.clone()).unwrap();
    let right = decide_immediate(&original, input).unwrap();
    assert_eq!(left, right);

    let mut first = original.clone();
    first.apply_decision(left).unwrap();
    let mut second = original;
    second.apply_decision(right).unwrap();
    assert_eq!(first, second);
    assert_eq!(
        first.pending_actions().next().unwrap().action_id,
        "intent:immediate:algorithm:1:decision:1:action:1"
    );
}

#[test]
fn pending_action_prevents_a_duplicate_submit_decision() {
    let mut run = run();
    let first = decide_immediate(&run, input(100)).unwrap();
    run.apply_decision(first).unwrap();

    let second = decide_immediate(&run, input(101)).unwrap();
    assert!(second.actions.is_empty());
    assert_eq!(second.next_status, AlgorithmRunStatus::Waiting);
}

#[test]
fn pending_action_survives_serialization_without_changing_identity() {
    let mut original = run();
    let decision = decide_immediate(&original, input(100)).unwrap();
    original.apply_decision(decision).unwrap();

    let encoded = serde_json::to_vec(&original).unwrap();
    let restored: AlgorithmRun = serde_json::from_slice(&encoded).unwrap();

    assert_eq!(restored, original);
    assert_eq!(
        restored.pending_actions().next().unwrap().action_id,
        "intent:immediate:algorithm:1:decision:1:action:1"
    );
}

#[test]
fn stale_sequence_and_regressing_business_time_are_rejected() {
    let mut run = run();
    let decision = decide_immediate(&run, input(100)).unwrap();
    run.apply_decision(decision.clone()).unwrap();
    assert!(run.apply_decision(decision).is_err());
    assert!(decide_immediate(&run, input(99)).is_err());
}

#[test]
fn quantity_ledger_rejects_overcommitment() {
    let mut run = run();
    run.legs[0].committed_quantity = Quantity::new(4, 0).unwrap();
    run.legs[0].filled_quantity = Quantity::new(2, 0).unwrap();
    assert!(run.validate().is_err());
}

fn maker_taker_run() -> AlgorithmRun {
    AlgorithmRun::maker_taker_hedge(
        IntentId::new("intent:maker-taker").unwrap(),
        MakerTakerHedgeSpec {
            leader_leg_id: LegId::new("leg:leader").unwrap(),
            hedge_leg_id: LegId::new("leg:hedge").unwrap(),
            hedge_ratio: Ratio::new(1, 1).unwrap(),
            contract_multiplier: Ratio::new(1, 1).unwrap(),
            max_unhedged_quantity: Quantity::new(1, 0).unwrap(),
            max_unhedged_duration: None,
            fallback_execution_route_ids: Vec::new(),
        },
        Quantity::new(5, 0).unwrap(),
        Quantity::new(5, 0).unwrap(),
    )
    .unwrap()
}

#[test]
fn maker_taker_tail_exposure_waits_for_business_deadline_then_hedges() {
    let mut run = maker_taker_run();
    let ExecutionAlgorithmSpec::MakerTakerHedge(spec) = &mut run.spec else {
        panic!("maker-taker fixture changed algorithm type");
    };
    spec.max_unhedged_duration = Some(DurationNanos::new(10));
    run.legs[0].filled_quantity = Quantity::new(1, 0).unwrap();
    run.synchronize_maker_taker_exposure(Quantity::ZERO, Quantity::ZERO, Some(UnixNanos::new(100)))
        .unwrap();

    let waiting = decide_maker_taker_hedge(
        &run,
        AlgorithmInput {
            business_time: UnixNanos::new(109),
            ready_children: Vec::new(),
        },
    )
    .unwrap();
    assert_eq!(waiting.next_status, AlgorithmRunStatus::Waiting);
    assert_eq!(waiting.next_wake_at, Some(UnixNanos::new(110)));
    assert!(waiting.actions.is_empty());

    let due = decide_maker_taker_hedge(
        &run,
        AlgorithmInput {
            business_time: UnixNanos::new(110),
            ready_children: vec![candidate(
                "order:tail-hedge",
                "leg:hedge",
                1,
                AlgorithmExecutionStyle::TakerImmediate,
            )],
        },
    )
    .unwrap();
    assert!(matches!(
        due.actions.as_slice(),
        [AlgorithmActionKind::SubmitChild {
            quantity,
            execution_style: AlgorithmExecutionStyle::TakerImmediate,
            ..
        }] if *quantity == Quantity::new(1, 0).unwrap()
    ));
}

fn candidate(
    order_id: &str,
    leg_id: &str,
    quantity: i64,
    execution_style: AlgorithmExecutionStyle,
) -> AlgorithmChildCandidate {
    AlgorithmChildCandidate {
        order_id: OrderId::new(order_id).unwrap(),
        leg_id: LegId::new(leg_id).unwrap(),
        quantity: Quantity::new(quantity, 0).unwrap(),
        execution_style,
        execution_route_id: None,
    }
}

#[test]
fn maker_taker_starts_with_maker_and_keeps_hedge_dormant() {
    let run = maker_taker_run();
    assert_eq!(run.legs[0].role, AlgorithmLegRole::LeaderMaker);
    assert_eq!(run.legs[1].role, AlgorithmLegRole::HedgeTaker);
    assert_eq!(run.legs[1].lifecycle, AlgorithmLegLifecycle::Dormant);

    let decision = decide_maker_taker_hedge(
        &run,
        AlgorithmInput {
            business_time: UnixNanos::new(100),
            ready_children: vec![candidate(
                "order:leader:1",
                "leg:leader",
                5,
                AlgorithmExecutionStyle::MakerPostOnly,
            )],
        },
    )
    .unwrap();
    assert!(matches!(
        &decision.actions[0],
        AlgorithmActionKind::SubmitChild {
            order_id,
            execution_style: AlgorithmExecutionStyle::MakerPostOnly,
            ..
        } if order_id.as_str() == "order:leader:1"
    ));
}

#[test]
fn maker_fill_above_threshold_prioritizes_exact_taker_hedge() {
    let mut run = maker_taker_run();
    let maker = decide_maker_taker_hedge(
        &run,
        AlgorithmInput {
            business_time: UnixNanos::new(100),
            ready_children: vec![candidate(
                "order:leader:1",
                "leg:leader",
                5,
                AlgorithmExecutionStyle::MakerPostOnly,
            )],
        },
    )
    .unwrap();
    run.apply_decision(maker).unwrap();
    let maker_action = run.actions[0].action_id.clone();
    run.set_action_status(&maker_action, AlgorithmActionStatus::Completed)
        .unwrap();
    run.legs[0].filled_quantity = Quantity::new(3, 0).unwrap();
    run.legs[0].committed_quantity = Quantity::new(2, 0).unwrap();
    run.legs[0].lifecycle = AlgorithmLegLifecycle::Active;
    run.synchronize_maker_taker_exposure(Quantity::ZERO, Quantity::ZERO, None)
        .unwrap();

    let input = AlgorithmInput {
        business_time: UnixNanos::new(101),
        ready_children: vec![
            candidate(
                "order:leader:2",
                "leg:leader",
                2,
                AlgorithmExecutionStyle::MakerPostOnly,
            ),
            candidate(
                "order:hedge:1",
                "leg:hedge",
                5,
                AlgorithmExecutionStyle::TakerImmediate,
            ),
        ],
    };
    let left = decide_maker_taker_hedge(&run, input.clone()).unwrap();
    let right = decide_maker_taker_hedge(&run, input).unwrap();
    assert_eq!(left, right);
    assert!(matches!(
        &left.actions[0],
        AlgorithmActionKind::SubmitChild {
            order_id,
            quantity,
            execution_style: AlgorithmExecutionStyle::TakerImmediate,
            ..
        } if order_id.as_str() == "order:hedge:1" && *quantity == Quantity::new(3, 0).unwrap()
    ));
}

#[test]
fn in_flight_hedge_commitment_prevents_duplicate_hedge() {
    let mut run = maker_taker_run();
    run.legs[0].filled_quantity = Quantity::new(3, 0).unwrap();
    run.legs[0].committed_quantity = Quantity::new(2, 0).unwrap();
    run.legs[0].lifecycle = AlgorithmLegLifecycle::Active;
    run.legs[1].committed_quantity = Quantity::new(3, 0).unwrap();
    run.legs[1].lifecycle = AlgorithmLegLifecycle::Active;
    run.synchronize_maker_taker_exposure(Quantity::ZERO, Quantity::ZERO, None)
        .unwrap();
    assert_eq!(
        run.exposure.as_ref().unwrap().unhedged_after_commitment,
        Quantity::ZERO
    );

    let decision = decide_maker_taker_hedge(
        &run,
        AlgorithmInput {
            business_time: UnixNanos::new(101),
            ready_children: vec![candidate(
                "order:hedge:2",
                "leg:hedge",
                3,
                AlgorithmExecutionStyle::TakerImmediate,
            )],
        },
    )
    .unwrap();
    assert!(decision.actions.is_empty());
    assert_eq!(decision.next_status, AlgorithmRunStatus::Waiting);
}

#[test]
fn maker_taker_completes_only_after_leader_and_exposure_are_settled() {
    let mut run = maker_taker_run();
    run.legs[0].filled_quantity = Quantity::new(5, 0).unwrap();
    run.legs[0].lifecycle = AlgorithmLegLifecycle::Completed;
    run.legs[1].filled_quantity = Quantity::new(5, 0).unwrap();
    run.synchronize_maker_taker_exposure(Quantity::ZERO, Quantity::ZERO, None)
        .unwrap();

    let decision = decide_maker_taker_hedge(
        &run,
        AlgorithmInput {
            business_time: UnixNanos::new(102),
            ready_children: Vec::new(),
        },
    )
    .unwrap();
    assert_eq!(decision.next_status, AlgorithmRunStatus::Completed);
    assert_eq!(decision.actions, vec![AlgorithmActionKind::Complete]);
}

#[test]
fn proven_failed_hedge_prefers_a_ready_fallback_taker_over_unwind() {
    let mut run = maker_taker_run();
    run.legs[0].filled_quantity = Quantity::new(3, 0).unwrap();
    run.legs[0].committed_quantity = Quantity::new(2, 0).unwrap();
    run.legs[0].lifecycle = AlgorithmLegLifecycle::Active;
    run.legs[1].lifecycle = AlgorithmLegLifecycle::Failed;
    run.synchronize_maker_taker_exposure(Quantity::ZERO, Quantity::ZERO, None)
        .unwrap();
    let fallback_route = ExecutionRouteId::new("execution-route:fallback").unwrap();

    let decision = decide_maker_taker_hedge(
        &run,
        AlgorithmInput {
            business_time: UnixNanos::new(103),
            ready_children: vec![AlgorithmChildCandidate {
                order_id: OrderId::new("order:fallback:1").unwrap(),
                leg_id: LegId::new("leg:hedge").unwrap(),
                quantity: Quantity::new(3, 0).unwrap(),
                execution_style: AlgorithmExecutionStyle::TakerImmediate,
                execution_route_id: Some(fallback_route.clone()),
            }],
        },
    )
    .unwrap();

    assert!(matches!(
        &decision.actions[0],
        AlgorithmActionKind::SubmitChild {
            order_id,
            quantity,
            execution_style: AlgorithmExecutionStyle::TakerImmediate,
            execution_route_id: Some(route_id),
            ..
        } if order_id.as_str() == "order:fallback:1"
            && *quantity == Quantity::new(3, 0).unwrap()
            && route_id == &fallback_route
    ));
}

#[test]
fn proven_failed_hedge_selects_immediate_leader_unwind() {
    let mut run = maker_taker_run();
    run.legs[0].filled_quantity = Quantity::new(3, 0).unwrap();
    run.legs[0].committed_quantity = Quantity::new(2, 0).unwrap();
    run.legs[0].lifecycle = AlgorithmLegLifecycle::Active;
    run.legs[1].lifecycle = AlgorithmLegLifecycle::Failed;
    run.synchronize_maker_taker_exposure(Quantity::ZERO, Quantity::ZERO, None)
        .unwrap();

    let decision = decide_maker_taker_hedge(
        &run,
        AlgorithmInput {
            business_time: UnixNanos::new(103),
            ready_children: vec![candidate(
                "order:unwind:1",
                "leg:leader",
                3,
                AlgorithmExecutionStyle::UnwindImmediate,
            )],
        },
    )
    .unwrap();
    assert!(matches!(
        &decision.actions[0],
        AlgorithmActionKind::SubmitChild {
            order_id,
            quantity,
            execution_style: AlgorithmExecutionStyle::UnwindImmediate,
            ..
        } if order_id.as_str() == "order:unwind:1" && *quantity == Quantity::new(3, 0).unwrap()
    ));

    run.apply_decision(decision).unwrap();
    let action_id = run.actions.last().unwrap().action_id.clone();
    run.set_action_status(&action_id, AlgorithmActionStatus::Completed)
        .unwrap();
    run.synchronize_maker_taker_exposure(Quantity::ZERO, Quantity::new(3, 0).unwrap(), None)
        .unwrap();
    assert_eq!(
        run.exposure.as_ref().unwrap().unhedged_after_commitment,
        Quantity::ZERO
    );
    assert_eq!(
        run.exposure.as_ref().unwrap().net_leader_filled_quantity,
        Quantity::new(3, 0).unwrap()
    );
    run.synchronize_maker_taker_exposure(Quantity::new(3, 0).unwrap(), Quantity::ZERO, None)
        .unwrap();
    assert_eq!(
        run.exposure.as_ref().unwrap().net_leader_filled_quantity,
        Quantity::ZERO
    );
    assert_eq!(
        run.exposure.as_ref().unwrap().required_hedge_quantity,
        Quantity::ZERO
    );
}
