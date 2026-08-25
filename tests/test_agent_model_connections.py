from __future__ import annotations

from pathlib import Path

from kairospy.strategy.apps.agent.application import AgentResourceApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication


def test_openai_connection_uses_private_value_and_records_manual_test(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="a")
    resources = AgentResourceApplication(workspace)
    resources.configure_openai_credential("openai-main", "sk-do-not-record")
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


def test_openai_test_is_invalidated_when_credential_value_changes(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="a")
    resources = AgentResourceApplication(workspace)
    resources.configure_openai_credential("openai-main", "first")
    resources.test_openai_model(
        "openai-main", "gpt-5.4-2026-08-01", probe=lambda *_: None
    )

    resources.configure_openai_credential(
        "openai-main", "second", overwrite=True
    )

    assert (
        resources.model_verification("openai-main")["verification_status"]
        == "retest_required"
    )


def test_plaintext_openai_credential_write_uses_private_credential_file(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="a")

    path = AgentResourceApplication(workspace).create_openai_credential(
        "openai-main", "sk-plaintext"
    )
    assert path.stat().st_mode & 0o777 == 0o600
