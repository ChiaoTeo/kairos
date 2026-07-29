from __future__ import annotations

def __getattr__(name: str) -> object:
    if name == "TradingSystemLauncher":
        from .trading import TradingSystemLauncher

        return TradingSystemLauncher
    raise AttributeError(name)

__all__ = ["TradingSystemLauncher"]
