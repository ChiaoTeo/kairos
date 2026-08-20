use kairos_capital_contract::{
    FundingLocation, FundingObjectivePriority, PublishFundingObjectiveRequest,
    QueryCapitalAvailabilityRequest, ReconcileCapitalPlanRequest,
};
use kairos_primitives::account::{AccountId, BrokerId, SegmentKey};
use kairos_primitives::capital::{CapitalGroupId, CapitalPlanId, FundingObjectiveId};
use kairos_primitives::decimal::Quantity;
use kairos_primitives::reference::Currency;
use kairos_primitives::runtime::{RequestId, StrategyDecisionId, StrategyId};
use kairos_primitives::time::{BasisPoints, Generation, UnixNanos};

#[test]
fn funding_objective_control_has_no_route_or_source_authority() {
    let request = PublishFundingObjectiveRequest {
        request_id: RequestId::new("request-1").unwrap(),
        capital_group_id: CapitalGroupId::new("group-a").unwrap(),
        objective_id: FundingObjectiveId::new("buffer-usdt").unwrap(),
        version: Generation::new(2),
        strategy_id: StrategyId::new("basis").unwrap(),
        destination: FundingLocation {
            broker: BrokerId::new("binance").unwrap(),
            account_id: AccountId::new("account-a").unwrap(),
            segment: SegmentKey::new("usd-m").unwrap(),
            asset: Currency::new("USDT").unwrap(),
        },
        desired_available: "80000".parse::<Quantity>().unwrap(),
        required_by_unix_nanos: UnixNanos::new(200),
        expires_at_unix_nanos: UnixNanos::new(500),
        priority: FundingObjectivePriority::High,
        confidence_bps: BasisPoints::new(8_000),
        strategy_decision_id: StrategyDecisionId::new("decision-7").unwrap(),
        observed_at_unix_nanos: UnixNanos::new(100),
    };

    let value = serde_json::to_value(request).unwrap();
    assert_eq!(value["desired_available"], "80000");
    assert_eq!(value["priority"], "high");
    assert!(value.get("source_account_id").is_none());
    assert!(value.get("route_id").is_none());
    assert!(value.get("participant_endpoint").is_none());
}

#[test]
fn availability_query_is_scoped_to_one_group_location() {
    let request = QueryCapitalAvailabilityRequest {
        request_id: RequestId::new("availability-1").unwrap(),
        capital_group_id: CapitalGroupId::new("group-a").unwrap(),
        location: FundingLocation {
            broker: BrokerId::new("binance").unwrap(),
            account_id: AccountId::new("account-a").unwrap(),
            segment: SegmentKey::new("usd-m").unwrap(),
            asset: Currency::new("USDT").unwrap(),
        },
    };

    let value = serde_json::to_value(request).unwrap();
    assert_eq!(value["location"]["segment"], "usd-m");
    assert!(value.get("source").is_none());
}

#[test]
fn manual_reconcile_identifies_only_an_existing_plan_and_observation_time() {
    let request = ReconcileCapitalPlanRequest {
        request_id: RequestId::new("reconcile-1").unwrap(),
        capital_group_id: CapitalGroupId::new("group-a").unwrap(),
        plan_id: CapitalPlanId::new("plan-a").unwrap(),
        observed_at_unix_nanos: UnixNanos::new(900),
    };

    let value = serde_json::to_value(request).unwrap();
    assert_eq!(value["plan_id"], "plan-a");
    assert!(value.get("route_id").is_none());
    assert!(value.get("amount").is_none());
    assert!(value.get("idempotency_key").is_none());
}
