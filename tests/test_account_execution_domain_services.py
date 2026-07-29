from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.runtime.protocol import RuntimeLine
from kairospy.application.service.domain.account import SimulatedAccount, account_baseline_event
from kairospy.application.service.domain.execution import JsonExecutionStateStore
from kairospy.application.service.modes.paper import PaperAccountService
from kairospy.core.account import AccountSource, Environment
from kairospy.core.execution import ExecutionCoordinator


class NoopStrategy:
    strategy_id = "account-domain-strategy"

    def on_start(self, context: object) -> None:
        return None

    def on_data(self, context: object, signal: object) -> None:
        return None

    def on_intent(self, context: object, intent: object) -> None:
        return None

    def on_clock(self, context: object, signal: object) -> None:
        return None

    def on_system(self, context: object, signal: object) -> None:
        return None

    def on_end(self, context: object) -> None:
        return None


def test_account_baseline_event_updates_account_view_state() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    account = SimulatedAccount("paper-main", Decimal("0"), cash_currency="USDT", broker="paper", environment=Environment.PAPER)
    coordinator = ExecutionCoordinator()
    account_service = PaperAccountService(account, coordinator)
    event = account_baseline_event(
        account.context,
        sequence=1,
        at=now,
        currency="USDT",
        equity=Decimal("1000"),
        source=AccountSource.SIMULATED,
    )
    kernel = RuntimeKernel(NoopStrategy(), account=account_service)

    asyncio.run(kernel.run(RuntimeLine((event,))))

    view = kernel.views.require("account.current.paper.paper.paper_main")
    assert view.event_count == 1
    assert view.cash == Decimal("1000")
    assert view.initial_equity == Decimal("1000")


def test_json_execution_state_store_round_trips_snapshot(tmp_path) -> None:
    coordinator = ExecutionCoordinator()
    store = JsonExecutionStateStore(tmp_path / "execution.json")

    saved = store.save(coordinator)
    loaded = store.load()

    assert loaded == saved
    assert (tmp_path / "execution.json").exists()
