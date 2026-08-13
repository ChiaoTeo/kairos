use std::collections::BTreeMap;

use kairos_protocol::generated::kairos::{
    account::v_1 as account_fb, execution::v_1 as execution_fb, intent::v_1 as intent_fb,
    risk::v_1 as risk_fb,
};

fn fixtures() -> BTreeMap<String, Vec<u8>> {
    let values: BTreeMap<String, String> =
        serde_json::from_str(include_str!("../../../tests/fixtures/active_wire/v1.json"))
            .expect("valid active wire fixture manifest");
    values
        .into_iter()
        .map(|(name, value)| (name, decode_hex(&value)))
        .collect()
}

fn decode_hex(value: &str) -> Vec<u8> {
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).expect("valid hex byte")
        })
        .collect()
}

macro_rules! rejects_bad_identifier {
    ($payload:expr, $identifier_check:path) => {{
        assert!($identifier_check(&$payload));
        let mut corrupted = $payload.clone();
        corrupted[4..8].copy_from_slice(b"BAD1");
        assert!(!$identifier_check(&corrupted));
    }};
}

#[test]
fn rust_decodes_account_execution_and_risk_active_roots() {
    let fixtures = fixtures();

    let event = account_fb::root_as_account_event(&fixtures["account.event.ACE1"]).unwrap();
    assert_eq!(event.header().sequence(), 1);
    assert_eq!(event.account_id(), "account:fixture");
    assert_eq!(event.changes().unwrap().get(0).kind(), "status_changed");
    let current = account_fb::root_as_accounts_snapshot(&fixtures["account.current.AAC1"]).unwrap();
    assert_eq!(current.header().generation(), 7);
    assert_eq!(current.payload().account_count(), 0);
    rejects_bad_identifier!(
        fixtures["account.event.ACE1"],
        account_fb::account_event_buffer_has_identifier
    );
    rejects_bad_identifier!(
        fixtures["account.current.AAC1"],
        account_fb::accounts_snapshot_buffer_has_identifier
    );

    let event =
        execution_fb::root_as_execution_event_message(&fixtures["execution.event.EXE1"]).unwrap();
    assert_eq!(event.header().sequence(), 1);
    assert_eq!(event.changes().unwrap().get(0).kind(), "order_update");
    assert_eq!(
        event.changes().unwrap().get(0).order().unwrap().order_id(),
        "order:fixture"
    );
    let orders = execution_fb::root_as_orders_snapshot(&fixtures["execution.orders.PEO1"]).unwrap();
    assert_eq!(orders.header().generation(), 7);
    let intents = intent_fb::root_as_intent_snapshot(&fixtures["execution.intents.PIJ1"]).unwrap();
    assert_eq!(intents.header().generation(), 7);
    rejects_bad_identifier!(
        fixtures["execution.event.EXE1"],
        execution_fb::execution_event_message_buffer_has_identifier
    );
    rejects_bad_identifier!(
        fixtures["execution.orders.PEO1"],
        execution_fb::orders_snapshot_buffer_has_identifier
    );
    rejects_bad_identifier!(
        fixtures["execution.intents.PIJ1"],
        intent_fb::intent_snapshot_buffer_has_identifier
    );

    let event = risk_fb::root_as_risk_event_message(&fixtures["risk.event.RKE1"]).unwrap();
    assert_eq!(event.header().sequence(), 1);
    assert_eq!(event.kind(), "decision_evaluated");
    assert_eq!(event.decision_id(), Some("decision:fixture"));
    let current = risk_fb::root_as_risk_snapshot(&fixtures["risk.current.PRK1"]).unwrap();
    assert_eq!(current.header().generation(), 7);
    assert_eq!(current.payload().budget_count(), 0);
    rejects_bad_identifier!(
        fixtures["risk.event.RKE1"],
        risk_fb::risk_event_message_buffer_has_identifier
    );
    rejects_bad_identifier!(
        fixtures["risk.current.PRK1"],
        risk_fb::risk_snapshot_buffer_has_identifier
    );
}
