from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from trading.adapters.base import ExecutionAdapter
from trading.domain.identity import AccountKey


@dataclass(frozen=True, slots=True)
class KillSwitchResult:
    triggered_at: datetime
    reason: str
    cancelled_orders: tuple[str, ...]
    failures: tuple[str, ...]


class KillSwitch:
    def __init__(self, adapters: tuple[ExecutionAdapter, ...]) -> None:
        self.adapters = adapters
        self.triggered = False
        self.reduce_only = False

    def trigger(self, accounts: tuple[AccountKey, ...], reason: str) -> KillSwitchResult:
        cancelled, failures = [], []
        for adapter in self.adapters:
            for account in accounts:
                if account.venue_id != adapter.venue_id:
                    continue
                for order_id in adapter.open_orders(account):
                    try:
                        adapter.cancel_order(account, order_id)
                        cancelled.append(order_id)
                    except Exception as error:
                        failures.append(f"{adapter.venue_id}:{order_id}:{error}")
        self.triggered = True
        self.reduce_only = True
        return KillSwitchResult(datetime.now(timezone.utc), reason, tuple(cancelled), tuple(failures))
