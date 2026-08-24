from __future__ import annotations

from pathlib import Path

from kairospy.system.apps.launch.application.readiness import (
    OperationEffect,
    ResourceErrorCategory,
    ResourceKind,
    ResourceState,
    RunReadinessApplication,
    normalize_error_category,
    project_resource_readiness,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication
from kairospy.investment.apps.account.application import AccountConfigurationApplication


def test_resource_readiness_has_stable_state_priority_and_next_action() -> None:
    missing = project_resource_readiness(
        ResourceKind.AI_MODEL,
        {
            "connection_id": "gateway",
            "configured": False,
            "enabled": True,
            "verification_status": "failed",
            "issues": ["安全凭据不存在"],
        },
    )
    changed = project_resource_readiness(
        ResourceKind.MARKET_DATA,
        {
            "connection_id": "massive",
            "configured": True,
            "enabled": True,
            "verification_status": "retest_required",
        },
    )
    available = project_resource_readiness(
        ResourceKind.ACCOUNT,
        {
            "account_id": "paper-main",
            "configured": True,
            "enabled": True,
            "verification_status": "verified",
        },
    )

    assert missing.state is ResourceState.MISSING_CREDENTIAL
    assert missing.next_action == "replace_credential"
    assert missing.selectable is False
    assert changed.state is ResourceState.CONFIGURATION_CHANGED
    assert changed.next_action == "retest_connection"
    assert available.state is ResourceState.AVAILABLE
    assert available.selectable is True
    assert available.actions[1].effect is OperationEffect.NETWORK_READ
    assert available.actions[1].confirmation_required is False


def test_side_effect_descriptors_are_stable_for_workbench_confirmation() -> None:
    model = project_resource_readiness(
        ResourceKind.AI_MODEL,
        {
            "connection_id": "gateway",
            "configured": True,
            "verification_status": "pending",
        },
    )
    notification = project_resource_readiness(
        ResourceKind.NOTIFICATION,
        {
            "destination_id": "ops",
            "configured": True,
            "verification_status": "pending",
        },
    )

    assert model.actions[1].as_dict() == {
        "action_id": "test_connection",
        "effect": "billable_model_call",
        "probe_level": "external_effect",
        "confirmation_required": True,
        "destructive": False,
    }
    assert notification.actions[1].effect is OperationEffect.EXTERNAL_MESSAGE


def test_error_categories_normalize_legacy_provider_results() -> None:
    assert normalize_error_category("network_unavailable") is (
        ResourceErrorCategory.NETWORK_FAILED
    )
    assert normalize_error_category("authentication_or_permission") is (
        ResourceErrorCategory.AUTHENTICATION_FAILED
    )
    assert normalize_error_category("future-provider-error") is (
        ResourceErrorCategory.UNKNOWN
    )


def test_overview_treats_missing_notification_as_optional_and_required_kinds_as_input(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="ready"
    )
    application = RunReadinessApplication(workspace)

    default = application.overview()
    required = application.overview(
        required_kinds=(ResourceKind.ACCOUNT, ResourceKind.MARKET_DATA)
    )

    assert default["ready"] is True
    assert default["groups"]["notification"]["optional"] is True
    assert default["needs_action"] == 0
    assert required["ready"] is False
    assert required["missing_required_kinds"] == ["account", "market_data"]


def test_readiness_includes_published_and_draft_launch_references(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="ready"
    )
    account = AccountConfigurationApplication(workspace)
    account.simulate("paper-main")
    account.test_connection("paper-main")
    published = workspace.paths.config / "launches" / "published.toml"
    draft = workspace.paths.config / "launches" / ".drafts" / "working.toml"
    published.parent.mkdir(parents=True, exist_ok=True)
    draft.parent.mkdir(parents=True, exist_ok=True)
    content = '[launch]\nid = "run"\nmode = "paper"\n\n[account]\nref = "paper-main"\n'
    published.write_text(content, encoding="utf-8")
    draft.write_text(content, encoding="utf-8")

    readiness = RunReadinessApplication(workspace).resources()[0]

    assert readiness.selectable is True
    assert {value["document_state"] for value in readiness.references} == {
        "published",
        "draft",
    }
    assert readiness.as_dict()["reference_count"] == 2
