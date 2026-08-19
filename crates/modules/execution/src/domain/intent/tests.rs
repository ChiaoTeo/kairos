// Intent-domain tests live outside the production module boundary.

mod tests {
    use super::super::*;

    #[test]
    fn rejects_invalid_state_transition() {
        assert!(!IntentLifecycle::Accepted.can_transition_to(IntentLifecycle::Satisfied));
        assert!(IntentLifecycle::Accepted.can_transition_to(IntentLifecycle::Planning));
    }

    #[test]
    fn plan_requires_unique_legs() {
        let first = ExecutionLeg::new(
            "leg",
            "account",
            "spot",
            "BTCUSDT",
            OrderSide::Buy,
            Quantity::new(1, 0).unwrap(),
        )
        .unwrap();
        let second = first.clone();
        assert!(
            ExecutionPlan::new(
                "plan",
                "intent",
                IntentType::PairArbitrage,
                vec![first, second],
                CompletionPolicy::AllLegsSatisfied,
                FailurePolicy::CancelRemaining,
            )
            .is_err()
        );
    }

    #[test]
    fn plan_classifies_orders_by_progress() {
        let leg = ExecutionLeg::new(
            "leg",
            "account",
            "spot",
            "BTCUSDT",
            OrderSide::Buy,
            Quantity::new(1, 0).unwrap(),
        )
        .unwrap();
        let plan = ExecutionPlan::new(
            "plan",
            "intent",
            IntentType::SingleOrder,
            vec![leg],
            CompletionPolicy::AllLegsSatisfied,
            FailurePolicy::CancelRemaining,
        )
        .unwrap();
        assert_eq!(
            plan.lifecycle([ExecutionOrderStatus::Accepted]),
            IntentLifecycle::Executing
        );
        assert_eq!(
            plan.lifecycle([ExecutionOrderStatus::PartiallyFilled]),
            IntentLifecycle::PartiallyFilled
        );
        assert_eq!(
            plan.lifecycle([ExecutionOrderStatus::Filled]),
            IntentLifecycle::Satisfied
        );
    }

    #[test]
    fn split_quantity_preserves_total_and_is_deterministic() {
        let chunks = split_quantity(
            Quantity::new(10, 0).unwrap(),
            &SplitOrderPolicy {
                max_child_quantity: None,
                child_count: Some(3),
                min_child_quantity: Some(Quantity::new(3, 0).unwrap()),
                interval: Some(DurationNanos::new(100_000_000)),
            },
        )
        .unwrap();
        assert_eq!(
            chunks,
            vec![
                Quantity::new(4, 0).unwrap(),
                Quantity::new(3, 0).unwrap(),
                Quantity::new(3, 0).unwrap()
            ]
        );
        assert_eq!(chunks.iter().map(|value| value.mantissa()).sum::<i64>(), 10);
    }

    #[test]
    fn split_quantity_can_increase_precision_after_canonicalization() {
        let chunks = split_quantity(
            Quantity::new(1, 0).unwrap(),
            &SplitOrderPolicy {
                max_child_quantity: None,
                child_count: Some(2),
                min_child_quantity: None,
                interval: None,
            },
        )
        .unwrap();

        assert_eq!(
            chunks,
            vec![Quantity::new(5, 1).unwrap(), Quantity::new(5, 1).unwrap()]
        );
    }

    #[test]
    fn hedge_requirement_uses_actual_leader_fills() {
        let policy = HedgePolicy {
            leader_leg_id: LegId::new("leader").unwrap(),
            hedge_leg_id: LegId::new("hedge").unwrap(),
            ratio: Ratio::new(2, 1).unwrap(),
            contract_multiplier: Ratio::new(1, 1).unwrap(),
            max_unhedged_quantity: Quantity::new(1, 0).unwrap(),
            compensate_on_failure: true,
            max_compensation_attempts: 3,
        };
        assert_eq!(
            policy
                .required_hedge_quantity(Quantity::new(3, 0).unwrap(), Quantity::new(4, 0).unwrap())
                .unwrap(),
            Quantity::new(2, 0).unwrap()
        );
        assert_eq!(
            policy
                .required_hedge_quantity(Quantity::new(3, 0).unwrap(), Quantity::new(6, 0).unwrap())
                .unwrap(),
            Quantity::ZERO
        );
    }
}
