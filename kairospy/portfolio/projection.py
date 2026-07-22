from __future__ import annotations

from datetime import datetime
from typing import Any

from kairospy.strategy.views import PortfolioView


def portfolio_view_from_snapshot(
    snapshot: Any,
    *,
    timestamp: datetime | None = None,
    ledger: Any | None = None,
    account_states: tuple[Any, ...] = (),
    market_view: Any | None = None,
) -> PortfolioView:
    """Project portfolio owner facts into the strategy read model."""

    return PortfolioView.from_snapshot(
        snapshot,
        timestamp=timestamp,
        ledger=ledger,
        account_states=account_states,
        market_view=market_view,
    )


__all__ = ["portfolio_view_from_snapshot"]
