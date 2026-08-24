from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Mapping, cast

import pytest

from kairospy.strategy.apps.agent.services.tools import (
    AgentToolScope,
    _optional_mcp_error,
    build_mcp_servers,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication


class Server:
    def __init__(self, params: Mapping[str, object], **options: object) -> None:
        self.params = dict(params)
        self.options = options


def _workspace(tmp_path: Path):
    return WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ws")


def _selection(
    allowed_tools: list[str], *, required: bool
) -> tuple[Mapping[str, object], ...]:
    return (
        {
            "id": "context",
            "transport": "stdio",
            "command": "kairos-context-mcp",
            "args": ["--readonly"],
            "cwd": "config",
            "timeout_seconds": 2,
            "allowed_tools": allowed_tools,
            "scope_enforced": True,
            "max_result_bytes": 4096,
            "max_rows": 20,
            "freshness_required_tools": ["market.get_latest_quote"],
            "max_age_seconds": 30,
            "required": required,
        },
    )


def _scope() -> AgentToolScope:
    return AgentToolScope("ws", "launch", "instance", "strategy", ("main",))


def _sdk(monkeypatch):
    sdk = SimpleNamespace(
        create_static_tool_filter=lambda **values: values,
        MCPServerStdio=Server,
        MCPServerStreamableHttp=Server,
    )
    monkeypatch.setattr(
        "kairospy.strategy.apps.agent.services.tools.importlib.import_module",
        lambda name: sdk,
    )


def test_mcp_composition_applies_read_only_static_filter_and_bounds(
    tmp_path: Path, monkeypatch
) -> None:
    _sdk(monkeypatch)
    workspace = _workspace(tmp_path)

    bindings = build_mcp_servers(
        workspace,
        _selection(["market.get_latest_quote", "account.get_position"], required=True),
        scope=_scope(),
    )

    assert len(bindings) == 1
    assert bindings[0].required is True
    server = cast(Server, bindings[0].server)
    assert server.params["cwd"] == str(workspace.paths.config)
    assert server.params["env"] == {
        "KAIROS_AGENT_WORKSPACE_ID": "ws",
        "KAIROS_AGENT_LAUNCH_ID": "launch",
        "KAIROS_AGENT_INSTANCE_ID": "instance",
        "KAIROS_AGENT_STRATEGY_ID": "strategy",
        "KAIROS_AGENT_ACCOUNT_IDS": '["main"]',
    }
    assert server.options["max_retry_attempts"] == 0
    assert server.options["require_approval"] == "never"
    assert server.options["failure_error_function"] is None
    assert server.options["tool_filter"] == {
        "allowed_tool_names": [
            "market.get_latest_quote",
            "account.get_position",
        ]
    }


def test_optional_mcp_failure_is_sanitized_for_model_and_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    _sdk(monkeypatch)
    workspace = _workspace(tmp_path)

    bindings = build_mcp_servers(
        workspace,
        _selection(["market.get_latest_quote"], required=False),
        scope=_scope(),
    )

    server = cast(Server, bindings[0].server)
    formatter = cast(
        Callable[[object, Exception], str],
        server.options["failure_error_function"],
    )
    assert formatter is _optional_mcp_error
    assert formatter(object(), RuntimeError("Bearer secret-value")) == (
        '{"kairos_tool_status":"unavailable"}'
    )


def test_mcp_profile_rejects_write_capability(tmp_path: Path, monkeypatch) -> None:
    _sdk(monkeypatch)
    workspace = _workspace(tmp_path)

    with pytest.raises(ValueError, match="non-approved tool"):
        build_mcp_servers(
            workspace,
            _selection(["execution.submit_intent"], required=True),
            scope=_scope(),
        )


def test_mcp_composition_uses_launch_inline_configuration_without_workspace_catalog(
    tmp_path: Path, monkeypatch
) -> None:
    _sdk(monkeypatch)
    workspace = _workspace(tmp_path)
    selections = _selection(["market.get_latest_quote"], required=True)

    bindings = build_mcp_servers(
        workspace,
        selections,
        scope=_scope(),
    )

    server = cast(Server, bindings[0].server)
    assert server.params["command"] == "kairos-context-mcp"
    assert bindings[0].policies[0].tool_name == "market.get_latest_quote"
