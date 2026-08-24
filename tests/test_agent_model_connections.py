from __future__ import annotations

from pathlib import Path

import pytest

from kairospy.strategy.apps.agent.application import AgentResourceApplication
from kairospy.system.apps.credentials.application import SecretRef
from kairospy.system.apps.workspace.application import WorkspaceApplication


def test_openai_connection_requires_secret_ref_and_records_manual_test(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="a")
    resources = AgentResourceApplication(workspace)
    monkeypatch.setenv("OPENAI_KAIROS_API_KEY", "sk-do-not-record")
    resources.configure_openai_credential(
        "openai-main", SecretRef("env", "OPENAI_KAIROS_API_KEY")
    )
    observed: list[tuple[str, str]] = []

    result = resources.test_openai_model(
        "openai-main",
        "gpt-5.4-2026-08-01",
        probe=lambda api_key, model: observed.append((api_key, model)),
    )

    assert observed == [("sk-do-not-record", "gpt-5.4-2026-08-01")]
    assert result["verification_status"] == "verified"
    assert resources.model_connections()[0]["verification_status"] == "verified"
    evidence = workspace.paths.child(
        "state", "configuration", "models", "openai-main.json"
    ).read_text(encoding="utf-8")
    assert "sk-do-not-record" not in evidence


def test_openai_test_is_invalidated_when_credential_reference_changes(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="a")
    resources = AgentResourceApplication(workspace)
    monkeypatch.setenv("OPENAI_FIRST_KEY", "first")
    monkeypatch.setenv("OPENAI_SECOND_KEY", "second")
    resources.configure_openai_credential(
        "openai-main", SecretRef("env", "OPENAI_FIRST_KEY")
    )
    resources.test_openai_model(
        "openai-main", "gpt-5.4-2026-08-01", probe=lambda *_: None
    )

    resources.configure_openai_credential(
        "openai-main", SecretRef("env", "OPENAI_SECOND_KEY"), overwrite=True
    )

    assert (
        resources.model_verification("openai-main")["verification_status"]
        == "retest_required"
    )


def test_plaintext_openai_credential_write_is_rejected(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="a")

    with pytest.raises(ValueError, match="SecretRef"):
        AgentResourceApplication(workspace).create_openai_credential(
            "openai-main", "sk-plaintext"
        )
