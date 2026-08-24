from __future__ import annotations

import json
import stat
from io import StringIO
from pathlib import Path

import pytest

from kairospy.strategy.apps.agent.application import AgentResourceApplication
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
    SecretRef,
)
from kairospy.system.apps.launch.application.wizard import (
    LaunchDraft,
    build_and_validate,
)
from kairospy.system.apps.launch.application import LaunchConfigurationApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication
from kairospy.surface.cli import execute_argv


MODEL = "gpt-5.4-2026-08-01"


def _workspace(tmp_path: Path):
    return WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ws")


def _prepared_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = _workspace(tmp_path)
    monkeypatch.setenv("KAIROS_OPENAI_KEY", "sk-secret-never-persist")
    credentials = CredentialConfigurationApplication(workspace)
    credential = credentials.configure(
        "openai-prod",
        provider="openai",
        role="model-inference",
        fields={"api_key": SecretRef("env", "KAIROS_OPENAI_KEY")},
    )
    resources = AgentResourceApplication(workspace)
    resources.test_openai_model(
        "openai-prod", MODEL, probe=lambda _api_key, _model: {"output": "OK"}
    )
    return workspace, resources, credential


def test_workspace_owns_only_secret_ref_model_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, resources, credential = _prepared_resources(tmp_path, monkeypatch)

    status = resources.status()

    assert status["ready"] is True
    assert status["credentials"] == ["openai-prod"]
    assert status["profiles"] == []
    assert status["mcp"] == []
    assert status["model_connections"][0]["verification_status"] == "verified"
    assert "sk-secret-never-persist" not in repr(status)
    path = workspace.paths.credential_config().parent / "openai-prod.toml"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert credential["secret_refs"] == {
        "api_key": {"source": "env", "id": "KAIROS_OPENAI_KEY"}
    }
    assert not workspace.paths.agent_profiles_root().exists()
    assert not workspace.paths.agent_mcp_config().exists()


def test_plaintext_agent_credential_write_is_rejected(tmp_path: Path) -> None:
    resources = AgentResourceApplication(_workspace(tmp_path))

    with pytest.raises(ValueError, match="plaintext"):
        resources.create_openai_credential("openai-prod", "sk-secret")


def test_agent_status_cli_is_secret_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _, _ = _prepared_resources(tmp_path, monkeypatch)
    output = StringIO()

    assert (
        execute_argv(
            [
                "config",
                "agent",
                "status",
                "--workspace",
                str(workspace.paths.root),
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )
    value = json.loads(output.getvalue())
    assert value["model_connections"][0]["verification_status"] == "verified"
    assert "sk-secret-never-persist" not in output.getvalue()
