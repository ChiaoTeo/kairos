use kairos_risk_contract::{DecodedRiskEvent, RiskViewKey};

#[test]
fn risk_latest_view_is_partitioned_by_actor() {
    let key = RiskViewKey::latest("risk:instance-1");
    assert!(key
        .resource_path("/runtime")
        .ends_with("risk/risk:instance-1/latest/current.snapshot"));
}

#[test]
fn unknown_risk_event_identifier_is_rejected() {
    let error = match kairos_risk_contract::event::decode_event(b"not-a-flatbuffer") {
        Ok(_) => panic!("unknown root must be rejected"),
        Err(error) => error,
    };
    assert!(error.to_string().contains("unknown Risk v2 event identifier"));
}

#[test]
fn event_api_is_typed_and_does_not_expose_string_discriminator() {
    fn assert_typed<'a>(_: Option<DecodedRiskEvent<'a>>) {}
    assert_typed(None);
}
