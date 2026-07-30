from __future__ import annotations

from enum import StrEnum


class RuntimeMode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"
    SYSTEM = "system"


__all__ = ["RuntimeMode"]
