from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class Component(StrEnum):
    CATALOG = "catalog"
    MARKET_DATA = "market_data"
    ACCOUNT = "account"
    EXECUTION = "execution"
    RECONCILIATION = "reconciliation"


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    component: Component
    ready: bool
    reason: str
    checked_at: datetime


class SystemReadiness:
    def __init__(self) -> None:
        self._health = {
            component: ComponentHealth(component, False, "not checked", datetime.now(timezone.utc))
            for component in Component
        }

    def update(self, component: Component, ready: bool, reason: str = "ok") -> None:
        self._health[component] = ComponentHealth(component, ready, reason, datetime.now(timezone.utc))

    @property
    def ready(self) -> bool:
        return all(item.ready for item in self._health.values())

    @property
    def health(self) -> tuple[ComponentHealth, ...]:
        return tuple(self._health[item] for item in Component)

    def require_ready(self) -> None:
        failures = [f"{item.component}:{item.reason}" for item in self.health if not item.ready]
        if failures:
            raise RuntimeError(f"system is not ready: {', '.join(failures)}")
