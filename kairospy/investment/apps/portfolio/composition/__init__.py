from __future__ import annotations

from kairospy.investment.apps.account.application import AccountApplication

from ..application.application import PortfolioApplication


def build_strategy_access(
    *,
    launch_id: str,
    mode: str,
    account: AccountApplication,
    valuation_asset: str | None = None,
) -> PortfolioApplication:
    """Build the one in-process Portfolio record owned by a Strategy instance."""

    return PortfolioApplication(
        f"{mode}:{launch_id}", account, valuation_asset=valuation_asset
    )
