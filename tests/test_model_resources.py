from __future__ import annotations

from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from kairospy.strategy.apps.agent.application import (
    AvailableModelApplication,
    ModelEndpointApplication,
    ModelResourceMigrationApplication,
)
from kairospy.strategy.apps.agent.application.model_connections import (
    ModelProviderConnectionApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication


def _workspace(tmp_path: Path):
    return WorkspaceApplication().init(tmp_path / "workspace", workspace_id="models")


def test_endpoint_and_available_model_have_independent_files_and_verification(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    endpoint = ModelEndpointApplication(workspace).configure(
        "ollama-local", provider="ollama"
    )
    model = AvailableModelApplication(workspace).configure(
        "local-reasoning",
        endpoint_id="ollama-local",
        provider_model="qwen3:8b",
    )

    assert endpoint["endpoint_id"] == "ollama-local"
    assert "models" not in endpoint
    assert model["model_id"] == "local-reasoning"
    assert model["endpoint_id"] == "ollama-local"
    assert (
        workspace.paths.model_endpoints_root().joinpath("ollama-local.toml").is_file()
    )
    assert (
        workspace.paths.available_models_root()
        .joinpath("local-reasoning.toml")
        .is_file()
    )

    tested = AvailableModelApplication(workspace).converse(
        "local-reasoning",
        "你好",
        probe=lambda endpoint, secret, provider_model, message: {
            "choices": [{"message": {"content": "你好，我可以工作。"}}]
        },
    )

    assert tested["response"] == "你好，我可以工作。"
    assert tested["verification_status"] == "verified"
    evidence = workspace.paths.child(
        "state", "configuration", "ai-models", "local-reasoning.json"
    ).read_text(encoding="utf-8")
    assert "你好" not in evidence
    assert "我可以工作" not in evidence


def test_conversation_failure_returns_safe_provider_diagnostics(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    ModelEndpointApplication(workspace).configure("remote", provider="ollama")
    models = AvailableModelApplication(workspace)
    models.configure("reasoning", endpoint_id="remote", provider_model="gpt-5.4")

    def forbidden(*_args: object) -> object:
        raise HTTPError(
            "https://provider.example/v1/responses",
            403,
            "Forbidden",
            {},
            BytesIO(b"error code: 1010"),
        )

    result = models.converse("reasoning", "你好", probe=forbidden)

    assert result["succeeded"] is False
    assert result["error_category"] == "authentication_or_permission"
    assert result["error_detail"] == "HTTP 403 Forbidden · error code: 1010"
    evidence = workspace.paths.child(
        "state", "configuration", "ai-models", "reasoning.json"
    ).read_text(encoding="utf-8")
    assert "error code: 1010" not in evidence


def test_conversation_failure_redacts_the_endpoint_secret(tmp_path: Path) -> None:
    app = ModelProviderConnectionApplication(_workspace(tmp_path))
    secret = "sk-provider-secret"

    def rejected(*_args: object) -> object:
        raise RuntimeError(f"provider echoed {secret}")

    result = app.converse_config(
        {
            "api_mode": "openai-chat-completions",
            "base_url": "https://provider.example/v1",
            "timeout_seconds": 1.0,
        },
        "gpt-5.4",
        "你好",
        secret=secret,
        probe=rejected,
    )

    assert result["error_detail"] == "RuntimeError: provider echoed [REDACTED]"
    assert secret not in repr(result)


def test_model_http_client_identifies_itself_to_provider_waf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed_headers: dict[str, str | None] = {}

    class Response(BytesIO):
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def open_request(request: Request, *, timeout: float) -> Response:
        observed_headers["user_agent"] = request.get_header("User-agent")
        observed_headers["accept"] = request.get_header("Accept")
        assert timeout == 60.0
        return Response(b'{"choices":[{"message":{"content":"OK"}}]}')

    monkeypatch.setattr(
        "kairospy.strategy.apps.agent.application.model_connections.urlopen",
        open_request,
    )
    app = ModelProviderConnectionApplication(_workspace(tmp_path))

    result = app.converse_config(
        {
            "api_mode": "openai-chat-completions",
            "base_url": "https://provider.example/v1",
            "timeout_seconds": 60.0,
        },
        "gpt-5.4",
        "hello",
        secret="secret",
    )

    assert result["succeeded"] is True
    assert observed_headers == {
        "user_agent": "Kairos/1.0",
        "accept": "application/json",
    }


def test_endpoint_change_requires_model_retest(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    endpoints = ModelEndpointApplication(workspace)
    endpoints.configure("local", provider="ollama")
    models = AvailableModelApplication(workspace)
    models.configure("reasoning", endpoint_id="local", provider_model="qwen3:8b")
    models.test("reasoning", probe=lambda *_args: {"ok": True})

    endpoints.configure(
        "local",
        provider="ollama",
        base_url="http://127.0.0.1:22434/v1",
        overwrite=True,
    )

    assert models.show("reasoning")["verification_status"] == "retest_required"


def test_endpoint_delete_is_blocked_while_model_references_it(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    endpoints = ModelEndpointApplication(workspace)
    endpoints.configure("local", provider="ollama")
    AvailableModelApplication(workspace).configure(
        "reasoning", endpoint_id="local", provider_model="qwen3:8b"
    )

    with pytest.raises(ValueError, match="referenced"):
        endpoints.delete("local")


def test_catalog_forbidden_does_not_block_manual_model_configuration(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    endpoints = ModelEndpointApplication(workspace)
    endpoints.configure(
        "gateway",
        provider="ollama",
        api_mode="openai-responses",
        base_url="https://example.com/v1",
    )

    def forbidden(*_args: object) -> tuple[dict[str, object], ...]:
        raise PermissionError("HTTP Error 403: Forbidden")

    with pytest.raises(PermissionError, match="403"):
        endpoints.discover_models("gateway", probe=forbidden)

    model = AvailableModelApplication(workspace).configure(
        "primary", endpoint_id="gateway", provider_model="gpt-5.6-sol"
    )
    assert model["configured"] is True
    assert endpoints.show("gateway")["configured"] is True


def test_available_model_id_accepts_provider_style_dot(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    ModelEndpointApplication(workspace).configure("gateway", provider="ollama")

    model = AvailableModelApplication(workspace).configure(
        "gpt5.5", endpoint_id="gateway", provider_model="gpt5.5"
    )

    assert model["model_id"] == "gpt5.5"
    assert model["provider_model"] == "gpt5.5"
    assert workspace.paths.available_models_root().joinpath("gpt5.5.toml").is_file()

    with pytest.raises(ValueError, match="path-safe identifier"):
        AvailableModelApplication(workspace).configure(
            "gpt5.", endpoint_id="gateway", provider_model="gpt5."
        )


def test_legacy_migration_is_idempotent_and_preserves_verified_model(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    legacy = ModelProviderConnectionApplication(workspace)
    legacy.configure("local", provider="ollama", models=("qwen3:8b",))
    legacy.test("local", "qwen3:8b", probe=lambda *_args: {"ok": True})
    migration = ModelResourceMigrationApplication(workspace)

    first = migration.migrate_legacy()
    second = migration.migrate_legacy()

    assert first["created_endpoints"] == ["local"]
    assert len(first["created_models"]) == 1
    assert second == {
        "status": "migrated",
        "created_endpoints": [],
        "created_models": [],
    }
    migrated = AvailableModelApplication(workspace).list()
    assert len(migrated) == 1
    assert migrated[0]["provider_model"] == "qwen3:8b"
    assert migrated[0]["verification_status"] == "verified"
