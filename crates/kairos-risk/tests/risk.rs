use kairos_protocol::generated::kairos::risk::v_1::{
    reservation_event_buffer_has_identifier, root_as_reservation_event,
};
use kairos_protocol::generated::kairos::risk::v_1::{
    risk_snapshot_buffer_has_identifier, root_as_risk_snapshot,
};
use kairos_risk::composition::{
    FlatbuffersRiskEventWriter, FlatbuffersRiskSnapshotWriter, MemoryRiskStore,
};
use kairos_risk::{
    Amount, AssessRisk, Budget, BudgetRef, ConsumeReservation, Metric, ReleaseReservation,
    ReservationStatus, ReserveRisk, RiskApplication, Usage,
};
fn amount(value: i64) -> Amount {
    Amount::new(value, 0).unwrap()
}

fn application(limit: i64) -> RiskApplication {
    RiskApplication::with_dependencies(
        "risk",
        vec![Budget {
            budget_id: "account-notional".into(),
            owner_id: "account".into(),
            reference: BudgetRef {
                scope: "account".into(),
                subject: "main".into(),
            },
            metric: Metric::Notional,
            limit: amount(limit),
            used: Amount::ZERO,
            reserved: Amount::ZERO,
            valid_from_unix_nanos: None,
            valid_until_unix_nanos: None,
        }],
        false,
        None,
    )
    .unwrap()
}

fn assess(id: &str, value: i64) -> AssessRisk {
    AssessRisk {
        request_id: id.into(),
        usages: vec![Usage {
            metric: Metric::Notional,
            amount: amount(value),
            budgets: vec![BudgetRef {
                scope: "account".into(),
                subject: "main".into(),
            }],
        }],
        at_unix_nanos: 1,
    }
}

#[test]
fn rejection_does_not_mutate_budget() {
    let app = application(100);
    let result = app.assess(assess("request", 101)).unwrap();
    assert!(!result.allowed);
    assert_eq!(app.snapshot().budgets[0].available(), amount(100));
}

#[test]
fn reservation_is_idempotent_and_consumption_moves_to_used() {
    let mut app = application(100);
    let request = ReserveRisk {
        reservation_id: "reservation".into(),
        assessment: assess("request", 40),
    };
    let first = app.reserve(request.clone()).unwrap();
    let second = app.reserve(request).unwrap();
    assert_eq!(first, second);
    assert_eq!(app.snapshot().budgets[0].reserved, amount(40));
    let consumed = app
        .consume(ConsumeReservation {
            reservation_id: "reservation".into(),
        })
        .unwrap();
    assert_eq!(consumed.status, ReservationStatus::Consumed);
    assert_eq!(app.snapshot().budgets[0].used, amount(40));
    assert_eq!(app.snapshot().budgets[0].reserved, amount(0));
}

#[test]
fn release_returns_capacity_and_conflicting_id_is_rejected() {
    let mut app = application(100);
    app.reserve(ReserveRisk {
        reservation_id: "reservation".into(),
        assessment: assess("request", 40),
    })
    .unwrap();
    assert!(app
        .release(ReleaseReservation {
            reservation_id: "reservation".into()
        })
        .is_ok());
    assert_eq!(app.snapshot().budgets[0].available(), amount(100));
    let error = app
        .reserve(ReserveRisk {
            reservation_id: "reservation".into(),
            assessment: assess("other", 20),
        })
        .unwrap_err();
    assert!(error.to_string().contains("conflicting"));
}

#[test]
fn multiple_usages_cannot_overcommit_a_budget() {
    let app = application(100);
    let mut request = assess("request", 60);
    request.usages.push(Usage {
        metric: Metric::Notional,
        amount: amount(60),
        budgets: vec![BudgetRef {
            scope: "account".into(),
            subject: "main".into(),
        }],
    });
    assert!(!app.assess(request).unwrap().allowed);
}

