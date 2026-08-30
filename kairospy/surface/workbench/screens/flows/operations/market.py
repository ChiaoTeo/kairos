"""Workspace Market runtime facts exposed from the Operations Center."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from rich.console import Group, RenderableType

from kairospy.system.apps.components.application import ComponentProcessApplication
from kairospy.system.apps.components.application.clients import MarketSystemClient

from ...presentation import ResultTone, conclusion, facts
from ..market.workspace import (
    command_status_mapping,
    routes_renderable,
    routes_response_mapping,
    subscriptions_renderable,
    subscriptions_response_mapping,
)


def execute(state: Any, action: str) -> Mapping[str, Any]:
    """Read or control the project-shared Market runtime."""

    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的项目")
    owner = state.owner
    processes = ComponentProcessApplication(owner)
    client = cast(
        MarketSystemClient,
        processes.client("market", owner.paths.process_socket("market")),
    )
    if action == "routes":
        return routes_response_mapping(client.data_routes())
    if action == "subscriptions":
        return subscriptions_response_mapping(client.subscriptions())
    if action == "pause-replay":
        return command_status_mapping(client.pause_replay())
    if action == "resume-replay":
        return command_status_mapping(client.resume_replay())
    raise ValueError(f"unknown Workspace Market runtime action: {action}")


def render(action: str, result: Mapping[str, Any]) -> RenderableType:
    """Present shared runtime facts without leaking them into Market browsing."""

    if action == "routes":
        return routes_renderable(result)
    if action == "subscriptions":
        return subscriptions_renderable(result, current_session=False)
    replay_state = str(result.get("state") or result.get("status") or "unknown")
    paused = action == "pause-replay"
    return Group(
        conclusion(
            "项目共享行情回放已暂停" if paused else "项目共享行情回放已继续",
            tone=ResultTone.SUCCESS,
        ),
        facts((("运行状态", replay_state), ("作用域", "项目共享行情服务"))),
    )


__all__ = ["execute", "render"]
