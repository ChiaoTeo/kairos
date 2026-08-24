from __future__ import annotations

import json
from pathlib import Path

from kairospy.application.agent.model_connections import (
    ModelProviderConnectionApplication,
)
from kairospy.application.workspace.credentials import (
    CredentialConfigurationApplication,
    SecretRef,
)
from kairospy.application.workspace import WorkspaceApplication


def test_hosted_provider_separates_connection_credential_and_model(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ai")
    monkeypatch.setenv("ANTHROPIC_KAIROS_KEY", "secret-never-record")
    CredentialConfigurationApplication(workspace).configure(
        "anthropic-work",
        provider="anthropic",
        role="model-inference",
        fields={"api_key": SecretRef("env", "ANTHROPIC_KAIROS_KEY")},
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


def test_custom_openai_compatible_provider_requires_explicit_mode_and_endpoint(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ai")
    monkeypatch.setenv("CUSTOM_MODEL_KEY", "custom-secret")
    CredentialConfigurationApplication(workspace).configure(
        "company-auth",
        provider="custom-model",
        fields={"api_key": SecretRef("env", "CUSTOM_MODEL_KEY")},
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
