from __future__ import annotations

import json
import stat
from io import StringIO
from pathlib import Path

import pytest

from kairospy.application.agent import AgentResourceApplication
from kairospy.application.credential import (
    CredentialConfigurationApplication,
    SecretRef,
)
from kairospy.application.launch.application.wizard import (
    LaunchDraft,
    build_and_validate,
    prompt_agent_config,
)
from kairospy.application.launch.application import LaunchConfigurationApplication
from kairospy.application.workspace import WorkspaceApplication
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


def test_paper_launch_agent_guide_builds_inline_profile_and_mcp_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _, _ = _prepared_resources(tmp_path, monkeypatch)
    confirmations = iter((True, False))
    monkeypatch.setattr(
        "kairospy.application.launch.application.wizard.typer.confirm",
        lambda *args, **kwargs: next(confirmations),
    )
    monkeypatch.setattr(
        "kairospy.application.launch.application.wizard.typer.prompt",
        lambda label, default=None, **kwargs: default,
    )

    agent = prompt_agent_config({}, mode="paper", workspace=workspace)

    assert agent["runtime"] == "openai-agents"
    assert agent["profile"]["version"] == "1"
    assert agent["profile"]["goal"]
    assert agent["mcp"] == []
    assert agent["model"] == {
        "provider": "openai",
        "model": MODEL,
        "credential": "openai-prod",
    }
    assert "sk-secret-never-persist" not in repr(agent)


def test_launch_agent_guide_preserves_advanced_runtime_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _, _ = _prepared_resources(tmp_path, monkeypatch)
    confirmations = iter((True, False))
    monkeypatch.setattr(
        "kairospy.application.launch.application.wizard.typer.confirm",
        lambda *args, **kwargs: next(confirmations),
    )
    monkeypatch.setattr(
        "kairospy.application.launch.application.wizard.typer.prompt",
        lambda label, default=None, **kwargs: default,
    )
    current = {
        "agent": {
            "enabled": True,
            "runtime": "openai-agents",
            "profile": {
                "version": "1",
                "goal": "Review intents",
                "rubric": ["Prefer bounded risk"],
                "invalidation_rules": ["Abstain without context"],
                "reason_codes": [],
                "risk_flags": [],
            },
            "max_queue_size": 64,
            "shutdown_timeout_seconds": 9,
            "model": {
                "provider": "openai",
                "model": MODEL,
                "credential": "openai-prod",
                "max_turns": 4,
                "max_output_tokens": 1000,
            },
            "capabilities": {
                "intent_review": {
                    "initial_mode": "shadow",
                    "strategy_selectable_modes": ["shadow", "gate"],
                    "operations": ["target_position"],
                    "max_decision_age_seconds": 3,
                    "required_contexts": [],
                    "revisions": {},
                }
            },
            "mcp": [],
        }
    }

    agent = prompt_agent_config(current, mode="paper", workspace=workspace)

    assert agent["max_queue_size"] == 64
    assert agent["shutdown_timeout_seconds"] == 9
    assert agent["model"]["max_turns"] == 4
    assert agent["model"]["max_output_tokens"] == 1000
    assert agent["capabilities"]["intent_review"]["max_decision_age_seconds"] == 3


def test_launch_validation_pins_inline_agent_and_verified_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _, _ = _prepared_resources(tmp_path, monkeypatch)
    confirmations = iter((True, False))
    monkeypatch.setattr(
        "kairospy.application.launch.application.wizard.typer.confirm",
        lambda *args, **kwargs: next(confirmations),
    )
    monkeypatch.setattr(
        "kairospy.application.launch.application.wizard.typer.prompt",
        lambda label, default=None, **kwargs: default,
    )
    agent = prompt_agent_config({}, mode="paper", workspace=workspace)
    values = LaunchDraft(
        launch_id="agent-paper",
        mode="paper",
        strategy="builtin:interactive",
        accounts=(),
        execution_enabled=False,
        agent=agent,
    ).apply({})
    path = workspace.paths.launch_config("agent-paper")

    assert build_and_validate(path, values, workspace.paths.root)["valid"] is True
    assert values["agent"]["profile"]["goal"]
    assert "api_key" not in repr(values)

    (workspace.paths.credential_config().parent / "openai-prod.toml").unlink()
    degraded = build_and_validate(path, values, workspace.paths.root)
    assert degraded["valid"] is True
    assert degraded["issues"] == []
    assert any(
        "Agent credential does not exist" in warning for warning in degraded["warnings"]
    )
    assert degraded["diagnostics"][0]["severity"] == "warning"
    assert "no Agent review" in degraded["diagnostics"][0]["action"]


