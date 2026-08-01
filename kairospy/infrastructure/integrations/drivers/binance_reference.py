from __future__ import annotations

from dataclasses import dataclass, field

from .ccxt import CcxtDriver
from kairospy.infrastructure.integrations.payloads.types import IntegrationParams, RawPayloadRows


@dataclass(frozen=True, slots=True)
class BinanceReferenceDriver:
    driver: CcxtDriver = field(default_factory=CcxtDriver)

    def fetch_delist_schedule(self, *, params: IntegrationParams | None = None) -> RawPayloadRows:
        return self.driver.fetch_binance_spot_delist_schedule(params=params)


__all__ = ["BinanceReferenceDriver"]
