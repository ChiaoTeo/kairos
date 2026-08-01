from __future__ import annotations

from .components import AccountCatalog, AccountRuntime, ExecutionRuntime, MarketRuntime, ReferenceRuntime, RuntimeComponents


def __getattr__(name: str) -> object:
    if name in {"RuntimeLaunchSpec", "RuntimeRunner"}:
        from .launch import RuntimeLaunchSpec, RuntimeRunner

        return {"RuntimeLaunchSpec": RuntimeLaunchSpec, "RuntimeRunner": RuntimeRunner}[name]
    raise AttributeError(name)

__all__ = [
    "AccountCatalog",
    "AccountRuntime",
    "ExecutionRuntime",
    "MarketRuntime",
    "ReferenceRuntime",
    "RuntimeComponents",
    "RuntimeLaunchSpec",
    "RuntimeRunner",
]
