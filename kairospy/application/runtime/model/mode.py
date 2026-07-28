from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


ClockKind = Literal["replay", "system"]
ExecutionKind = Literal["simulated", "paper", "venue"]
AccountSourceKind = Literal["simulated", "venue"]


class RuntimeMode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class RunProfile:
    mode: RuntimeMode
    execution: ExecutionKind
    account_source: AccountSourceKind
    clock: ClockKind


BACKTEST_PROFILE = RunProfile(
    RuntimeMode.BACKTEST,
    execution="simulated",
    account_source="simulated",
    clock="replay",
)
PAPER_PROFILE = RunProfile(
    RuntimeMode.PAPER,
    execution="simulated",
    account_source="simulated",
    clock="replay",
)
LIVE_PROFILE = RunProfile(
    RuntimeMode.LIVE,
    execution="venue",
    account_source="venue",
    clock="system",
)


__all__ = [
    "BACKTEST_PROFILE",
    "LIVE_PROFILE",
    "PAPER_PROFILE",
    "RunProfile",
    "RuntimeMode",
]
