from __future__ import annotations

import json
import stat
from io import StringIO
from pathlib import Path

import pytest

from kairospy.strategy.apps.agent.application import AgentResourceApplication
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
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
    credentials = CredentialConfigurationApplication(workspace)
    credential = credentials.configure(
        "openai-prod",
        provider="openai",
        role="model-inference",
        values={"api_key": "sk-secret-never-persist"},
    )
    resources = AgentResourceApplication(workspace)
    resources.test_openai_model(
        "openai-prod", MODEL, probe=lambda _api_key, _model: {"output": "OK"}
    )
    return workspace, resources, credential


def test_workspace_owns_private_credential_model_connection(
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
    path = workspace.paths.credentials_root() / "openai-prod.toml"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert credential["fields"] == ["api_key"]
    assert not workspace.paths.agent_profiles_root().exists()
    assert not workspace.paths.agent_mcp_config().exists()


def test_verified_model_refs_are_concrete_launch_choices(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    resources = AgentResourceApplication(workspace)
    resources.configure_model_connection(
        "ollama-local",
        provider="ollama",
        models=("qwen3:8b", "unverified:latest"),
    )
    resources.test_model_connection(
        "ollama-local", "qwen3:8b", probe=lambda *_args: {"ok": True}
    )

    assert resources.verified_model_refs() == (
        {
            "connection_id": "ollama-local",
            "model": "qwen3:8b",
            "model_ref": "ollama-local/qwen3:8b",
            "provider": "ollama",
            "provider_label": "Ollama",
            "last_tested_at": resources.model_verification(
                "ollama-local", model="qwen3:8b"
            )["last_tested_at"],
        },
    )


def test_refresh_model_catalog_preserves_existing_model_verification(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    resources = AgentResourceApplication(workspace)
    resources.configure_model_connection(
        "ollama-local", provider="ollama", models=("qwen3:8b",)
    )
    resources.test_model_connection(
        "ollama-local", "qwen3:8b", probe=lambda *_args: {"ok": True}
    )

    refreshed = resources.refresh_model_catalog(
        "ollama-local",
        probe=lambda _connection, _secret: (
            {"id": "qwen3:8b"},
            {"id": "deepseek-r1:8b"},
        ),
    )

    assert refreshed["models"] == ["qwen3:8b", "deepseek-r1:8b"]
    assert refreshed["verified_models"] == ["qwen3:8b"]
    assert refreshed["verification_status"] == "verified"


def test_agent_credential_write_uses_private_file(tmp_path: Path) -> None:
    resources = AgentResourceApplication(_workspace(tmp_path))

    path = resources.create_openai_credential("openai-prod", "sk-secret")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


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
