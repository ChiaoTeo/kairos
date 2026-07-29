from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


class ConnectionManager(Protocol):
    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...

    def health(self) -> Mapping[str, object]:
        ...


@dataclass(frozen=True, slots=True)
class NoopConnectionManager:
    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def health(self) -> Mapping[str, object]:
        return {"status": "ready", "connections": 0}


__all__ = ["ConnectionManager", "NoopConnectionManager"]
