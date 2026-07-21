from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Iterable, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class HistoricalDataRequest:
    dataset_id: str
    start: datetime | None = None
    end: datetime | None = None
    instruments: tuple[str, ...] = ()
    params: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LiveDataRequest:
    dataset_id: str
    account: str | None = None
    instruments: tuple[str, ...] = ()
    channel: str | None = None
    params: Mapping[str, object] = field(default_factory=dict)


class HistoricalDataProtocol(Protocol):
    def load(self, request: HistoricalDataRequest) -> Iterable[Mapping[str, object]]:
        ...


class LiveDataProtocol(Protocol):
    async def stream(self, request: LiveDataRequest) -> AsyncIterator[Mapping[str, object]]:
        ...


class DataProtocolRegistry:
    def __init__(self) -> None:
        self._historical: dict[str, HistoricalDataProtocol] = {}
        self._live: dict[str, LiveDataProtocol] = {}

    def register_historical(self, name: str, adapter: HistoricalDataProtocol) -> None:
        self._historical[_clean_name(name)] = adapter

    def register_live(self, name: str, adapter: LiveDataProtocol) -> None:
        self._live[_clean_name(name)] = adapter

    def historical(self, name: str) -> HistoricalDataProtocol:
        key = _clean_name(name)
        try:
            return self._historical[key]
        except KeyError as error:
            raise KeyError(f"unknown historical data protocol: {name}") from error

    def live(self, name: str) -> LiveDataProtocol:
        key = _clean_name(name)
        try:
            return self._live[key]
        except KeyError as error:
            raise KeyError(f"unknown live data protocol: {name}") from error


def _clean_name(name: str) -> str:
    value = str(name).strip()
    if not value:
        raise ValueError("data protocol name cannot be empty")
    return value
