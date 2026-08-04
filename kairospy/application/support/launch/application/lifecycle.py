from __future__ import annotations

from typing import Protocol


class TradingLifecycle(Protocol):
    def prepare(self) -> None: ...
    def complete(self) -> None: ...


class NoopTradingLifecycle:
    def prepare(self) -> None:
        return

    def complete(self) -> None:
        return


__all__ = ["NoopTradingLifecycle", "TradingLifecycle"]
