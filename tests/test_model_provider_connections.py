from __future__ import annotations

import json
from pathlib import Path

from kairospy.strategy.apps.agent.application.model_connections import (
    ModelProviderConnectionApplication,
)
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication


def test_hosted_provider_separates_connection_credential_and_model(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ai")
    CredentialConfigurationApplication(workspace).configure(
        "anthropic-work",
        provider="anthropic",
        role="model-inference",
        values={"api_key": "secret-never-record"},
    )
    application = ModelProviderConnectionApplication(workspace)

    connection = application.configure(
        "anthropic-main",
        provider="anthropic",
        credential_id="anthropic-work",
    )
    models = application.discover_models(
        "anthropic-main",
        probe=lambda value, secret: (
            {
                "id": "claude-test",
                "name": "Claude Test",
                "input": ["text", "image"],
                "tool_calling": True,
            },
        ),
    )
    observed: list[tuple[str, str | None, str]] = []
    verification = application.test(
        "anthropic-main",
        "claude-test",
        probe=lambda value, secret, model: observed.append(
            (str(value["api_mode"]), secret, model)
        ),
    )

    assert connection["provider"] == "anthropic"
    assert connection["api_mode"] == "anthropic-messages"
    assert connection["credential_id"] == "anthropic-work"
    assert models[0]["input"] == ["text", "image"]
    assert observed == [("anthropic-messages", "secret-never-record", "claude-test")]
    assert verification["verification_status"] == "verified"
    assert verification["model_ref"] == "anthropic-main/claude-test"
    assert "secret-never-record" not in repr(application.show("anthropic-main"))
    evidence = workspace.paths.child(
        "state", "configuration", "models", "anthropic-main.json"
    ).read_text(encoding="utf-8")
    assert "secret-never-record" not in evidence


def test_local_provider_detection_is_read_only_and_ollama_compat_is_configurable(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ai")
    application = ModelProviderConnectionApplication(workspace)

    detected = application.detect_local(
        probe=lambda provider, _base_url: (
            ({"id": "qwen3:8b"},) if provider == "ollama" else ()
        )
    )

    assert detected == (
        {
            "provider": "ollama",
            "label": "Ollama",
            "base_url": "http://127.0.0.1:11434/v1",
            "models": ["qwen3:8b"],
            "model_count": 1,
        },
        {
            "provider": "lmstudio",
            "label": "LM Studio",
            "base_url": "http://127.0.0.1:1234/v1",
            "models": [],
            "model_count": 0,
        },
    )
    assert workspace.paths.model_connections_root().exists() is False

    connection = application.configure(
        "ollama-local",
        provider="ollama",
        models=("qwen3:8b",),
    )
    assert connection["api_mode"] == "openai-chat-completions"
    assert connection["credential_id"] is None
    assert connection["configured"] is True


def test_connection_change_invalidates_model_verification(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ai")
    application = ModelProviderConnectionApplication(workspace)
    application.configure("local", provider="ollama", base_url="http://127.0.0.1:11434")
    application.test("local", "qwen", probe=lambda *_args: {"ok": True})

    application.configure(
        "local",
        provider="ollama",
        base_url="http://127.0.0.1:22434",
        overwrite=True,
    )

    assert application.verification("local")["verification_status"] == (
        "retest_required"
    )


def test_each_model_keeps_independent_verification_evidence(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ai")
    application = ModelProviderConnectionApplication(workspace)
    application.configure(
        "local",
        provider="ollama",
        models=("qwen3:8b", "deepseek-r1:8b"),
    )

    application.test("local", "qwen3:8b", probe=lambda *_args: {"ok": True})
    application.test("local", "deepseek-r1:8b", probe=lambda *_args: {"ok": True})

    assert (
        application.verification("local", model="qwen3:8b")["verification_status"]
        == "verified"
    )
    assert (
        application.verification("local", model="deepseek-r1:8b")["verification_status"]
        == "verified"
    )
    summary = application.show("local")
    assert summary["verified_models"] == ["deepseek-r1:8b", "qwen3:8b"]
    evidence = json.loads(
        workspace.paths.child(
            "state", "configuration", "models", "local.json"
        ).read_text(encoding="utf-8")
    )
    assert evidence["version"] == 3
    assert set(evidence["verifications"]) == {"qwen3:8b", "deepseek-r1:8b"}


def test_catalog_refresh_does_not_invalidate_models_but_endpoint_change_does(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ai")
    application = ModelProviderConnectionApplication(workspace)
    application.configure("local", provider="ollama", models=("qwen3:8b",))
    application.test("local", "qwen3:8b", probe=lambda *_args: {"ok": True})

    application.configure(
        "local",
        provider="ollama",
        models=("qwen3:8b", "new-model:latest"),
        overwrite=True,
    )
    assert (
        application.verification("local", model="qwen3:8b")["verification_status"]
        == "verified"
    )

    application.configure(
        "local",
        provider="ollama",
        base_url="http://127.0.0.1:22434/v1",
        models=("qwen3:8b", "new-model:latest"),
        overwrite=True,
    )
    assert (
        application.verification("local", model="qwen3:8b")["verification_status"]
        == "retest_required"
    )


def test_version_two_single_model_evidence_remains_readable(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ai")
    application = ModelProviderConnectionApplication(workspace)
    application.configure("local", provider="ollama", models=("qwen3:8b",))
    connection = application._base_summary(application._path("local"))
    legacy = {
        "version": 2,
        "connection_id": "local",
        "provider": "ollama",
        "api_mode": "openai-chat-completions",
        "model": "qwen3:8b",
        "model_ref": "local/qwen3:8b",
        "configuration_hash": application._legacy_configuration_hash(connection),
        "tested_at": "2026-01-01T00:00:00+00:00",
        "succeeded": True,
        "detail": "最小文本响应成功",
        "error_category": None,
        "tested": ["endpoint", "authentication", "minimum text response"],
        "not_tested": [],
        "capabilities": ["text_inference"],
    }
    evidence_path = workspace.paths.child(
        "state", "configuration", "models", "local.json"
    )
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(legacy), encoding="utf-8")

    verification = application.verification("local", model="qwen3:8b")

    assert verification["verification_status"] == "verified"
    assert verification["model_ref"] == "local/qwen3:8b"


def test_custom_openai_compatible_provider_requires_explicit_mode_and_endpoint(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ai")
    CredentialConfigurationApplication(workspace).configure(
        "company-auth",
        provider="custom-model",
        values={"api_key": "custom-secret"},
    )
    application = ModelProviderConnectionApplication(workspace)

    connection = application.configure(
        "company-gateway",
        provider="company",
        api_mode="openai-responses",
        base_url="https://ai.example.com/v1",
        credential_id="company-auth",
        models=("model-a",),
    )

    assert connection["provider"] == "company"
    assert connection["models"] == ["model-a"]
    text = application._path("company-gateway").read_text(encoding="utf-8")
    assert "custom-secret" not in text
    assert json.dumps(connection)
