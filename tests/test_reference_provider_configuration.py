from __future__ import annotations

import json
from io import StringIO
import tomllib

from kairospy.system.apps.credentials.application import CredentialConfigurationApplication
from kairospy.investment.apps.reference.application import ReferenceProviderConfigurationApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication
from kairospy.surface.cli import execute_argv


def _workspace(tmp_path, monkeypatch):
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="data-provider"
    )
    CredentialConfigurationApplication(workspace).configure(
        "massive-readonly",
        provider="massive",
        values={"api_key": "do-not-persist"},
    )
    return workspace


def test_massive_configuration_writes_only_the_integration_connection(
    tmp_path, monkeypatch
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    app = ReferenceProviderConfigurationApplication(workspace)

    configured = app.configure_massive(
        credential_id="massive-readonly",
        endpoint="https://massive.example",
        capabilities=("reference", "equity_market"),
    )

    assert configured["verification_status"] == "pending"
    assert configured["shared_by"] == ["Reference"]
    manifest = tomllib.loads(workspace.paths.manifest.read_text())
    assert "reference" not in manifest or "providers" not in manifest["reference"]
    assert "market" not in manifest or "providers" not in manifest["market"]
    connection = tomllib.loads(
        (workspace.paths.provider_connections_root() / "massive.toml").read_text()
    )["connection"]
    assert connection["credential_id"] == "massive-readonly"
    assert connection["endpoint"] == "https://massive.example"
    assert "do-not-persist" not in workspace.paths.manifest.read_text()


def test_massive_configuration_rejects_missing_api_key(
    tmp_path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="data-provider"
    )
    import pytest

    with pytest.raises(ValueError, match="api_key"):
        CredentialConfigurationApplication(workspace).configure(
            "massive-readonly", provider="massive", values={}
        )




def test_massive_disable_and_delete_preserve_the_workspace_credential(
    tmp_path, monkeypatch
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    application = ReferenceProviderConfigurationApplication(workspace)
    application.configure_massive(credential_id="massive-readonly")

    disabled = application.set_enabled("massive", enabled=False)
    assert disabled["enabled"] is False
    assert disabled["verification_status"] == "pending"

    deleted = application.delete("massive")
    assert deleted == {"connection_id": "massive", "status": "deleted"}
    assert application.list() == []
    assert CredentialConfigurationApplication(workspace).show("massive-readonly")


def test_massive_manual_read_test_is_auditable_and_configuration_change_invalidates_it(
    tmp_path, monkeypatch
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    app = ReferenceProviderConfigurationApplication(workspace)
    app.configure_massive(credential_id="massive-readonly")

    value = app.test_connection(
        probe=lambda _endpoint, _api_key: {
            "reference_symbol": "AAPL",
            "market_symbol": "SPY",
            "bar_count": 1,
        }
    )

    assert value["verification_status"] == "verified"
    assert value["samples"] == {
        "reference_symbol": "AAPL",
        "market_symbol": "SPY",
        "bar_count": 1,
    }
    assert "Options catalog and market data" in value["not_tested"]
    evidence_path = workspace.paths.child(
        "state", "configuration", "data-providers", "massive.json"
    )
    evidence = json.loads(evidence_path.read_text())
    assert evidence["configuration_hash"]
    assert "do-not-persist" not in evidence_path.read_text()

    app.configure_massive(
        credential_id="massive-readonly", endpoint="https://another.example"
    )
    assert app.show()["verification_status"] == "retest_required"


def test_massive_failed_probe_stores_category_not_provider_payload(
    tmp_path, monkeypatch
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    app = ReferenceProviderConfigurationApplication(workspace)
    app.configure_massive(credential_id="massive-readonly")

    result = app.test_connection(
        probe=lambda _endpoint, _api_key: (_ for _ in ()).throw(
            ValueError("provider echoed do-not-persist")
        )
    )

    assert result["verification_status"] == "failed"
    evidence = workspace.paths.child(
        "state", "configuration", "data-providers", "massive.json"
    ).read_text()
    assert "do-not-persist" not in evidence
    assert "invalid_response" in evidence