def test_instance_pins_remote_mcp_credential_identity_and_detects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _, _ = _prepared_resources(tmp_path, monkeypatch)
    monkeypatch.setenv("MCP_TOKEN_A", "remote-secret-a")
    monkeypatch.setenv("MCP_TOKEN_B", "remote-secret-b")
    credentials = CredentialConfigurationApplication(workspace)
    credentials.configure(
        "mcp-context",
        provider="telegram",
        role="mcp-auth",
        fields={"bot_token": SecretRef("env", "MCP_TOKEN_A")},
    )
    confirmations = iter((True, False))
    monkeypatch.setattr(
        "kairospy.application.launch.application.wizard.typer.confirm",
        lambda *args, **kwargs: next(confirmations),
    )
    monkeypatch.setattr(
        "kairospy.application.launch.application.wizard.typer.prompt",
        lambda label, default=None, **kwargs: default,
    )
    agent = prompt_agent_config({}, mode="paper", workspace=workspace)
    agent["mcp"] = [
        {
            "id": "context",
            "transport": "streamable_http",
            "url": "https://mcp.example.test",
            "credential": "mcp-context",
            "allowed_tools": ["account.get_position"],
            "scope_enforced": True,
            "required": True,
        }
    ]
    values = LaunchDraft(
        launch_id="mcp-paper",
        mode="paper",
        strategy="builtin:interactive",
        accounts=(),
        execution_enabled=False,
        agent=agent,
    ).apply({})
    path = workspace.paths.launch_config("mcp-paper")
    build_and_validate(path, values, workspace.paths.root)

    environment = LaunchConfigurationApplication().environment(
        path, workspace_root=workspace.paths.root, instance_id="run-1"
    )
    normalized = json.loads(
        environment.normalized_config_path.read_text(encoding="utf-8")
    )
    snapshot = normalized["resource_snapshots"]["mcp_credentials"]["mcp-context"]
    assert snapshot["secret_refs"] == {
        "bot_token": {"source": "env", "id": "MCP_TOKEN_A"}
    }
    assert snapshot["required"] is True
    assert "remote-secret-a" not in json.dumps(normalized)

    credentials.configure(
        "mcp-context",
        provider="telegram",
        role="mcp-auth",
        fields={"bot_token": SecretRef("env", "MCP_TOKEN_B")},
        overwrite=True,
    )
    drift = LaunchConfigurationApplication().instance_resource_drift(
        environment.normalized_config_path, workspace_root=workspace.paths.root
    )
    assert drift["valid"] is False
    assert drift["issues"] == [
        {
            "resource": "mcp_credentials:mcp-context",
            "severity": "blocker",
            "reason": "configuration hash changed",
        }
    ]


def test_launch_agent_guide_preserves_enabled_intent_until_model_is_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    monkeypatch.setattr(
        "kairospy.application.launch.application.wizard.typer.confirm",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "kairospy.application.launch.application.wizard.typer.prompt",
        lambda label, default=None, **kwargs: default,
    )

    agent = prompt_agent_config({}, mode="paper", workspace=workspace)

    assert agent["enabled"] is True
    assert agent["required"] is True
    assert "model" not in agent


def test_launch_edit_expands_legacy_workspace_profile_and_mcp_into_inline_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _, _ = _prepared_resources(tmp_path, monkeypatch)
    profile = workspace.paths.agent_profiles_root() / "legacy.toml"
    profile.parent.mkdir(parents=True)
    profile.write_text(
        """[profile]
id = "legacy"
version = "1"
goal = "Review legacy intents"
rubric = ["Prefer bounded risk"]
invalidation_rules = ["Abstain without context"]
reason_codes = []
risk_flags = []
""",
        encoding="utf-8",
    )
    workspace.paths.agent_mcp_config().write_text(
        """[servers.context]
transport = "stdio"
command = "kairos-agent-mcp"
args = ["serve"]

[profiles.readonly]
server = "context"
allowed_tools = ["account.get_position"]
scope_enforced = true
max_result_bytes = 65536
max_rows = 200
""",
        encoding="utf-8",
    )
    confirmations = iter((True, False, False))
    monkeypatch.setattr(
        "kairospy.application.launch.application.wizard.typer.confirm",
        lambda *args, **kwargs: next(confirmations),
    )
    monkeypatch.setattr(
        "kairospy.application.launch.application.wizard.typer.prompt",
        lambda label, default=None, **kwargs: default,
    )
    current = {
        "agent": {
            "enabled": True,
            "runtime": "openai-agents",
            "profile": "legacy",
            "model": {
                "provider": "openai",
                "model": MODEL,
                "credential": "openai-prod",
            },
            "mcp": [{"server": "context", "profile": "readonly"}],
        }
    }

    migrated = prompt_agent_config(current, mode="paper", workspace=workspace)

    assert migrated["profile"]["goal"] == "Review legacy intents"
    assert migrated["mcp"][0]["id"] == "context"
    assert migrated["mcp"][0]["allowed_tools"] == ["account.get_position"]
    assert "server" not in migrated["mcp"][0]
    with pytest.raises(ValueError, match="Launch-owned"):
        AgentResourceApplication(workspace).create_profile(
            "new",
            goal="goal",
            rubric=("r",),
            invalidation_rules=("i",),
        )
