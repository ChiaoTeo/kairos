from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

from ._text import TextValue


@dataclass(frozen=True, slots=True)
class WorkspaceId(TextValue):
    """Canonical workspace identity."""


@dataclass(frozen=True, slots=True)
class LaunchId(TextValue):
    """Canonical launch identity."""


@dataclass(frozen=True, slots=True)
class InstanceId(TextValue):
    """Canonical process instance identity."""


@dataclass(frozen=True, slots=True)
class StrategyId(TextValue):
    """Canonical Strategy identity."""


@dataclass(frozen=True, slots=True)
class RequestId(TextValue):
    """Canonical request identity."""


WorkspaceIdRead = NewType("WorkspaceIdRead", str)
LaunchIdRead = NewType("LaunchIdRead", str)
InstanceIdRead = NewType("InstanceIdRead", str)
StrategyIdRead = NewType("StrategyIdRead", str)
RequestIdRead = NewType("RequestIdRead", str)


__all__ = [
    "InstanceId",
    "InstanceIdRead",
    "LaunchId",
    "LaunchIdRead",
    "RequestId",
    "RequestIdRead",
    "StrategyId",
    "StrategyIdRead",
    "WorkspaceId",
    "WorkspaceIdRead",
]
