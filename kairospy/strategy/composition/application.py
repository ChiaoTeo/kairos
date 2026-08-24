"""Construct Strategy from already-resolved System launch context."""

from __future__ import annotations

from typing import Any

from kairospy.strategy.apps.runtime.composition import compose_strategy_runtime


def compose_strategy_application(**launch_context: Any):
    return compose_strategy_runtime(**launch_context)


__all__ = ["compose_strategy_application"]
