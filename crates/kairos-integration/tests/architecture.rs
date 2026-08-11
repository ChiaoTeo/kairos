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
