from __future__ import annotations

from pathlib import Path
from decimal import Decimal

import pytest

from kairospy.application.account import (
    AccountApplication,
    AccountSegmentSnapshot,
    AccountSnapshot,
    DataFreshness,
    Position,
    SegmentCompleteness,
    SPOT,
)
from kairospy.application.agent.composition import _exposure_classifier, compose_agent
from kairospy.application.agent.configuration import AgentLaunchConfig
from kairospy.application.agent.services.controlled_execution import (
    AgentControlledExecutionCommands,
    UnavailableAgentExecutionCommands,
)
from kairospy.application.agent.services.tools import AgentToolScope
from kairospy.application.workspace import WorkspaceApplication
from kairospy.domain_types import AccountId, InstrumentId
from kairospy.application.reference import InstrumentRef
from kairospy.application.execution import TargetPositionRequest


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


def _scope() -> AgentToolScope:
    return AgentToolScope("ws", "launch", "instance", "strategy", ("main",))


def test_fixture_agent_composition_starts_worker_and_decorates_commands(
    tmp_path: Path,
) -> None:
    workspace = _resources(tmp_path)
    composition = compose_agent(
        workspace=workspace,
        instance=workspace.instance("backtest", "launch", "instance"),
        config=_config(),
        tool_scope=_scope(),
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
        tool_scope=_scope(),
    )

    assert composition.worker is None
    assert composition.application._health().state == "degraded"
    assert composition.application.set_mode("revise").status.value == "rejected"

    class Commands:
        def __init__(self) -> None:
            self.calls: list[object] = []

        def target_position(self, request, **identity):
            self.calls.append((request, identity))
            return type("Result", (), {"status": "accepted"})()

    commands = Commands()
    decorated = composition.decorate_commands(
        commands,
        launch_id="launch",
        account=AccountApplication({}),
    )
    assert isinstance(decorated, UnavailableAgentExecutionCommands)
    rejected = decorated.target_position(
        TargetPositionRequest("BTCUSDT", Decimal("1"), account_id="main"),
        request_id="request-1",
        strategy_id="strategy",
        instance_id="instance",
    )
    assert rejected.status == "rejected"
    assert rejected.error_code == "agent_runtime_unavailable"
    assert commands.calls == []
    composition.close()


def test_disabled_agent_does_not_import_optional_sdk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ws")

    def unexpected_import(name: str):
        raise AssertionError(f"disabled Agent imported optional SDK: {name}")

    monkeypatch.setattr(
        "kairospy.application.agent.services.openai_runtime.importlib.import_module",
        unexpected_import,
    )
    composition = compose_agent(
        workspace=workspace,
        instance=workspace.instance("paper", "launch", "instance"),
        config=AgentLaunchConfig.disabled(),
        tool_scope=_scope(),
    )
    commands = object()

    assert composition.worker is None
    assert (
        composition.decorate_commands(
            commands,
            launch_id="launch",
            account=AccountApplication({}),
        )
        is commands
    )
    assert composition.application._health().state == "disabled"


def test_required_agent_resource_failure_prevents_startup(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ws")

    with pytest.raises(FileNotFoundError):
        compose_agent(
            workspace=workspace,
            instance=workspace.instance("backtest", "launch", "instance"),
            config=_config(required=True),
            tool_scope=_scope(),
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
                    "model": "gpt-5.4-2026-03-05",
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


def test_exposure_reduction_requires_fresh_complete_account_evidence() -> None:
    instrument = InstrumentRef(InstrumentId("BTCUSDT"), "BTCUSDT")

    class Projection:
        def __init__(self, completeness: SegmentCompleteness) -> None:
            self.completeness = completeness

        def snapshot(self, account_id: AccountId) -> AccountSnapshot:
            return AccountSnapshot(
                account_id,
                (
                    AccountSegmentSnapshot(
                        account_id,
                        SPOT,
                        "paper",
                        "paper",
                        None,
                        Decimal("1000"),
                        (),
                        (Position(account_id, SPOT, instrument, Decimal("2")),),
                        DataFreshness.FRESH,
                        1,
                        completeness=self.completeness,
                    ),
                ),
                1,
            )

    complete = _exposure_classifier(
        AccountApplication(
            {AccountId("main"): Projection(SegmentCompleteness.COMPLETE)}
        )
    )
    partial = _exposure_classifier(
        AccountApplication({AccountId("main"): Projection(SegmentCompleteness.PARTIAL)})
    )

    assert (
        complete(TargetPositionRequest("BTCUSDT", Decimal("1"), account_id="main"))
        == "reduce"
    )
    assert (
        complete(TargetPositionRequest("BTCUSDT", Decimal("0"), account_id="main"))
        == "reduce"
    )
    assert (
        complete(TargetPositionRequest("BTCUSDT", Decimal("3"), account_id="main"))
        == "increase"
    )
    assert (
        partial(TargetPositionRequest("BTCUSDT", Decimal("0"), account_id="main"))
        == "unknown"
    )
