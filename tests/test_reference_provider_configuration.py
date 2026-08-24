from __future__ import annotations

import json
from io import StringIO
import tomllib

from kairospy.application.workspace.credentials import (
    CredentialConfigurationApplication,
    SecretRef,
)
from kairospy.application.reference import ReferenceProviderConfigurationApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli import execute_argv


def _workspace(tmp_path, monkeypatch):
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="data-provider"
    )
    monkeypatch.setenv("KAIROS_MASSIVE_API_KEY", "do-not-persist")
    CredentialConfigurationApplication(workspace).configure(
        "massive-readonly",
        provider="massive",
        fields={"api_key": SecretRef("env", "KAIROS_MASSIVE_API_KEY")},
    )
    return workspace


def test_massive_configuration_owns_reference_and_market_sections(
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
    assert configured["shared_by"] == ["Reference", "Market"]
    manifest = tomllib.loads(workspace.paths.manifest.read_text())
    assert (
        manifest["reference"]["providers"]["massive"]["credential_id"]
        == "massive-readonly"
    )
    assert manifest["market"]["providers"] == [
        {
            "type": "massive",
            "product": "equity",
            "enabled": True,
            "credential_id": "massive-readonly",
            "endpoint": "https://massive.example",
        }
    ]
    assert "do-not-persist" not in workspace.paths.manifest.read_text()


def test_massive_configuration_can_be_saved_before_secret_is_visible(
    tmp_path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="data-provider"
    )
    monkeypatch.delenv("KAIROS_MASSIVE_API_KEY", raising=False)
    CredentialConfigurationApplication(workspace).configure(
        "massive-readonly",
        provider="massive",
        fields={"api_key": SecretRef("env", "KAIROS_MASSIVE_API_KEY")},
    )

    configured = ReferenceProviderConfigurationApplication(workspace).configure_massive(
        credential_id="massive-readonly"
    )

    assert configured["configured"] is False
    assert configured["verification_status"] == "pending"
    assert configured["credential_id"] == "massive-readonly"
    assert "KAIROS_MASSIVE_API_KEY" not in workspace.paths.manifest.read_text()


def test_guided_massive_setup_accepts_hidden_direct_api_key(
    tmp_path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="data-provider"
    )
    answers = iter(("1", "1", "massive-direct-key", "https://api.massive.com"))
    prompt_options: list[dict[str, object]] = []

    def prompt(*_args, **kwargs):
        prompt_options.append(kwargs)
        return next(answers)

    confirmations = iter((True, False))
    monkeypatch.setattr("typer.prompt", prompt)
    monkeypatch.setattr("typer.confirm", lambda *_args, **_kwargs: next(confirmations))
    output = StringIO()

    assert (
        execute_argv(
            [
                "config",
                "data",
                "setup",
                "--credential-id",
                "massive-readonly",
                "--workspace",
                str(workspace.paths.root),
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )

    payload = json.loads(output.getvalue().splitlines()[-1])
    assert payload["connection_id"] == "massive"
    assert payload["verification_status"] == "pending"
    assert "massive-direct-key" not in output.getvalue()
    assert any(options.get("hide_input") is True for options in prompt_options)
    assert (
        CredentialConfigurationApplication(workspace).resolve_field(
            "massive-readonly", "api_key"
        )
        == "massive-direct-key"
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
