from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from trading.application.clock import Clock, SystemClock
from trading.adapters.base import ExecutionAdapter
from trading.domain.identity import AccountKey

if TYPE_CHECKING:
    from .runtime_store import SQLiteRuntimeStore


@dataclass(frozen=True, slots=True)
class KillSwitchResult:
    triggered_at: datetime
    reason: str
    cancelled_orders: tuple[str, ...]
    failures: tuple[str, ...]


class KillSwitch:
    STATE_KEY = "kill_switch"

    def __init__(self, adapters: tuple[ExecutionAdapter, ...], clock: Clock | None = None,
                 runtime_store: "SQLiteRuntimeStore | None" = None) -> None:
        self.adapters = adapters
        self.clock = clock or SystemClock()
        self.runtime_store = runtime_store
        state = runtime_store.runtime_state(self.STATE_KEY) if runtime_store is not None else None
        self.triggered = bool(state.get("triggered")) if isinstance(state, dict) else False
        self.reduce_only = bool(state.get("reduce_only")) if isinstance(state, dict) else False

    def trigger(self, accounts: tuple[AccountKey, ...], reason: str) -> KillSwitchResult:
        cancelled, failures = [], []
        for adapter in self.adapters:
            for account in accounts:
                if account.institution_id != adapter.institution_id:
                    continue
                for order_id in adapter.open_orders(account):
                    try:
                        adapter.cancel_order(account, order_id)
                        cancelled.append(order_id)
                    except Exception as error:
                        failures.append(f"{adapter.venue_id}:{order_id}:{error}")
        self.triggered = True
        self.reduce_only = True
        result = KillSwitchResult(self.clock.now(), reason, tuple(cancelled), tuple(failures))
        if self.runtime_store is not None:
            self.runtime_store.set_runtime_state(self.STATE_KEY, {
                "triggered": True,
                "reduce_only": True,
                "reason": reason,
                "triggered_at": result.triggered_at.isoformat(),
                "cancelled_orders": list(result.cancelled_orders),
                "failures": list(result.failures),
            }, result.triggered_at)
        return result

    def reset(self, *, actor: str, reason: str) -> None:
        if not actor.strip() or not reason.strip():
            raise ValueError("kill switch reset requires actor and reason")
        self.triggered = False
        self.reduce_only = False
        at = self.clock.now()
        if self.runtime_store is not None:
            self.runtime_store.set_runtime_state(self.STATE_KEY, {
                "triggered": False,
                "reduce_only": False,
                "reset_by": actor,
                "reset_reason": reason,
                "reset_at": at.isoformat(),
            }, at)
