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

from kairospy.strategy.apps.agent.application import (
    AgentApplication,
    AgentMode,
    DecisionKind,
    DecisionResult,
    IntentCandidate,
)
from kairospy.strategy.apps.agent.services.fixture_runtime import (
    FixtureDecisionRuntime,
    fixture_key,
)
from kairospy.strategy.apps.agent.services.openai_runtime import (
    ModelDecisionRuntime,
    OpenAIDecisionRuntime,
    _ToolLimitHooks,
    _tool_evidence,
)
from kairospy.strategy.apps.agent.services.tools import (
    MCPServerBinding,
    MCPToolPolicy,
)
from kairospy.investment.apps.execution.application import (
    ImmediateAlgorithm,
    TargetPositionRequest,
)


def _candidate() -> IntentCandidate:
    now = datetime.now(timezone.utc)
    return IntentCandidate(
        decision_id="decision",
        request_id="request",
        intent_id="intent",
        workspace_id="workspace",
        strategy_id="strategy",
        launch_id="launch",
        instance_id="instance",
        operation="target_position",
        request=TargetPositionRequest(
            "BTCUSDT",
            Decimal("1"),
            algorithm=ImmediateAlgorithm(),
            account_id="main",
            intent_id="intent",
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
    changed_snapshot = replace(
        candidate,
        snapshot=replace(candidate.snapshot, mode_revision=1),
    )
    with pytest.raises(LookupError, match="does not match"):
        runtime.decide(changed_snapshot)
    changed_strategy = replace(candidate, strategy_id="different-strategy")
    with pytest.raises(LookupError, match="does not match"):
        runtime.decide(changed_strategy)


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
        "kairospy.strategy.apps.agent.services.openai_runtime.importlib.import_module",
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
    provider_values = cast(Mapping[str, object], captured["provider"])
    assert provider_values["use_responses"] is True
    assert provider_values["api_key"] == "not-logged"
    assert "hooks" in cast(Mapping[str, object], captured["run"])
    assert "not-logged" not in str(captured["input"])


def test_model_runtime_routes_openai_compatible_and_native_interfaces(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class Provider:
        def __init__(self, **values) -> None:
            captured["provider"] = values

    class Settings:
        def __init__(self, **values) -> None:
            captured.setdefault("settings", []).append(values)

    class Config:
        def __init__(self, **values) -> None:
            captured.setdefault("configs", []).append(values)

    class AnyModel:
        def __init__(self, model: str, **values) -> None:
            captured["any_model"] = (model, values)

    sdk = SimpleNamespace(
        OpenAIProvider=Provider,
        ModelSettings=Settings,
        RunConfig=Config,
    )
    any_llm = SimpleNamespace(AnyLLMModel=AnyModel)

    def import_module(name: str):
        return any_llm if name.endswith("any_llm_model") else sdk

    monkeypatch.setattr(
        "kairospy.strategy.apps.agent.services.openai_runtime.importlib.import_module",
        import_module,
    )
    ModelDecisionRuntime(
        instructions="Review risk",
        model="company/model",
        api_key="gateway-key",
        provider="custom",
        api_mode="openai-chat-completions",
        base_url="https://gateway.example/v1",
        max_turns=2,
        max_tool_calls=1,
        max_input_tokens=1000,
        max_output_tokens=100,
        request_timeout_seconds=3,
    )
    assert captured["provider"] == {
        "api_key": "gateway-key",
        "base_url": "https://gateway.example/v1",
        "use_responses": False,
        "buffer_streamed_tool_calls": True,
    }

    ModelDecisionRuntime(
        instructions="Review risk",
        model="claude-model",
        api_key="anthropic-key",
        provider="anthropic",
        api_mode="anthropic-messages",
        base_url="https://api.anthropic.example/v1",
        max_turns=2,
        max_tool_calls=1,
        max_input_tokens=1000,
        max_output_tokens=100,
        request_timeout_seconds=3,
    )
    assert captured["any_model"] == (
        "anthropic/claude-model",
        {
            "base_url": "https://api.anthropic.example/v1",
            "api_key": "anthropic-key",
            "api": "chat_completions",
        },
    )


def test_openai_runtime_enforces_tool_call_budget() -> None:
    hooks = _ToolLimitHooks(1)

    asyncio.run(hooks.on_tool_start())
    with pytest.raises(RuntimeError, match="max_tool_calls"):
        asyncio.run(hooks.on_tool_start())


def test_openai_runtime_enforces_tool_rows_and_freshness() -> None:
    hooks = _ToolLimitHooks(
        2,
        (
            MCPToolPolicy(
                "market.get_latest_quote",
                max_result_bytes=4096,
                max_rows=1,
                max_age_seconds=10,
            ),
        ),
    )
    tool = SimpleNamespace(name="market.get_latest_quote")
    fresh = datetime.now(timezone.utc).isoformat()
    asyncio.run(
        hooks.on_tool_end(None, None, tool, {"observed_at": fresh, "rows": [1]})
    )

    with pytest.raises(RuntimeError, match="row limit"):
        asyncio.run(
            hooks.on_tool_end(None, None, tool, {"observed_at": fresh, "rows": [1, 2]})
        )
    stale = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with pytest.raises(RuntimeError, match="freshness"):
        asyncio.run(
            hooks.on_tool_end(None, None, tool, {"observed_at": stale, "rows": []})
        )


def test_openai_runtime_rejects_credential_like_tool_content() -> None:
    hooks = _ToolLimitHooks(1)
    tool = SimpleNamespace(name="account.get_position")

    with pytest.raises(RuntimeError, match="credential-like"):
        asyncio.run(
            hooks.on_tool_end(
                None,
                None,
                tool,
                {"position": {"quantity": "1", "api_token": "must-not-leak"}},
            )
        )


def test_openai_runtime_distinguishes_optional_and_required_mcp_failure(
    monkeypatch,
) -> None:
    captured_inputs: list[str] = []

    class Provider:
        def __init__(self, **values) -> None:
            pass

    class Settings:
        def __init__(self, **values) -> None:
            pass

    class Config:
        def __init__(self, **values) -> None:
            pass

    class Agent:
        def __init__(self, **values) -> None:
            pass

    class Runner:
        @staticmethod
        async def run(agent, model_input, **values):
            captured_inputs.append(model_input)
            return SimpleNamespace(
                final_output=DecisionResult(
                    DecisionKind.ABSTAIN, 0, (), (), "optional evidence unavailable"
                ),
                new_items=(),
            )

    class UnavailableServer:
        async def __aenter__(self):
            raise ConnectionError("MCP unavailable")

        async def __aexit__(self, *args):
            return None

    sdk = SimpleNamespace(
        OpenAIProvider=Provider,
        ModelSettings=Settings,
        RunConfig=Config,
        Agent=Agent,
        Runner=Runner,
    )
    monkeypatch.setattr(
        "kairospy.strategy.apps.agent.services.openai_runtime.importlib.import_module",
        lambda name: sdk,
    )

    def runtime(required: bool) -> OpenAIDecisionRuntime:
        return OpenAIDecisionRuntime(
            instructions="Review risk",
            model="pinned-model",
            api_key="not-logged",
            max_turns=2,
            max_tool_calls=1,
            max_input_tokens=10_000,
            max_output_tokens=1000,
            request_timeout_seconds=2,
            mcp_servers=(
                MCPServerBinding(
                    UnavailableServer(),
                    required,
                    "context/review",
                    (),
                ),
            ),
        )

    output = runtime(False).decide(_candidate())
    assert output.tool_evidence[0].tool_name == "mcp:context/review"
    assert output.tool_evidence[0].status == "unavailable"
    assert '"kairos_tool_status": "unavailable"' in captured_inputs[0]
    assert "MCP unavailable" not in captured_inputs[0]

    with pytest.raises(ConnectionError, match="MCP unavailable"):
        runtime(True).decide(_candidate())


def test_openai_runtime_records_sanitized_optional_tool_failure() -> None:
    class ToolCallItem:
        raw_item = SimpleNamespace(
            call_id="call-optional",
            name="market.get_latest_quote",
            arguments='{"instrument_id":"BTCUSDT"}',
        )

    class ToolCallOutputItem:
        raw_item = SimpleNamespace(call_id="call-optional")
        output = '{"kairos_tool_status":"unavailable"}'

    evidence = _tool_evidence([ToolCallItem(), ToolCallOutputItem()])

    assert evidence[0].status == "unavailable"
    assert evidence[0].result_hash is not None
