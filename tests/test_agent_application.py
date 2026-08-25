from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
from pathlib import Path
from typing import Mapping

import pytest

from kairospy.strategy.apps.agent.application import (
    AgentApplication,
    AgentContextDocument,
    AgentContextStatus,
    AgentLaunchConfig,
    AgentMode,
    AgentModeStatus,
    AgentModelConfig,
    DecisionKind,
    DecisionResult,
    DecisionStatus,
    IntentCandidate,
    ToolEvidence,
)
from kairospy.strategy.apps.agent.services import DecisionRecordStore
from kairospy.system.apps.launch.application.configuration import LaunchConfig
from kairospy.system.apps.launch.application.strategy_runtime import (
    StrategyLaunchConfig,
)


CAPABILITY = "execution.intent_review"


def test_strategy_visible_agent_surface_is_context_and_mode_commands() -> None:
    commands = {
        name
        for name, value in inspect.getmembers(AgentApplication, inspect.isfunction)
        if not name.startswith("_")
    }
    assert commands == {"publish_context", "remove_context", "set_mode"}


def test_context_replace_dedupe_remove_and_immutable_snapshot() -> None:
    agent = AgentApplication(
        enabled=True,
        initial_mode=AgentMode.SHADOW,
        selectable_modes=(AgentMode.GATE,),
    )
    event_time = datetime(2026, 8, 18, 1, 2, tzinfo=timezone.utc)
    agent._bind_event(42, event_time)
    values = {"hypothesis": "liquidity", "nested": {"score": 2.4}}
    document = AgentContextDocument("signal", (CAPABILITY,), values)

    first = agent.publish_context(document)
    values["hypothesis"] = "mutated"
    duplicate = agent.publish_context(document)
    snapshot = agent._snapshot(CAPABILITY, required_contexts=("signal",))

    assert first.status is AgentContextStatus.ACCEPTED
    assert duplicate.status is AgentContextStatus.DUPLICATE
    assert duplicate.revision == first.revision
    assert snapshot.context_watermark == first.revision
    assert snapshot.documents[0].observed_at == event_time
    assert snapshot.documents[0].source_event_sequence == 42
    assert snapshot.documents[0].values["hypothesis"] == "liquidity"

    replacement = agent.publish_context(
        AgentContextDocument("signal", (CAPABILITY,), {"hypothesis": "regime"})
    )
    assert replacement.revision > first.revision
    assert agent.remove_context("signal").status is AgentContextStatus.REMOVED
    assert agent.remove_context("signal").status is AgentContextStatus.DUPLICATE
    with pytest.raises(ValueError, match="unsupported characters"):
        agent.remove_context("invalid/context")


def test_context_scope_expiry_required_and_secret_bounds() -> None:
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    agent = AgentApplication(enabled=True)
    agent.publish_context(
        AgentContextDocument(
            "expired",
            (CAPABILITY,),
            {"value": 1},
            observed_at=now - timedelta(seconds=2),
            expires_at=now - timedelta(seconds=1),
        )
    )
    agent.publish_context(AgentContextDocument("other", ("research",), {"value": 2}))

    snapshot = agent._snapshot(CAPABILITY, now=now)
    assert snapshot.documents == ()
    with pytest.raises(ValueError, match="missing or expired"):
        agent._snapshot(CAPABILITY, now=now, required_contexts=("expired",))
    with pytest.raises(ValueError, match="credential-like"):
        AgentContextDocument("bad", (CAPABILITY,), {"api_key": "secret"})
    with pytest.raises(ValueError, match="4096"):
        AgentContextDocument("large", (CAPABILITY,), {"value": "x" * 4097})


def test_mode_changes_are_authorized_and_snapshot_pinned() -> None:
    agent = AgentApplication(
        enabled=True,
        initial_mode=AgentMode.SHADOW,
        selectable_modes=(AgentMode.SHADOW, AgentMode.GATE),
    )
    before = agent._snapshot(CAPABILITY)
    rejected = agent.set_mode(AgentMode.REVISE)
    accepted = agent.set_mode(AgentMode.GATE)
    duplicate = agent.set_mode(AgentMode.GATE)
    after = agent._snapshot(CAPABILITY)

    assert rejected.status is AgentModeStatus.REJECTED
    assert accepted.status is AgentModeStatus.ACCEPTED
    assert accepted.revision == 1
    assert duplicate.status is AgentModeStatus.DUPLICATE
    assert before.mode is AgentMode.SHADOW and before.mode_revision == 0
    assert after.mode is AgentMode.GATE and after.mode_revision == 1

    disabled = AgentApplication.disabled().set_mode(AgentMode.GATE)
    assert disabled.status is AgentModeStatus.REJECTED


