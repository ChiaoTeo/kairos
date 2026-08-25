from __future__ import annotations

import json

import pytest

from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.system.apps.integration.application import (
    ProviderConnectionConfigurationApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication


def _workspace(tmp_path):
    return WorkspaceApplication().init(tmp_path / "workspace", workspace_id="connections")


def _credential(workspace, credential_id: str, provider: str) -> None:
    fields = {
        "massive": {"api_key": "massive-secret"},
        "binance": {"api_key": "binance-key", "api_secret": "binance-secret"},
        "okx": {
            "api_key": "okx-key",
            "api_secret": "okx-secret",
            "passphrase": "okx-passphrase",
        },
    }
    CredentialConfigurationApplication(workspace).configure(
        credential_id, provider=provider, values=fields[provider]
    )


@pytest.mark.parametrize(
    ("provider", "product", "purposes"),
    [
        ("massive", "equity", ("reference-catalog", "market-query")),
        ("binance", "spot", ("market-query", "market-stream")),
        ("okx", "swap", ("market-query", "market-stream")),
    ],
)
def test_configures_supported_provider_connections_without_copying_secrets(
    tmp_path, provider: str, product: str, purposes: tuple[str, ...]
) -> None:
    workspace = _workspace(tmp_path)
    credential_id = f"{provider}-readonly"
    _credential(workspace, credential_id, provider)
    app = ProviderConnectionConfigurationApplication(workspace)

    connection = app.configure(
        f"{provider}-{product}",
        provider=provider,
        credential_id=credential_id,
        products=(product,),
        purposes=purposes,
    )

    assert connection["provider"] == provider
    assert connection["verification_status"] == "pending"
    document = (
        workspace.paths.market_connections_root() / f"{provider}-{product}.toml"
    ).read_text()
    assert "secret" not in document
    assert "passphrase" not in document
    assert app.resource_snapshot(f"{provider}-{product}")["credential_identity"] == {
        "credential_id": credential_id,
        "provider": provider,
        "role": "readonly",
        "fields": sorted(
            CredentialConfigurationApplication(workspace).schema(provider)[
                "required_fields"
            ]
        ),
    }


def test_connection_requires_a_matching_complete_credential(tmp_path) -> None:
    workspace = _workspace(tmp_path)
    _credential(workspace, "binance-readonly", "binance")
    app = ProviderConnectionConfigurationApplication(workspace)

    with pytest.raises(ValueError, match="requires a okx credential"):
        app.configure(
            "okx-spot",
            provider="okx",
            credential_id="binance-readonly",
            products=("spot",),
            purposes=("market-query",),
        )


def test_verification_records_capabilities_not_secrets_and_detects_drift(tmp_path) -> None:
    workspace = _workspace(tmp_path)
    _credential(workspace, "binance-readonly", "binance")
    app = ProviderConnectionConfigurationApplication(workspace)
    app.configure(
        "binance-spot",
        provider="binance",
        credential_id="binance-readonly",
        products=("spot",),
        purposes=("market-query", "market-stream"),
    )

    verification = app.test_connection(
        "binance-spot",
        probe=lambda connection, secrets: {
            "capabilities": ["market-query", "market-stream"],
            "observed_permissions": ["market-read"],
            "warnings": [],
            "saw_secret": secrets["api_secret"] == "binance-secret",
        },
    )

    assert verification["verification_status"] == "verified"
    assert verification["capabilities_verified"] == ["market-query", "market-stream"]
    evidence_path = workspace.paths.child(
        "state", "configuration", "provider-connections", "binance-spot.json"
    )
    evidence = evidence_path.read_text()
    assert "binance-secret" not in evidence
    assert "saw_secret" not in evidence

    app.configure(
        "binance-spot",
        provider="binance",
        credential_id="binance-readonly",
        products=("spot",),
        purposes=("market-query",),
        overwrite=True,
    )
    assert app.show("binance-spot")["verification_status"] == "retest_required"


def test_disable_and_safe_delete_preserve_credential(tmp_path) -> None:
    workspace = _workspace(tmp_path)
    _credential(workspace, "okx-readonly", "okx")
    app = ProviderConnectionConfigurationApplication(workspace)
    app.configure(
        "okx-spot",
        provider="okx",
        credential_id="okx-readonly",
        products=("spot",),
        purposes=("market-query",),
    )

    assert app.set_enabled("okx-spot", enabled=False)["enabled"] is False
    with pytest.raises(ValueError, match="Market:primary"):
        app.delete("okx-spot", referenced_by=("Market:primary",))
    assert app.delete("okx-spot") == {
        "connection_id": "okx-spot",
        "status": "deleted",
    }
    assert CredentialConfigurationApplication(workspace).show("okx-readonly")


def test_failed_probe_keeps_only_stable_error_category(tmp_path) -> None:
    workspace = _workspace(tmp_path)
    _credential(workspace, "massive-readonly", "massive")
    app = ProviderConnectionConfigurationApplication(workspace)
    app.configure(
        "massive-equity",
        provider="massive",
        credential_id="massive-readonly",
        products=("equity",),
        purposes=("market-query",),
    )

    result = app.test_connection(
        "massive-equity",
        probe=lambda _connection, _secrets: (_ for _ in ()).throw(
            ValueError("provider echoed massive-secret")
        ),
    )

    assert result["verification_status"] == "failed"
    evidence_path = workspace.paths.child(
        "state", "configuration", "provider-connections", "massive-equity.json"
    )
    evidence = json.loads(evidence_path.read_text())
    assert evidence["error_category"] == "provider_response"
    assert "massive-secret" not in evidence_path.read_text()
