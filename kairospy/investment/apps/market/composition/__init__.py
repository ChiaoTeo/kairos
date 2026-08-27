"""Private concrete construction for Strategy-facing Market access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from kairospy.system.apps.components.application.clients import MarketSystemClient
from kairospy.system.apps.workspace.application import InstanceWorkspace, Workspace
from kairospy.infrastructure.contracts.market import MarketClient, MarketCurrentView
from kairospy.strategy import StrategyIdentity

from ..application.application import MarketApplication
from .replay import UnixMarketEventStream


@dataclass(frozen=True, slots=True)
class MarketAccessConfig:
    scope: Literal["shared", "instance"]
    replayable: bool = False


@dataclass(frozen=True, slots=True)
class StrategyMarketAccess:
    application: MarketApplication


@dataclass(frozen=True, slots=True)
class StrategyOwnerRelease:
    request_id: str
    status: str
    removed_subscription_ids: tuple[str, ...]
    error: str | None = None


def build_strategy_access(
    *,
    workspace: Workspace,
    instance: InstanceWorkspace,
    identity: StrategyIdentity,
    config: MarketAccessConfig,
    client: MarketSystemClient,
) -> StrategyMarketAccess:
    """Build Market commands, current views, and events as one access slice."""

    if config.scope == "shared":
        event_socket = workspace.paths.process_socket("market-events")
        snapshot = workspace.paths.child("snapshots")
        view_launch_id = None
        view_instance_id = None
    else:
        event_socket = instance.socket("market-events")
        snapshot = instance.snapshot()
        view_launch_id = identity.launch_id
        view_instance_id = identity.instance_id

    event_source = (
        UnixMarketEventStream(event_socket)
        if config.replayable
        else MarketClient(
            client.socket_path,
            workspace_id=workspace.identity.workspace_id,
            aeron_dir=str(workspace.paths.aeron_dir()),
        ).events
    )
    application = MarketApplication(
        client.control,
        MarketCurrentView(
            snapshot,
            workspace.identity.workspace_id,
            view_launch_id,
            view_instance_id,
        ),
        event_source,
        strategy_id=identity.strategy_id,
        instance_id=identity.instance_id,
        # Shared Market is a workspace process and intentionally carries no
        # launch/instance ownership. Instance Market must match both.
        launch_id=identity.launch_id if config.scope == "instance" else None,
    )
    return StrategyMarketAccess(application=application)


def release_strategy_owner(
    *,
    client: MarketSystemClient,
    identity: StrategyIdentity,
    scope: Literal["shared", "instance"],
) -> StrategyOwnerRelease:
    """Release Market demand when the owning Strategy process is unavailable."""

    request_id = (
        f"{identity.strategy_id}:{identity.instance_id}:"
        "market.release_owner:external-stop"
    )
    response = client.control.release_owner(
        strategy_id=identity.strategy_id,
        instance_id=identity.instance_id,
        request_id=request_id,
        launch_id=identity.launch_id if scope == "instance" else None,
    )
    return StrategyOwnerRelease(
        request_id=request_id,
        status="applied",
        removed_subscription_ids=tuple(response.released_subscription_ids),
        error=None,
    )
