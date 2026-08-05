"""Public reference snapshot translation entrypoints."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from kairospy.application.usecases.reference.services.builders import catalog_from_market_rows
from kairospy.application.usecases.reference.application.requests import ReferenceMarketRow
from kairospy.domain.reference import ReferenceCatalog


def catalog_from_market_snapshot(rows: Iterable[ReferenceMarketRow], *, effective_from: datetime) -> ReferenceCatalog:
    return catalog_from_market_rows(rows, effective_from=effective_from)


__all__ = ["catalog_from_market_snapshot"]
