from __future__ import annotations

from collections.abc import Callable


class StrategyContractError(ValueError):
    """Raised when a loaded strategy does not implement the public contract."""


_LIFECYCLE = ("on_start", "on_data", "on_intent", "on_clock", "on_system", "on_end")


def validate_strategy(strategy: object) -> None:
    strategy_id = getattr(strategy, "strategy_id", None)
    if not isinstance(strategy_id, str) or not strategy_id.strip():
        raise StrategyContractError("strategy must expose a non-empty strategy_id")
    missing = [name for name in _LIFECYCLE if not isinstance(getattr(strategy, name, None), Callable)]
    if missing:
        raise StrategyContractError(
            f"strategy {strategy_id!r} is missing lifecycle callbacks: {', '.join(missing)}"
        )
