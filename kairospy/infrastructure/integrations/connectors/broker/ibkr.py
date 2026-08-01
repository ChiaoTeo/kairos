from __future__ import annotations

from dataclasses import dataclass
from kairospy.infrastructure.integrations.types import IntegrationParams, OrderSubmissionResponse, RawPayload


@dataclass(frozen=True, slots=True)
class IBKR:
    name: str = "ibkr"

    def create_order(
        self,
        symbol: str,
        *,
        side: str,
        type: str,
        amount: object,
        price: object | None = None,
        params: IntegrationParams | None = None,
    ) -> OrderSubmissionResponse:
        raise NotImplementedError("IBKR broker driver is not implemented yet")

    def cancel_order(
        self,
        id: str,
        *,
        symbol: str | None = None,
        params: IntegrationParams | None = None,
    ) -> OrderSubmissionResponse:
        raise NotImplementedError("IBKR broker driver is not implemented yet")

    def fetch_balance(self, *, params: IntegrationParams | None = None) -> RawPayload:
        raise NotImplementedError("IBKR broker driver is not implemented yet")


__all__ = ["IBKR"]
