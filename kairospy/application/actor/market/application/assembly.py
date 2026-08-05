"""Market Actor usecase assembly."""

from __future__ import annotations

from kairospy.application.usecases.market.application.component import MarketApplication


def build_market_application(source: object | None, *, store: object | None = None) -> MarketApplication:
    """Build the market application owned by the Market Actor."""
    market_data = getattr(source, "market_data", None) or source
    reader = getattr(market_data, "reader", None)
    writer = getattr(market_data, "writer", None)
    return (
        MarketApplication(
            store=store,
            reader=reader,
            writer=writer,
            integration_runtime=getattr(source, "integration_runtime", None),
        )
        if reader is not None
        else MarketApplication(store=store, integration_runtime=getattr(source, "integration_runtime", None))
    )


__all__ = ["build_market_application"]
