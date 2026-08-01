from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from kairospy.core.account import AccountBalance, AccountContext, AccountBookRef, AccountSource, Environment, PositionSnapshot
from kairospy.core.intent import IntentJournal, target_position_intent


def _demo_module():
    path = Path(__file__).resolve().parents[1] / "examples" / "account_status_demo.py"
    spec = importlib.util.spec_from_file_location("account_status_demo", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load account status demo")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True, slots=True)
class _Envelope:
    payload: object


class _Views:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def envelopes(self):
        return {"account.current.paper.binance.main.spot": _Envelope(self._payload)}


def test_account_status_demo_renders_account_report() -> None:
    module = _demo_module()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    account = AccountContext(AccountBookRef("binance", "main", "spot"), Environment.PAPER)
    account_view = SimpleNamespace(
        context=account,
        book=account.book,
        cash=Decimal("999"),
        equity=Decimal("1001"),
        net_profit=Decimal("1"),
        total_return=Decimal("0.001"),
        balances=(AccountBalance("USDT", Decimal("999"), Decimal("999"), Decimal("0"), AccountSource.LEDGER),),
        positions=(PositionSnapshot("market:binance:spot:btc_usdt", Decimal("0.01"), AccountSource.LEDGER, mark_price=Decimal("100000")),),
        stale=False,
        last_event_time=now,
    )
    intents = IntentJournal()
    intent = target_position_intent(
        strategy_id="demo",
        instrument_id="market:binance:spot:btc_usdt",
        target_quantity=Decimal("0.01"),
        at=now,
        reason="demo",
    )
    intents.record_intent(intent, at=now)
    result = SimpleNamespace(
        launch_id="demo-launch",
        mode=SimpleNamespace(value="paper"),
        runtime=SimpleNamespace(event_count=2, intent_count=1),
        initial_equity=Decimal("1000"),
        final_equity=Decimal("1001"),
        net_profit=Decimal("1"),
        total_return=Decimal("0.001"),
        views=_Views(account_view),
        account_view=account_view,
        fills=(SimpleNamespace(order_id="order-1", intent_id=str(intent.intent_id), instrument_id="market:binance:spot:btc_usdt", side="buy", quantity=Decimal("0.01"), price=Decimal("100000"), fee=Decimal("1"), occurred_at=now),),
        intents=intents,
    )

    output = module.render_account_report(result)

    assert "Launch" in output
    assert "Accounts" in output
    assert "Balances" in output
    assert "Positions" in output
    assert "Fills" in output
    assert "Intents" in output
    assert "account.current.paper.binance.main.spot" in output
    assert "market:binance:spot:btc_usdt" in output


def test_account_status_demo_requires_explicit_live_permission(tmp_path) -> None:
    module = _demo_module()
    config_path = tmp_path / "live.toml"
    config_path.write_text(
        "\n".join(
            [
                "[launch]",
                'id = "live-demo"',
                'mode = "live"',
                'strategy = "examples.strategies.btc_sma_backtest:strategy"',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="--allow-live"):
        module.launch_config(config_path)
