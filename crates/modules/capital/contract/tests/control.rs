use kairos_capital_contract::{
    FundingLocation, FundingObjectivePriority, PublishFundingObjectiveRequest,
};

#[test]
fn funding_objective_control_has_no_route_or_source_authority() {
    let request = PublishFundingObjectiveRequest {
        request_id: "request-1".into(),
        capital_group_id: "group-a".into(),
        objective_id: "buffer-usdt".into(),
        version: 2,
        strategy_id: "basis".into(),
        destination: FundingLocation {
            broker: "binance".into(),
            account_id: "account-a".into(),
            segment: "usd-m".into(),
            asset: "USDT".into(),
        },
        desired_available: "80000".into(),
        required_by_unix_nanos: 200,
        expires_at_unix_nanos: 500,
        priority: FundingObjectivePriority::High,
        confidence_bps: 8_000,
        strategy_decision_id: "decision-7".into(),
        observed_at_unix_nanos: 100,
    };

    let value = serde_json::to_value(request).unwrap();
    assert_eq!(value["desired_available"], "80000");
    assert_eq!(value["priority"], "high");
    assert!(value.get("source_account_id").is_none());
    assert!(value.get("route_id").is_none());
    assert!(value.get("participant_endpoint").is_none());
}
