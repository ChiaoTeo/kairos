from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


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
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        raise NotImplementedError("IBKR broker driver is not implemented yet")

    def cancel_order(
        self,
        id: str,
        *,
        symbol: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        raise NotImplementedError("IBKR broker driver is not implemented yet")

    def fetch_balance(self, *, params: Mapping[str, object] | None = None) -> Mapping[str, object]:
        raise NotImplementedError("IBKR broker driver is not implemented yet")


__all__ = ["IBKR"]
