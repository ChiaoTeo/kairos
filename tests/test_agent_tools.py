from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, cast

import pytest

from kairospy.application.agent.services.tools import build_mcp_servers
from kairospy.application.workspace import WorkspaceApplication


class Server:
    def __init__(self, params: Mapping[str, object], **options: object) -> None:
        self.params = dict(params)
        self.options = options


def _workspace(tmp_path: Path, allowed_tools: list[str]):
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ws")
    tools = ", ".join(f'"{tool}"' for tool in allowed_tools)
    workspace.paths.agent_mcp_config().write_text(
        f"""[servers.context]
transport = "stdio"
command = "kairos-context-mcp"
args = ["--readonly"]
cwd = "config"
timeout_seconds = 2

[profiles.review]
server = "context"
allowed_tools = [{tools}]
""",
        encoding="utf-8",
    )
    return workspace


def _sdk(monkeypatch):
    sdk = SimpleNamespace(
        create_static_tool_filter=lambda **values: values,
        MCPServerStdio=Server,
        MCPServerStreamableHttp=Server,
    )
    monkeypatch.setattr(
        "kairospy.application.agent.services.tools.importlib.import_module",
        lambda name: sdk,
    )


def test_mcp_composition_applies_read_only_static_filter_and_bounds(
    tmp_path: Path, monkeypatch
) -> None:
    _sdk(monkeypatch)
    workspace = _workspace(
        tmp_path,
        ["market.get_latest_quote", "account.get_position"],
    )

    bindings = build_mcp_servers(
        workspace,
        ({"server": "context", "profile": "review", "required": True},),
    )

    assert len(bindings) == 1
    assert bindings[0].required is True
    server = cast(Server, bindings[0].server)
    assert server.params["cwd"] == str(workspace.paths.config)
    assert server.options["max_retry_attempts"] == 0
    assert server.options["require_approval"] == "never"
    assert server.options["tool_filter"] == {
        "allowed_tool_names": [
            "market.get_latest_quote",
            "account.get_position",
        ]
    }


def test_mcp_profile_rejects_write_capability(tmp_path: Path, monkeypatch) -> None:
    _sdk(monkeypatch)
    workspace = _workspace(tmp_path, ["execution.submit_intent"])

    with pytest.raises(ValueError, match="non-approved tool"):
        build_mcp_servers(
            workspace,
            ({"server": "context", "profile": "review", "required": True},),
        )
