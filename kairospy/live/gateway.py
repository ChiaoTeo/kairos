from __future__ import annotations

from typing import AsyncIterator, Iterable, Mapping, Protocol


class LiveAccountGateway(Protocol):
    def fetch_balance(self, *, params: Mapping[str, object] | None = None) -> Mapping[str, object]:
        ...

    def fetch_open_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        ...

    def watch_balance(
        self,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        ...

    def watch_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        ...

    def watch_my_trades(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        ...


__all__ = ["LiveAccountGateway"]
