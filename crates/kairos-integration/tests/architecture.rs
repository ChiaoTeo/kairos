use std::path::{Path, PathBuf};

fn source_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("src")
}

#[test]
fn integration_uses_the_target_top_level_layers() {
    let root = source_root();
    for layer in ["application", "bin", "composition", "domain", "services"] {
        assert!(root.join(layer).is_dir(), "missing target layer: {layer}");
    }

    for obsolete in ["blocking.rs", "credentials.rs", "protocol.rs"] {
        assert!(
            !root.join(obsolete).exists(),
            "obsolete root module returned: {obsolete}"
        );
    }
}

#[test]
fn services_have_only_participant_transport_and_quota_axes() {
    let services = source_root().join("services");
    for axis in ["participants", "transport", "quota"] {
        assert!(services.join(axis).is_dir(), "missing service axis: {axis}");
    }

    for obsolete in [
        "auth.rs",
        "connections",
        "drivers",
        "factories",
        "gateways",
        "streams",
    ] {
        assert!(
            !services.join(obsolete).exists(),
            "obsolete parallel service layer returned: {obsolete}"
        );
    }
}

#[test]
fn application_capabilities_have_one_semantic_home() {
    let application = source_root().join("application");
    let capabilities = application.join("capabilities");
    for capability in [
        "account.rs",
        "execution.rs",
        "funding.rs",
        "market.rs",
        "reference.rs",
    ] {
        assert!(
            capabilities.join(capability).is_file(),
            "missing capability module: {capability}"
        );
    }

    for obsolete in [
        "account.rs",
        "account_inspection.rs",
        "connection.rs",
        "earn.rs",
        "execution_stream.rs",
        "historical.rs",
        "market.rs",
        "market_stream.rs",
        "order_query.rs",
        "reference",
        "transfer.rs",
    ] {
        assert!(
            !application.join(obsolete).exists(),
            "capability has a second application home: {obsolete}"
        );
    }
}

#[test]
fn stable_domain_axes_use_the_target_names() {
    let domain = source_root().join("domain");
    for axis in [
        "participant.rs",
        "connection.rs",
        "instrument.rs",
        "operation.rs",
    ] {
        assert!(domain.join(axis).is_file(), "missing domain axis: {axis}");
    }
    assert!(
        !domain.join("reference.rs").exists(),
        "canonical Reference types returned to Integration domain"
    );
}

#[test]
fn participant_connections_keep_context_separate_from_capability_implementations() {
    let participants = source_root().join("application/participants");
    for (participant, capabilities) in [
        (
            "binance",
            &["account", "execution", "funding", "reference"][..],
        ),
        ("okx", &["account", "execution", "reference"][..]),
    ] {
        let participant = participants.join(participant);
        let facade = participant.join("connection.rs");
        let implementations = participant.join("connection");
        let source = std::fs::read_to_string(&facade).expect("read participant connection facade");

        assert!(
            // Binance keeps the four product-native connection constructors in
            // one public facade so callers cannot accidentally combine an
            // endpoint family with the wrong capability. The implementation
            // split remains tracked by the boundary-remediation task.
            source.lines().count() < 1_100,
            "participant connection facade grew into a second implementation home: {}",
            facade.display()
        );
        for capability in capabilities {
            assert!(
                implementations.join(format!("{capability}.rs")).is_file(),
                "missing participant capability implementation: {participant:?}/{capability}"
            );
        }
        assert!(implementations.join("blocking.rs").is_file());
        assert!(implementations.join("tests.rs").is_file());

        for implementation in [
            "impl AsyncAccount",
            "impl AsyncEarn",
            "impl AsyncInstrument",
            "impl AsyncMarket",
            "impl AsyncOrder",
            "impl AsyncTransfer",
            "pub mod blocking {",
        ] {
            assert!(
                !source.contains(implementation),
                "capability implementation returned to connection facade: {implementation}"
            );
        }
        for rejected in ["manager.rs", "registry.rs"] {
            assert!(
                !participant.join(rejected).exists() && !implementations.join(rejected).exists(),
                "participant introduced rejected orchestration layer: {rejected}"
            );
        }
    }
}

#[test]
fn external_event_envelopes_preserve_provider_and_recovery_identity() {
    let envelope = std::fs::read_to_string(source_root().join("application/external_event.rs"))
        .expect("read external event envelope");
    for field in [
        "participant: ParticipantRef",
        "binding_id: String",
        "channel_id: String",
        "channel_epoch: u64",
        "provider_event_id: Option<String>",
        "provider_sequence: Option<u64>",
        "observed_at_unix_nanos: UnixNanos",
        "received_at_unix_nanos: UnixNanos",
    ] {
        assert!(
            envelope.contains(field),
            "external envelope missing {field}"
        );
    }
}

#[test]
fn blocking_http_worker_is_lazy_for_async_first_connections() {
    let http = std::fs::read_to_string(source_root().join("services/transport/http/mod.rs"))
        .expect("read HTTP transport");
    let constructor = http
        .split("impl PublicHttpClient")
        .nth(1)
        .and_then(|source| source.split("fn worker(&self)").next())
        .expect("blocking HTTP constructor");
    assert!(!constructor.contains("std::thread::Builder"));
    assert!(http.contains("constructing_blocking_projection_does_not_start_a_hidden_worker"));
}
