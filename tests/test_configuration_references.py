from __future__ import annotations

from pathlib import Path

import pytest

from kairospy.application.agent.model_connections import (
    ModelProviderConnectionApplication,
)
from kairospy.application.config import (
    ConfigurationReferenceApplication,
    WorkspaceResourceLifecycleApplication,
)
from kairospy.application.workspace import WorkspaceApplication


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
    assert deleted["orphaned_references"][0]["source"] == (
        "config/launches/paper.toml"
    )