#[test]
fn publisher_emits_reserved_budget_and_allocation() {
    let mut app = application(100);
    app.reserve(ReserveRisk {
        reservation_id: "reservation".into(),
        assessment: assess("request", 40),
    })
    .unwrap();
    let snapshot = app.snapshot();
    let mut writer = FlatbuffersRiskSnapshotWriter::new("risk");
    writer.publish(&snapshot).unwrap();
    let payload = writer.last_payload.unwrap();
    assert!(risk_snapshot_buffer_has_identifier(&payload));
    let decoded = root_as_risk_snapshot(&payload).unwrap();
    assert_eq!(
        decoded
            .payload()
            .budgets()
            .unwrap()
            .get(0)
            .reserved()
            .mantissa(),
        40
    );
    assert_eq!(
        decoded
            .payload()
            .reservations()
            .unwrap()
            .get(0)
            .allocations()
            .unwrap()
            .len(),
        1
    );
}

#[test]
fn application_restores_actor_generation_from_store() {
    let snapshot = kairos_risk::RiskSnapshot {
        actor_id: "risk".into(),
        generation: 7,
        event_sequence: 11,
        budgets: vec![Budget {
            budget_id: "account-notional".into(),
            owner_id: "account".into(),
            reference: BudgetRef {
                scope: "account".into(),
                subject: "main".into(),
            },
            metric: Metric::Notional,
            limit: amount(100),
            used: amount(20),
            reserved: amount(10),
            valid_from_unix_nanos: None,
            valid_until_unix_nanos: None,
        }],
        reservations: Vec::new(),
    };
    let app = RiskApplication::with_dependencies(
        "risk",
        Vec::new(),
        false,
        Some(Box::new(MemoryRiskStore {
            snapshot: Some(snapshot),
        })),
    )
    .unwrap();
    let restored = app.snapshot();
    assert_eq!(restored.generation, 7);
    assert_eq!(restored.event_sequence, 11);
    assert_eq!(restored.budgets[0].used, amount(20));
}

#[test]
fn event_publisher_emits_reservation_fact_with_sequence() {
    let reservation = kairos_risk::Reservation {
        reservation_id: "reservation".into(),
        request_id: "request".into(),
        allocations: vec![kairos_risk::domain::ReservationAllocation {
            budget_id: "account-notional".into(),
            metric: Metric::Notional,
            amount: amount(40),
        }],
        status: ReservationStatus::Reserved,
        created_at_unix_nanos: 1,
        updated_at_unix_nanos: 1,
    };
    let mut writer = FlatbuffersRiskEventWriter::new("risk");
    writer
        .publish(&kairos_risk::RiskEvent::ReservationChanged {
            reservation,
            event_sequence: 3,
        })
        .unwrap();
    let payload = writer.last_payload.unwrap();
    assert!(reservation_event_buffer_has_identifier(&payload));
    let event = root_as_reservation_event(&payload).unwrap();
    assert_eq!(event.header().sequence(), 3);
    assert_eq!(event.reservation_id(), "reservation");
    assert_eq!(event.allocations().unwrap().len(), 1);
}

#[test]
fn actor_publishes_reservation_facts_after_state_changes() {
    let mut app = RiskApplication::with_dependencies(
        "risk",
        vec![Budget {
            budget_id: "account-notional".into(),
            owner_id: "account".into(),
            reference: BudgetRef {
                scope: "account".into(),
                subject: "main".into(),
            },
            metric: Metric::Notional,
            limit: amount(100),
            used: Amount::ZERO,
            reserved: Amount::ZERO,
            valid_from_unix_nanos: None,
            valid_until_unix_nanos: None,
        }],
        false,
        None,
    )
    .unwrap();
    app.reserve(ReserveRisk {
        reservation_id: "reservation".into(),
        assessment: assess("request", 40),
    })
    .unwrap();
    app.release(ReleaseReservation {
        reservation_id: "reservation".into(),
    })
    .unwrap();
    let events = app.drain_events();
    assert_eq!(events.len(), 2);
    assert!(matches!(
        events[0],
        kairos_risk::RiskEvent::ReservationChanged { .. }
    ));
    assert!(matches!(
        events[1],
        kairos_risk::RiskEvent::ReservationChanged { .. }
    ));
}
