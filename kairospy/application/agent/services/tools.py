from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path
import re
import tomllib
from typing import Any, AsyncContextManager, Mapping, cast

from kairospy.application.workspace import Workspace


_READ_ONLY_TOOLS = frozenset(
    {
        "reference.get_instrument",
        "market.get_latest_quote",
        "market.get_recent_bars",
        "market.get_freshness",
        "account.get_position",
        "account.get_equity",
        "account.get_available_margin",
        "risk.get_effective_limits",
        "execution.get_active_intents",
        "execution.get_recent_failures",
    }
)


@dataclass(frozen=True, slots=True)
class MCPServerBinding:
    server: AsyncContextManager[Any]
    required: bool
    name: str


def build_mcp_servers(
    workspace: Workspace,
    selections: tuple[Mapping[str, object], ...],
) -> tuple[MCPServerBinding, ...]:
    if not selections:
        return ()
    values = _load_toml(workspace.paths.agent_mcp_config(), "Agent MCP config")
    servers = _mapping(values.get("servers"), "Agent MCP servers")
    profiles = _mapping(values.get("profiles"), "Agent MCP profiles")
    sdk = importlib.import_module("agents.mcp")
    bindings: list[MCPServerBinding] = []
    for selection in selections:
        server_id = _text(selection.get("server"), "agent.mcp.server")
        profile_id = _text(selection.get("profile"), "agent.mcp.profile")
        server = _mapping(servers.get(server_id), f"MCP server {server_id}")
        profile = _mapping(profiles.get(profile_id), f"MCP profile {profile_id}")
        if (
            _text(profile.get("server"), f"MCP profile {profile_id}.server")
            != server_id
        ):
            raise ValueError(f"MCP profile {profile_id} does not select {server_id}")
        allowed = _strings(
            profile.get("allowed_tools"), f"MCP profile {profile_id}.allowed_tools"
        )
        forbidden = set(allowed) - _READ_ONLY_TOOLS
        if forbidden:
            raise ValueError(
                "MCP profile contains a non-approved tool: " + sorted(forbidden)[0]
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
        }
        if transport == "stdio":
            command = _text(server.get("command"), f"MCP server {server_id}.command")
            args = _strings(server.get("args", ()), f"MCP server {server_id}.args")
            params: dict[str, object] = {"command": command, "args": list(args)}
            cwd = server.get("cwd")
            if cwd is not None:
                params["cwd"] = str(_workspace_path(workspace, str(cwd)))
            instance = sdk.MCPServerStdio(params, **common)
        elif transport == "streamable_http":
            url = _text(server.get("url"), f"MCP server {server_id}.url")
            if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
                raise ValueError("Remote MCP URL must use HTTPS")
            params = {"url": url}
            credential = server.get("credential")
            if credential is not None:
                secret = _load_credential(workspace, str(credential))
                token = _text(secret.get("token"), f"MCP credential {credential}.token")
                params["headers"] = {"Authorization": f"Bearer {token}"}
            instance = sdk.MCPServerStreamableHttp(params, **common)
        else:
            raise ValueError(f"MCP server {server_id} has unsupported transport")
        bindings.append(
            MCPServerBinding(
                instance,
                bool(selection.get("required", False)),
                f"{server_id}/{profile_id}",
            )
        )
    return tuple(bindings)


def _load_credential(workspace: Workspace, credential_id: str) -> Mapping[str, object]:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", credential_id) or "unnamed"
    path = workspace.paths.credential_config().parent / f"{safe}.toml"
    value = _load_toml(path, f"Agent credential {credential_id}")
    credential = _mapping(value.get("credential", value), "Agent credential")
    actual_id = str(credential.get("id", credential_id))
    if actual_id != credential_id:
        raise ValueError("Agent credential identity mismatch")
    return credential


def _load_toml(path: Path, name: str) -> Mapping[str, object]:
    try:
        return cast(
            Mapping[str, object], tomllib.loads(path.read_text(encoding="utf-8"))
        )
    except FileNotFoundError as error:
        raise FileNotFoundError(f"{name} does not exist: {path}") from error
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"Invalid {name}: {path}") from error


def _workspace_path(workspace: Workspace, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        raise ValueError("MCP cwd must be relative to the Workspace")
    return workspace.paths.child(*path.parts)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a table")
    return value


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


__all__ = ["MCPServerBinding", "build_mcp_servers"]
