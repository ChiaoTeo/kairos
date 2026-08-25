use std::path::{Path, PathBuf};

fn source_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("src")
}

#[test]
fn integration_uses_the_target_top_level_layers() {
    let root = source_root();
    for layer in [
        "blocking",
        "capabilities",
        "composition",
        "domain",
        "participants",
        "services",
        "transport",
    ] {
        assert!(root.join(layer).is_dir(), "missing target layer: {layer}");
    }

    for obsolete in [
        "application",
        "blocking.rs",
        "credentials.rs",
        "protocol.rs",
    ] {
        assert!(
            !root.join(obsolete).exists(),
            "obsolete root module returned: {obsolete}"
        );
    }
}

#[test]
fn credential_storage_is_not_owned_by_integration() {
    let root = source_root();
    assert!(!root.join("composition/credentials.rs").exists());
    let composition = std::fs::read_to_string(root.join("composition/mod.rs"))
        .expect("read Integration composition root");
    assert!(!composition.contains("CredentialStore"));
    assert!(!composition.contains("CredentialRecord"));
}

#[test]
fn services_have_only_participant_implementation_axes() {
    let services = source_root().join("services");
    for axis in ["participants"] {
        assert!(services.join(axis).is_dir(), "missing service axis: {axis}");
    }

    for obsolete in [
        "auth.rs",
        "connections",
        "drivers",
        "factories",
        "gateways",
        "quota",
        "streams",
    ] {
        assert!(
            !services.join(obsolete).exists(),
            "obsolete parallel service layer returned: {obsolete}"
        );
    }
}

#[test]
fn capabilities_and_domain_facts_have_distinct_semantic_homes() {
    let capabilities = source_root().join("capabilities");
    for capability in [
        "account.rs",
        "connection.rs",
        "earn.rs",
        "event.rs",
        "execution.rs",
        "market.rs",
        "reference.rs",
        "transfer.rs",
    ] {
        assert!(
            capabilities.join(capability).is_file(),
            "missing capability module: {capability}"
        );
    }

    for model in ["account.rs", "execution.rs", "market.rs"] {
        assert!(
            source_root().join("domain").join(model).is_file(),
            "missing Integration domain fact: {model}"
        );
    }
    assert!(!source_root().join("application").exists());
}

#[test]
fn capability_and_blocking_surfaces_have_distinct_ownership() {
    let root = source_root();
    let capabilities = root.join("capabilities");
    let blocking = root.join("blocking");

    for entry in std::fs::read_dir(&capabilities).expect("read capabilities") {
        let path = entry.expect("read capability entry").path();
        if path.extension().and_then(|value| value.to_str()) == Some("rs") {
            let source = std::fs::read_to_string(&path).expect("read capability source");
            assert!(
                !source.contains("Blocking"),
                "async capability owns a blocking facade: {}",
                path.display()
            );
            assert!(
                !source.contains("pub struct ")
                    && !source.contains("pub enum ")
                    && !source.contains("pub type "),
                "capability module owns a domain object: {}",
                path.display()
            );
        }
    }

    for module in [
        "account.rs",
        "connection.rs",
        "earn.rs",
        "event.rs",
        "execution.rs",
        "market.rs",
        "reference.rs",
        "transfer.rs",
    ] {
        assert!(
            blocking.join(module).is_file(),
            "missing blocking module: {module}"
        );
        let source = std::fs::read_to_string(blocking.join(module)).expect("read blocking module");
        assert!(
            !source.contains("pub struct ")
                && !source.contains("pub enum ")
                && !source.contains("pub type "),
            "blocking module owns a domain object: {module}"
        );
    }
}

#[test]
fn stable_surface_files_use_single_word_names() {
    let root = source_root();
    for directory in ["capabilities", "blocking", "domain"] {
        for entry in std::fs::read_dir(root.join(directory)).expect("read stable surface") {
            let path = entry.expect("read stable surface entry").path();
            if path.extension().and_then(|value| value.to_str()) == Some("rs") {
                let stem = path.file_stem().and_then(|value| value.to_str()).unwrap();
                assert!(
                    !stem.contains('_'),
                    "compound file name must become nested modules: {}",
                    path.display()
                );
            }
        }
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
        domain.join("reference.rs").is_file(),
        "Integration-owned participant reference facts are missing"
    );
}

#[test]
fn participant_connections_keep_context_separate_from_capability_implementations() {
    let participants = source_root().join("participants");
    for obsolete in [
        "okx/connection.rs",
        "okx/types.rs",
        "hyperliquid/connection.rs",
        "hyperliquid/market.rs",
        "ibkr/connection.rs",
    ] {
        assert!(
            !participants.join(obsolete).exists(),
            "obsolete participant facade returned: {obsolete}"
        );
    }

    for connection in [
        "okx/public/rest.rs",
        "okx/public/websocket.rs",
        "okx/private/rest.rs",
        "okx/private/websocket.rs",
        "hyperliquid/info/rest.rs",
        "hyperliquid/websocket.rs",
        "ibkr/account.rs",
        "ibkr/market.rs",
        "ibkr/trading.rs",
    ] {
        assert!(
            participants.join(connection).is_file(),
            "missing concrete participant connection: {connection}"
        );
    }
}

#[test]
fn external_event_envelopes_preserve_participant_and_recovery_identity() {
    let envelope = std::fs::read_to_string(source_root().join("domain/event.rs"))
        .expect("read external event envelope");
    for field in [
        "participant: ParticipantRef",
        "connection_key: crate::ConnectionKey",
        "channel_id: String",
        "channel_epoch: u64",
        "participant_event_id: Option<String>",
        "participant_sequence: Option<u64>",
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
fn transport_is_async_only_and_blocking_is_a_capability_namespace() {
    let http = std::fs::read_to_string(source_root().join("transport/http/mod.rs"))
        .expect("read HTTP transport");
    let websocket = std::fs::read_to_string(source_root().join("transport/websocket/channel.rs"))
        .expect("read WebSocket transport");
    assert!(!http.contains("BlockingHttpClient"));
    assert!(!http.contains("reqwest::blocking"));
    assert!(!websocket.contains("BlockingTokioSocket"));
    assert!(!websocket.contains("std::thread::Builder"));
}
