from __future__ import annotations

from pathlib import Path
from typing import Mapping

from kairospy.application.usecases.strategy.services.entrypoint import StrategyEntrypoint, load_strategy_entrypoint


class StrategyApplication:
    """Public strategy loading and strategy-support application API."""

    def load(self, ref: str, *, root: Path, env: object | None = None, params: Mapping[str, object] | None = None) -> StrategyEntrypoint:
        return load_strategy_entrypoint(ref, root=root, env=env, params=params)


__all__ = ["StrategyApplication"]
