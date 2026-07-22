from __future__ import annotations

from enum import StrEnum


class Environment(StrEnum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"


__all__ = ["Environment"]