def test_launch_normalizes_agent_without_secret_material(tmp_path: Path) -> None:
    config = LaunchConfig.from_values(
        {
            "launch": {
                "id": "agent-paper",
                "mode": "paper",
                "strategy": "strategies.agent:Strategy",
            },
            "execution": {"enabled": False},
            "agent": {
                "enabled": True,
                "required": False,
                "runtime": "openai-agents",
                "profile": {
                    "version": "1",
                    "goal": "Review mean-reversion intents",
                    "rubric": ["Prefer bounded exposure"],
                    "invalidation_rules": ["Abstain without fresh evidence"],
                    "reason_codes": ["safe"],
                    "risk_flags": ["concentration"],
                },
                "max_queue_size": 64,
                "model": {
                    "provider": "openai",
                    "model": "gpt-5.4-2026-03-05",
                    "credential": "openai-agent",
                },
                "capabilities": {
                    "intent_review": {
                        "initial_mode": "shadow",
                        "strategy_selectable_modes": ["shadow", "gate"],
                        "operations": ["target_position"],
                        "required_contexts": ["signal"],
                    }
                },
                "mcp": [
                    {
                        "id": "kairos-context",
                        "transport": "stdio",
                        "command": "kairos-context-mcp",
                        "args": ["--readonly"],
                        "timeout_seconds": 2,
                        "allowed_tools": ["market.get_latest_quote"],
                        "scope_enforced": True,
                        "max_result_bytes": 4096,
                        "max_rows": 10,
                        "freshness_required_tools": ["market.get_latest_quote"],
                        "max_age_seconds": 30,
                        "required": True,
                    }
                ],
            },
        },
        root=tmp_path,
    )

    normalized = config.plan().normalized()
    agent = normalized["agent"]
    assert agent["enabled"] is True
    assert agent["capabilities"]["intent_review"]["initial_mode"] == "shadow"
    assert agent["capabilities"]["intent_review"]["strategy_selectable_modes"] == [
        "shadow",
        "gate",
    ]
    assert agent["model"]["connection"] == "openai-agent"
    assert "secret" not in repr(agent).lower()
    assert normalized["agent_profile"]["goal"] == "Review mean-reversion intents"
    assert len(normalized["agent_profile"]["content_hash"]) == 64
    assert len(normalized["agent_mcp"]["content_hash"]) == 64

    path = tmp_path / "normalized.json"
    path.write_text(__import__("json").dumps(normalized), encoding="utf-8")
    runtime = StrategyLaunchConfig.load(path, launch_id="agent-paper", mode="paper")
    assert runtime.agent.enabled is True
    assert runtime.agent.intent_review is not None
    assert runtime.agent.intent_review.initial_mode is AgentMode.SHADOW
    assert runtime.agent.profile_snapshot is not None
    assert runtime.agent.profile_snapshot["goal"] == "Review mean-reversion intents"
    assert runtime.agent.mcp_snapshot is not None
    entries = runtime.agent.mcp_snapshot["entries"]
    assert isinstance(entries, list)
    assert entries[0]["id"] == "kairos-context"
    assert entries[0]["scope_enforced"] is True


def test_launch_rejects_agent_secrets_and_remote_backtest_runtime(
    tmp_path: Path,
) -> None:
    base = {
        "launch": {
            "id": "bad-agent",
            "mode": "paper",
            "strategy": "strategies.agent:Strategy",
        },
        "execution": {"enabled": False},
    }
    secret = LaunchConfig.from_values(
        base
        | {
            "agent": {
                "enabled": True,
                "profile": {},
                "api_key": "forbidden",
            }
        },
        root=tmp_path,
    )
    assert any("forbidden" in issue for issue in secret.report().issues)

    backtest = LaunchConfig.from_values(
        {
            "launch": {
                "id": "bad-backtest-agent",
                "mode": "backtest",
                "strategy": "strategies.agent:Strategy",
            },
            "execution": {"enabled": False},
            "backtest": {
                "market": {
                    "start": "2026-08-18T00:00:00Z",
                    "end": "2026-08-18T01:00:00Z",
                }
            },
            "agent": {
                "enabled": True,
                "runtime": "openai-agents",
                "profile": {},
            },
        },
        root=tmp_path,
    )
    assert any("must be fixture" in issue for issue in backtest.report().issues)


def test_agent_model_accepts_provider_specific_id_and_rejects_whitespace() -> None:
    value = AgentModelConfig.from_mapping(
        {
            "connection": "ollama-local",
            "model": "qwen3:8b",
        }
    )
    assert value.connection == "ollama-local"
    with pytest.raises(ValueError, match="without whitespace"):
        AgentModelConfig.from_mapping(
            {
                "connection": "ollama-local",
                "model": "qwen 3",
            }
        )


def test_agent_model_normalizes_available_model_ref_and_rejects_mixed_shape() -> None:
    value = AgentModelConfig.from_mapping({"ref": "primary-reasoning"})

    assert value.ref == "primary-reasoning"
    assert value.normalized()["ref"] == "primary-reasoning"
    assert "connection" not in value.normalized()
    with pytest.raises(ValueError, match="cannot be combined"):
        AgentModelConfig.from_mapping(
            {
                "ref": "primary-reasoning",
                "connection": "legacy-endpoint",
                "model": "legacy-model",
            }
        )


