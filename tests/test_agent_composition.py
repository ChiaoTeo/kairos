from __future__ import annotations

from pathlib import Path

import pytest

from kairospy.application.account import AccountApplication
from kairospy.application.agent.composition import compose_agent
from kairospy.application.agent.configuration import AgentLaunchConfig
from kairospy.application.agent.services.controlled_execution import (
    AgentControlledExecutionCommands,
)
from kairospy.application.workspace import WorkspaceApplication


def _config(*, required: bool = True) -> AgentLaunchConfig:
    return AgentLaunchConfig.from_mapping(
        {
            "enabled": True,
            "required": required,
            "runtime": "fixture",
            "profile": "review-v1",
            "fixture_path": "decisions.jsonl",
            "capabilities": {
                "intent_review": {
                    "initial_mode": "gate",
                    "strategy_selectable_modes": ["shadow", "revise"],
                    "operations": ["target_position"],
                    "revisions": {"allow_quantity_reduction": True},
                }
            },
        },
        launch_mode="backtest",
    )


def _resources(tmp_path: Path):
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ws")
    (workspace.paths.agent_profiles_root() / "review-v1.toml").write_text(
        """[profile]
id = "review-v1"
version = "1"
goal = "Review the candidate against the supplied context."
rubric = ["Prefer bounded risk", "Use fresh evidence"]
invalidation_rules = ["Abstain when evidence is unavailable"]
reason_codes = ["safe", "risk"]
risk_flags = ["concentration"]
""",
        encoding="utf-8",
    )
    (workspace.paths.project_root / "decisions.jsonl").write_text("", encoding="utf-8")
    return workspace


def test_fixture_agent_composition_starts_worker_and_decorates_commands(
    tmp_path: Path,
) -> None:
    workspace = _resources(tmp_path)
    composition = compose_agent(
        workspace=workspace,
        instance=workspace.instance("backtest", "launch", "instance"),
        config=_config(),
    )

    decorated = composition.decorate_commands(
        object(), launch_id="launch", account=AccountApplication({})
    )

    assert isinstance(decorated, AgentControlledExecutionCommands)
    assert composition.profile_hash not in {"disabled", "unavailable"}
    assert composition.application._health().state == "ready"
    composition.close()


def test_optional_agent_resource_failure_is_degraded_and_cannot_enter_gate(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ws")
    composition = compose_agent(
        workspace=workspace,
        instance=workspace.instance("backtest", "launch", "instance"),
        config=_config(required=False),
    )

    assert composition.worker is None
    assert composition.application._health().state == "degraded"
    assert composition.application.set_mode("revise").status.value == "rejected"


def test_required_agent_resource_failure_prevents_startup(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ws")

    with pytest.raises(FileNotFoundError):
        compose_agent(
            workspace=workspace,
            instance=workspace.instance("backtest", "launch", "instance"),
            config=_config(required=True),
        )


def test_paper_agent_must_start_in_shadow() -> None:
    with pytest.raises(ValueError, match="initial_mode must be shadow"):
        AgentLaunchConfig.from_mapping(
            {
                "enabled": True,
                "runtime": "openai-agents",
                "profile": "review-v1",
                "model": {
                    "provider": "openai",
                    "model": "model-snapshot",
                    "credential": "agent-key",
                },
                "capabilities": {
                    "intent_review": {
                        "initial_mode": "gate",
                        "operations": ["target_position"],
                        "revisions": {},
                    }
                },
            },
            launch_mode="paper",
        )
