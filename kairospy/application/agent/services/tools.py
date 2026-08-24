from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
from pathlib import Path
from typing import Any, AsyncContextManager, Mapping, cast

from kairospy.application.workspace.credentials import (
    CredentialConfigurationApplication,
)
from kairospy.application.workspace import Workspace

from ..configuration import READ_ONLY_AGENT_TOOLS


@dataclass(frozen=True, slots=True)
class MCPServerBinding:
    server: AsyncContextManager[Any]
    required: bool
    name: str
    policies: tuple["MCPToolPolicy", ...]


@dataclass(frozen=True, slots=True)
class AgentToolScope:
    workspace_id: str
    launch_id: str
    instance_id: str
    strategy_id: str
    account_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("workspace_id", "launch_id", "instance_id", "strategy_id"):
            value = getattr(self, name)
            if not value.strip() or len(value) > 256:
                raise ValueError(f"Agent tool scope {name} is invalid")
        if len(self.account_ids) > 128 or any(
            not value.strip() or len(value) > 256 for value in self.account_ids
        ):
            raise ValueError("Agent tool scope account_ids are invalid")
        object.__setattr__(self, "account_ids", tuple(dict.fromkeys(self.account_ids)))


@dataclass(frozen=True, slots=True)
class MCPToolPolicy:
    tool_name: str
    max_result_bytes: int
    max_rows: int
    max_age_seconds: float | None


def build_mcp_servers(
    workspace: Workspace,
    selections: tuple[Mapping[str, object], ...],
    *,
    scope: AgentToolScope,
    snapshot: Mapping[str, object] | None = None,
) -> tuple[MCPServerBinding, ...]:
    del snapshot
    if not selections:
        return ()
    sdk = importlib.import_module("agents.mcp")
    bindings: list[MCPServerBinding] = []
    for server in selections:
        server_id = _text(server.get("id"), "agent.mcp.id")
        allowed = _strings(
            server.get("allowed_tools"), f"MCP server {server_id}.allowed_tools"
        )
        forbidden = set(allowed) - READ_ONLY_AGENT_TOOLS
        if forbidden:
            raise ValueError(
                "MCP profile contains a non-approved tool: " + sorted(forbidden)[0]
            )
        if server.get("scope_enforced") is not True:
            raise ValueError(f"MCP server {server_id} must enforce Strategy scope")
        max_result_bytes = _integer(
            server.get("max_result_bytes", 65_536),
            f"MCP server {server_id}.max_result_bytes",
            minimum=1,
            maximum=65_536,
        )
        max_rows = _integer(
            server.get("max_rows", 200),
            f"MCP server {server_id}.max_rows",
            minimum=1,
            maximum=10_000,
        )
        freshness_required = frozenset(
            _strings(
                server.get("freshness_required_tools", ()),
                f"MCP server {server_id}.freshness_required_tools",
            )
        )
        if not freshness_required.issubset(allowed):
            raise ValueError(
                f"MCP profile {profile_id} freshness tools must be allowed tools"
            )
        max_age_seconds = (
            _number(
                server.get("max_age_seconds"),
                f"MCP server {server_id}.max_age_seconds",
            )
            if freshness_required
            else None
        )
        policies = tuple(
            MCPToolPolicy(
                tool_name,
                max_result_bytes,
                max_rows,
                max_age_seconds if tool_name in freshness_required else None,
            )
            for tool_name in allowed
        )
        tool_filter = sdk.create_static_tool_filter(allowed_tool_names=list(allowed))
        transport = _text(server.get("transport"), f"MCP server {server_id}.transport")
        common = {
            "cache_tools_list": True,
            "name": server_id,
            "client_session_timeout_seconds": _number(
                server.get("timeout_seconds", 5),
                f"MCP server {server_id}.timeout_seconds",
            ),
            "tool_filter": tool_filter,
            "use_structured_content": True,
            "max_retry_attempts": 0,
            "require_approval": "never",
            "failure_error_function": (
                None if bool(server.get("required", False)) else _optional_mcp_error
            ),
        }
        if transport == "stdio":
            command = _text(server.get("command"), f"MCP server {server_id}.command")
            args = _strings(server.get("args", ()), f"MCP server {server_id}.args")
            params: dict[str, object] = {
                "command": command,
                "args": list(args),
                "env": _scope_environment(scope),
            }
            cwd = server.get("cwd")
            if cwd is not None:
                params["cwd"] = str(_workspace_path(workspace, str(cwd)))
            instance = sdk.MCPServerStdio(params, **common)
        elif transport == "streamable_http":
            url = _text(server.get("url"), f"MCP server {server_id}.url")
            if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
                raise ValueError("Remote MCP URL must use HTTPS")
            params = {"url": url, "headers": _scope_headers(scope)}
            credential = server.get("credential")
            if credential is not None:
                token = _text(
                    CredentialConfigurationApplication(workspace).resolve_field(
                        str(credential), "token"
                    ),
                    f"MCP credential {credential}.token",
                )
                params["headers"] = {
                    **cast(dict[str, str], params["headers"]),
                    "Authorization": f"Bearer {token}",
                }
            instance = sdk.MCPServerStreamableHttp(params, **common)
        else:
            raise ValueError(f"MCP server {server_id} has unsupported transport")
        bindings.append(
            MCPServerBinding(
                instance,
                bool(server.get("required", False)),
                server_id,
                policies,
            )
        )
    return tuple(bindings)


def _workspace_path(workspace: Workspace, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        raise ValueError("MCP cwd must be relative to the Workspace")
    return workspace.paths.child(*path.parts)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{name} must be an array of strings")
    return tuple(str(item).strip() for item in value)


def _number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be positive")
    return float(value)


def _integer(value: object, name: str, *, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _scope_environment(scope: AgentToolScope) -> dict[str, str]:
    return {
        "KAIROS_AGENT_WORKSPACE_ID": scope.workspace_id,
        "KAIROS_AGENT_LAUNCH_ID": scope.launch_id,
        "KAIROS_AGENT_INSTANCE_ID": scope.instance_id,
        "KAIROS_AGENT_STRATEGY_ID": scope.strategy_id,
        "KAIROS_AGENT_ACCOUNT_IDS": json.dumps(scope.account_ids),
    }


def _scope_headers(scope: AgentToolScope) -> dict[str, str]:
    return {
        "X-Kairos-Workspace-Id": scope.workspace_id,
        "X-Kairos-Launch-Id": scope.launch_id,
        "X-Kairos-Instance-Id": scope.instance_id,
        "X-Kairos-Strategy-Id": scope.strategy_id,
        "X-Kairos-Account-Ids": ",".join(scope.account_ids),
    }


def _optional_mcp_error(context: object, error: Exception) -> str:
    # Never return the provider exception to the model: it may contain an URL,
    # header, account identifier, or credential fragment.
    return '{"kairos_tool_status":"unavailable"}'


__all__ = [
    "AgentToolScope",
    "MCPServerBinding",
    "MCPToolPolicy",
    "build_mcp_servers",
]
