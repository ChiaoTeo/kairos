from __future__ import annotations

from pathlib import Path

import pytest

from kairospy.strategy.apps.agent.application.model_connections import (
    ModelProviderConnectionApplication,
)
from kairospy.strategy.apps.agent.application import (
    AvailableModelApplication,
    ModelEndpointApplication,
)
from kairospy.system.apps.configuration.application import (
    ConfigurationReferenceApplication,
    WorkspaceResourceLifecycleApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication


def test_resource_reference_impact_blocks_delete_and_marks_document_state(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="refs")
    launch = workspace.paths.config / "launches" / "live.toml"
    launch.parent.mkdir(parents=True, exist_ok=True)
    launch.write_text(
        '[launch]\nid = "live"\nmode = "live"\n\n'
        '[agent.model]\nconnection = "gateway"\nmodel = "model-a"\n',
        encoding="utf-8",
    )
    application = ConfigurationReferenceApplication(workspace)

    impact = application.deletion_impact("ai_model", "gateway")

    assert impact["allowed"] is False
    assert impact["reference_count"] == 1
    assert impact["references"] == [
        {
            "source": "config/launches/live.toml",
            "location": "agent.model.connection",
            "document_state": "published",
        }
    ]
    assert application.deletion_impact("notification", "unused")["allowed"] is True
    with pytest.raises(ValueError, match="unsupported resource kind"):
        application.resource_references("unknown", "id")


def test_workspace_resource_lifecycle_refuses_referenced_delete_by_default(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="refs")
    ModelProviderConnectionApplication(workspace).configure(
        "local", provider="ollama", models=("qwen3:8b",)
    )
    launch = workspace.paths.config / "launches" / "paper.toml"
    launch.parent.mkdir(parents=True, exist_ok=True)
    launch.write_text(
        '[launch]\nid = "paper"\nmode = "paper"\n\n'
        '[agent.model]\nconnection = "local"\nmodel = "qwen3:8b"\n',
        encoding="utf-8",
    )
    lifecycle = WorkspaceResourceLifecycleApplication(workspace)

    with pytest.raises(ValueError, match="is referenced"):
        lifecycle.delete("ai_model", "local")
    assert ModelProviderConnectionApplication(workspace).show("local")

    deleted = lifecycle.delete("ai_model", "local", force=True)
    assert deleted["status"] == "deleted"
    assert deleted["forced"] is True
    assert deleted["orphaned_references"][0]["source"] == ("config/launches/paper.toml")


def test_available_model_and_endpoint_reference_chain_protects_deletion(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="refs")
    ModelEndpointApplication(workspace).configure("local", provider="ollama")
    AvailableModelApplication(workspace).configure(
        "reasoning", endpoint_id="local", provider_model="qwen3:8b"
    )
    launch = workspace.paths.config / "launches" / "paper.toml"
    launch.parent.mkdir(parents=True, exist_ok=True)
    launch.write_text(
        '[launch]\nid = "paper"\nmode = "paper"\n\n[agent.model]\nref = "reasoning"\n',
        encoding="utf-8",
    )
    references = ConfigurationReferenceApplication(workspace)
    lifecycle = WorkspaceResourceLifecycleApplication(workspace)

    assert references.available_model_references("reasoning")[0]["location"] == (
        "agent.model.ref"
    )
    endpoint_reference = references.model_endpoint_references("local")[0]
    assert endpoint_reference["source"] == "config/ai/models/reasoning.toml"
    with pytest.raises(ValueError, match="is referenced"):
        lifecycle.delete("available_model", "reasoning")
    with pytest.raises(ValueError, match="is referenced"):
        lifecycle.delete("model_endpoint", "local")


def test_model_endpoint_is_reported_as_a_credential_reference(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="refs")
    endpoint_path = workspace.paths.model_endpoints_root() / "gateway.toml"
    endpoint_path.parent.mkdir(parents=True, exist_ok=True)
    endpoint_path.write_text(
        '[endpoint]\nid = "gateway"\nprovider = "custom"\n'
        'api_mode = "openai-responses"\nbase_url = "https://example.com/v1"\n'
        'credential_id = "gateway-auth"\n',
        encoding="utf-8",
    )

    references = ConfigurationReferenceApplication(workspace).credential_references(
        "gateway-auth"
    )

    assert references == [
        {
            "source": "config/ai/endpoints/gateway.toml",
            "location": "endpoint.credential_id",
            "document_state": "published",
        }
    ]
