use kairos_primitives::runtime::{ActorId, InstanceIdentity};
use kairos_risk_contract::{DecodedRiskEvent, risk_indexed_environment_path};

#[test]
fn risk_indexed_view_is_partitioned_by_actor_and_instance() {
    let identity = InstanceIdentity::new("workspace", "launch", "instance-1").unwrap();
    let actor_id = ActorId::new("risk:instance-1").unwrap();
    assert!(
        risk_indexed_environment_path("/runtime", &identity, &actor_id)
            .unwrap()
            .ends_with("Risk/risk-risk%3Ainstance-1/epoch-1/current.lmdb")
    );
}

#[test]
fn unknown_risk_event_identifier_is_rejected() {
    let error = match kairos_risk_contract::event::decode_event(b"not-a-flatbuffer") {
        Ok(_) => panic!("unknown root must be rejected"),
        Err(error) => error,
    };
    assert!(
        error
            .to_string()
            .contains("unknown Risk v2 event identifier")
    );
}

#[test]
fn event_api_is_typed_and_does_not_expose_string_discriminator() {
    fn assert_typed<'a>(_: Option<DecodedRiskEvent<'a>>) {}
    assert_typed(None);
}
