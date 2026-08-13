from __future__ import annotations

from typing import TYPE_CHECKING

from kairospy.domain_types import AccountId

from .models import RiskStatus

if TYPE_CHECKING:
    from kairospy.infrastructure.contracts.risk import RiskMmapProjection


class RiskApplication:
    """Concrete read-only Risk projection scoped to one strategy launch."""

    def __init__(self, projection: RiskMmapProjection | None) -> None:
        self._projection = projection

    def status(self, *, account: AccountId | str) -> RiskStatus:
        if self._projection is None:
            raise RuntimeError("Risk projection is unavailable")
        account_id = account if isinstance(account, AccountId) else AccountId(account)
        return self._projection.status(account_id)
