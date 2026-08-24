"""Launch application boundary with capability-lazy public exports."""

from __future__ import annotations

from typing import Any


_APPLICATION_EXPORTS = {
    "InstanceControlTarget",
    "LaunchControlApplication",
    "LaunchInstanceApplication",
    "LaunchInstanceTimelineApplication",
    "LaunchRegistryApplication",
    "StrategyProcessController",
}
_DOMAIN_EXPORTS = {"InstanceState", "LaunchIdentity", "LaunchInstance"}


def __getattr__(name: str) -> Any:
    if name in _APPLICATION_EXPORTS:
        from . import application

        return getattr(application, name)
    if name in _DOMAIN_EXPORTS:
        from .domain.identity import InstanceState, LaunchIdentity, LaunchInstance

        return {
            "InstanceState": InstanceState,
            "LaunchIdentity": LaunchIdentity,
            "LaunchInstance": LaunchInstance,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = sorted(_APPLICATION_EXPORTS | _DOMAIN_EXPORTS)
