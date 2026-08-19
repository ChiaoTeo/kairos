from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, cast

import pytest

from kairospy.application.agent import (
    AgentApplication,
    AgentMode,
    DecisionKind,
    DecisionResult,
    IntentCandidate,
)
from kairospy.application.agent.services.fixture_runtime import (
    FixtureDecisionRuntime,
    fixture_key,
)
from kairospy.application.agent.services.openai_runtime import (
    OpenAIDecisionRuntime,
    _ToolLimitHooks,
)
from kairospy.application.execution import TargetPositionRequest


def _candidate() -> IntentCandidate:
    now = datetime.now(timezone.utc)
    return IntentCandidate(
        decision_id="decision",
        request_id="request",
        intent_id="intent",
        strategy_id="strategy",
        launch_id="launch",
        instance_id="instance",
        operation="target_position",
        request=TargetPositionRequest(
            "BTCUSDT", Decimal("1"), account_id="main", intent_id="intent"
        ),
        exposure_effect="unknown",
        profile_hash="profile-hash",
        snapshot=AgentApplication(
            enabled=True, initial_mode=AgentMode.SHADOW
        )._snapshot("execution.intent_review", now=now),
        submitted_at=now,
        deadline=now + timedelta(seconds=5),
    )


def test_fixture_runtime_requires_exact_candidate_context_profile_and_mode(
    tmp_path: Path,
) -> None:
    candidate = _candidate()
    path = tmp_path / "fixtures.jsonl"
    path.write_text(
        json.dumps(
            {
                "fixture_key": fixture_key(candidate),
                "result": {
                    "decision": "approve",
                    "confidence_bps": 9000,
                    "reason_codes": [],
                    "risk_flags": [],
                    "summary": "fixture",
                    "revisions": [],
                },
                "tool_evidence": [
                    {
                        "tool_name": "market.get_latest_quote",
                        "argument_hash": "a" * 64,
                        "result_hash": "b" * 64,
                        "status": "completed",
                        "observed_at": "2026-08-18T00:00:00Z",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    runtime = FixtureDecisionRuntime(path)

    output = runtime.decide(candidate)
    assert output.result.decision is DecisionKind.APPROVE
    assert output.tool_evidence[0].tool_name == "market.get_latest_quote"
    changed = replace(candidate, profile_hash="different-profile")
    with pytest.raises(LookupError, match="does not match"):
        runtime.decide(changed)


def test_openai_runtime_uses_structured_output_and_disables_sensitive_trace(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    expected = DecisionResult(DecisionKind.APPROVE, 9000, (), (), "approved")

    class Provider:
        def __init__(self, **values) -> None:
            captured["provider"] = values

    class Settings:
        def __init__(self, **values) -> None:
            captured["settings"] = values

    class Config:
        def __init__(self, **values) -> None:
            captured["run_config"] = values

    class Agent:
        def __init__(self, **values) -> None:
            captured["agent"] = values

    class Runner:
        @staticmethod
        async def run(agent, model_input, **values):
            captured["input"] = model_input
            captured["run"] = values

            class ToolCallItem:
                raw_item = SimpleNamespace(
                    call_id="call-1",
                    name="market.get_latest_quote",
                    arguments='{"instrument_id":"BTCUSDT"}',
                )

            class ToolCallOutputItem:
                raw_item = SimpleNamespace(call_id="call-1")
                output = {
                    "observed_at": "2026-08-18T00:00:00Z",
                    "bid": "1",
                }

            return SimpleNamespace(
                final_output=expected,
                new_items=[ToolCallItem(), ToolCallOutputItem()],
            )

    sdk = SimpleNamespace(
        OpenAIProvider=Provider,
        ModelSettings=Settings,
        RunConfig=Config,
        Agent=Agent,
        Runner=Runner,
    )
    monkeypatch.setattr(
        "kairospy.application.agent.services.openai_runtime.importlib.import_module",
        lambda name: sdk,
    )
    runtime = OpenAIDecisionRuntime(
        instructions="Review risk",
        model="pinned-model",
        api_key="not-logged",
        max_turns=4,
        max_tool_calls=3,
        max_input_tokens=10_000,
        max_output_tokens=1000,
        request_timeout_seconds=2,
    )

    output = runtime.decide(_candidate())
    assert output.result is expected
    assert output.tool_evidence[0].tool_name == "market.get_latest_quote"
    assert output.tool_evidence[0].argument_hash is not None
    assert output.tool_evidence[0].result_hash is not None
    assert output.tool_evidence[0].observed_at == "2026-08-18T00:00:00Z"
    agent_values = cast(Mapping[str, object], captured["agent"])
    run_config = cast(Mapping[str, object], captured["run_config"])
    settings = cast(Mapping[str, object], captured["settings"])
    assert agent_values["output_type"] is DecisionResult
    assert run_config["trace_include_sensitive_data"] is False
    assert settings["store"] is False
    assert "hooks" in cast(Mapping[str, object], captured["run"])
    assert "not-logged" not in str(captured["input"])


def test_openai_runtime_enforces_tool_call_budget() -> None:
    hooks = _ToolLimitHooks(1)

    asyncio.run(hooks.on_tool_start())
    with pytest.raises(RuntimeError, match="max_tool_calls"):
        asyncio.run(hooks.on_tool_start())