def test_agent_config_rejects_unpublishable_required_context_key() -> None:
    with pytest.raises(ValueError, match="unsupported characters"):
        AgentLaunchConfig.from_mapping(
            {
                "enabled": True,
                "runtime": "fixture",
                "profile": {
                    "version": "1",
                    "goal": "Review intents",
                    "rubric": ["bounded"],
                    "invalidation_rules": ["missing context"],
                },
                "fixture_path": "fixtures/agent.jsonl",
                "capabilities": {
                    "intent_review": {
                        "operations": ["target_position"],
                        "required_contexts": ["signal/current"],
                        "revisions": {},
                    }
                },
            },
            launch_mode="backtest",
        )


def test_decision_store_persists_before_run_deduplicates_and_recovers(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    agent = AgentApplication(enabled=True)
    candidate = IntentCandidate(
        decision_id="decision-1",
        request_id="request-1",
        intent_id="intent-1",
        workspace_id="workspace",
        strategy_id="strategy",
        launch_id="launch",
        instance_id="instance",
        operation="target_position",
        request={"instrument_id": "BTCUSDT", "quantity": "1"},
        exposure_effect="unknown",
        profile_hash="profile-hash",
        snapshot=agent._snapshot(CAPABILITY, now=now),
        submitted_at=now,
        deadline=now + timedelta(seconds=5),
    )
    store = DecisionRecordStore(tmp_path / "decisions.sqlite3")

    pending, created = store.admit(candidate)
    duplicate, duplicate_created = store.admit(candidate)
    running = store.mark_running(candidate.decision_id)
    finished = store.finish(
        candidate.decision_id,
        DecisionStatus.APPROVED,
        result=DecisionResult(DecisionKind.APPROVE, 9000, (), (), "approved"),
        tool_evidence=(
            ToolEvidence(
                "market.get_latest_quote",
                "a" * 64,
                "b" * 64,
                "completed",
                "2026-08-18T00:00:00Z",
            ),
        ),
        effective_request=candidate.request,
        final_submission_status="accepted",
        delivery_certainty="sent",
    )

    assert created is True and duplicate_created is False
    assert pending.status is DecisionStatus.PENDING
    assert duplicate.status is DecisionStatus.PENDING
    assert running.status is DecisionStatus.RUNNING
    assert finished.status is DecisionStatus.APPROVED
    assert finished.final_submission_status == "accepted"
    assert store.recent(limit=1) == (finished,)
    row = store._connection.execute(
        """
        SELECT workspace_id, runtime, tool_profiles_json, tool_evidence_json,
               effective_request_hash, latency_millis
        FROM decision_records WHERE decision_id = ?
        """,
        (candidate.decision_id,),
    ).fetchone()
    assert row is not None
    assert row["workspace_id"] == "workspace"
    assert row["runtime"] == "unknown"
    assert row["tool_profiles_json"] == "[]"
    assert "market.get_latest_quote" in row["tool_evidence_json"]
    assert len(row["effective_request_hash"]) == 64
    assert row["latency_millis"] is not None
    store.close()

    reopened = DecisionRecordStore(tmp_path / "decisions.sqlite3")
    assert reopened.decision("decision-1") == finished
    reopened.close()


def test_decision_store_interrupts_only_nonterminal_records(tmp_path: Path) -> None:
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    store = DecisionRecordStore(tmp_path / "decisions.sqlite3")
    snapshot = AgentApplication(enabled=True)._snapshot(CAPABILITY, now=now)
    for index in range(3):
        store.admit(
            IntentCandidate(
                decision_id=f"decision-{index}",
                request_id=f"request-{index}",
                intent_id=f"intent-{index}",
                workspace_id="workspace",
                strategy_id="strategy",
                launch_id="launch",
                instance_id="instance",
                operation="target_position",
                request={"quantity": str(index + 1)},
                exposure_effect="unknown",
                profile_hash="profile-hash",
                snapshot=snapshot,
                submitted_at=now,
                deadline=now + timedelta(seconds=5),
            )
        )
    store.finish("decision-0", DecisionStatus.REJECTED, reason="policy")
    store.mark_running("decision-2")
    store.mark_submitting("decision-2")

    assert store.interrupt_nonterminal() == 2
    rejected = store.decision("decision-0")
    assert rejected is not None
    assert rejected.status is DecisionStatus.REJECTED
    interrupted = store.decision("decision-1")
    assert interrupted is not None
    assert interrupted.status is DecisionStatus.INTERRUPTED
    assert interrupted.delivery_certainty == "not_sent"
    uncertain = store.decision("decision-2")
    assert uncertain is not None
    assert uncertain.status is DecisionStatus.SUBMISSION_INDETERMINATE
    assert uncertain.delivery_certainty == "indeterminate"
    store.close()
