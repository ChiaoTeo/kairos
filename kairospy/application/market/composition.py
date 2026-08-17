"""Private concrete construction for Strategy-facing Market access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from kairospy.application.workspace import InstanceWorkspace, Workspace
from kairospy.infrastructure.transport.commands import (
    MarketCommandClient,
    UnixJsonCommandClient,
)
from kairospy.infrastructure.transport.market import (
    AeronMarketEventSource,
    MarketProjection,
    UnixMarketEventStream,
)
from kairospy.strategy import StrategyIdentity

from .application import MarketApplication


@dataclass(frozen=True, slots=True)
class MarketAccessConfig:
    scope: Literal["shared", "instance"]
    replayable: bool = False
    source_id: str = "default"

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("Market source_id is required for v2 current views")


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
) -> StrategyMarketAccess:
    """Build Market commands, current projection, and events as one access slice."""

    if config.scope == "shared":
        command_socket = workspace.paths.process_socket("market")
        event_socket = workspace.paths.process_socket("market-events")
        snapshot = workspace.paths.child("snapshots", "market", "market-shared")
    else:
        command_socket = instance.socket("market")
        event_socket = instance.socket("market-events")
        snapshot = instance.snapshot("market", "market-shared")

    commands = MarketCommandClient(
        UnixJsonCommandClient(command_socket),
        launch_id=identity.launch_id if config.scope == "instance" else None,
        workspace_id=workspace.identity.workspace_id,
    )
    event_source = (
        UnixMarketEventStream(event_socket, replayable=True)
        if config.replayable
        else AeronMarketEventSource(
            aeron_dir=workspace.paths.aeron_dir(),
        )
    )
    application = MarketApplication(
        commands,
        MarketProjection(snapshot),
        event_source,
        strategy_id=identity.strategy_id,
        instance_id=identity.instance_id,
        # Shared Market is a workspace process and intentionally carries no
        # launch/instance ownership. Instance Market must match both.
        launch_id=identity.launch_id if config.scope == "instance" else None,
        source_id=config.source_id,
    )
    return StrategyMarketAccess(application=application)


def release_strategy_owner(
    *,
    workspace: Workspace,
    instance: InstanceWorkspace,
    identity: StrategyIdentity,
    scope: Literal["shared", "instance"],
) -> StrategyOwnerRelease:
    """Release Market demand when the owning Strategy process is unavailable."""

    command_socket = (
        workspace.paths.process_socket("market")
        if scope == "shared"
        else instance.socket("market")
    )
    request_id = (
        f"{identity.strategy_id}:{identity.instance_id}:"
        "market.release_owner:external-stop"
    )
    handle = MarketCommandClient(
        UnixJsonCommandClient(command_socket),
        launch_id=identity.launch_id,
    ).release_owner(
        strategy_id=identity.strategy_id,
        instance_id=identity.instance_id,
        request_id=request_id,
    )
    removed = handle.result.get("removed_subscription_ids", ())
    return StrategyOwnerRelease(
        request_id=handle.request_id,
        status=handle.status,
        removed_subscription_ids=(
            tuple(str(value) for value in removed if isinstance(value, str))
            if isinstance(removed, (list, tuple, set))
            else ()
        ),
        error=handle.error,
    )
