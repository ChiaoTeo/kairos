from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any, cast

from kairospy.infrastructure.transport.market import decode_market_event
from kairospy.infrastructure.transport.account import decode_account_event
from kairospy.infrastructure.transport.execution import decode_execution_event
from kairospy.infrastructure.transport.risk import decode_risk_event
from kairospy.infrastructure.transport.generated.kairos.account.v1.AccountsSnapshot import (
    AccountsSnapshot,
)
from kairospy.infrastructure.transport.generated.kairos.execution.v1.OrdersSnapshot import (
    OrdersSnapshot,
)
from kairospy.infrastructure.transport.generated.kairos.intent.v1.IntentSnapshot import (
    IntentSnapshot,
)
from kairospy.infrastructure.transport.generated.kairos.market.v1.MarketDataSnapshot import (
    MarketDataSnapshot,
)
from kairospy.infrastructure.transport.generated.kairos.market.v1.OrderBookSnapshot import (
    OrderBookSnapshot,
)
from kairospy.infrastructure.transport.generated.kairos.risk.v1.RiskSnapshot import RiskSnapshot


FIXTURES = Path(__file__).parent / "fixtures" / "active_wire"


def _market_fixtures() -> dict[str, bytes]:
    values = json.loads((FIXTURES / "v1.json").read_text())
    values.update(json.loads((FIXTURES / "market_aux_v1.json").read_text()))
    return {name: bytes.fromhex(value) for name, value in values.items()}


def test_active_wire_fixture_generator_is_deterministic() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/generate_active_wire_fixtures.py"],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    generated = json.loads(result.stdout)
    checked_in = json.loads((FIXTURES / "v1.json").read_text())
    checked_in.update(json.loads((FIXTURES / "market_aux_v1.json").read_text()))
    assert generated == checked_in


def test_python_decodes_the_same_active_market_roots_as_rust() -> None:
    fixtures = _market_fixtures()

    records = [
        decode_market_event(fixtures["market.quote.MQT1"]),
        decode_market_event(fixtures["market.trade.MTR1"]),
        decode_market_event(fixtures["market.bar.MBA1"]),
        decode_market_event(fixtures["market.greeks.MGR1"]),
    ]
    assert [(record.kind, record.sequence) for record in records] == [
        ("quote", 1),
        ("trade", 2),
        ("bar", 3),
        ("greeks", 4),
    ]
    assert records[0].payload.market_id == "market:fixture"
    assert records[1].payload.trade_id == "trade:fixture"
    assert records[2].payload.timeframe == "1m"
    assert (
        records[3].payload.delta.mantissa,
        records[3].payload.delta.scale,
    ) == (525, 3)

    payload = fixtures["market.current.PMC1"]
    assert MarketDataSnapshot.MarketDataSnapshotBufferHasIdentifier(payload, 0)
    snapshot = cast(Any, MarketDataSnapshot.GetRootAs(payload, 0))
    assert snapshot.Header().Version() == 1
    assert snapshot.Header().Generation() == 7
    assert snapshot.Payload().QuoteCount() == 0

    auxiliary = [
        decode_market_event(fixtures["market.rate.MRA1"]),
        decode_market_event(fixtures["market.ticker_24h.MT24"]),
        decode_market_event(fixtures["market.mark_price.MMP1"]),
        decode_market_event(fixtures["market.orderbook.MOB1"]),
        decode_market_event(fixtures["market.index_price.MIP1"]),
        decode_market_event(fixtures["market.funding_rate.MFR1"]),
        decode_market_event(fixtures["market.open_interest.MOI1"]),
        decode_market_event(fixtures["market.instrument_status.MIS1"]),
    ]
    assert [record.sequence for record in auxiliary] == list(range(5, 13))
    orderbook = cast(
        Any,
        OrderBookSnapshot.GetRootAs(
            fixtures["market.orderbook.current.PMB1"], 0
        ),
    )
    assert orderbook.Header().Generation() == 7


def test_python_decodes_account_execution_and_risk_active_roots() -> None:
    fixtures = _market_fixtures()

    account = decode_account_event(fixtures["account.event.ACE1"])
    assert account.sequence == 1
    assert account.account_id == "account:fixture"
    assert account.changes[0].kind == "status_changed"
    account_current = cast(
        Any, AccountsSnapshot.GetRootAs(fixtures["account.current.AAC1"], 0)
    )
    assert account_current.Header().Generation() == 7
    assert account_current.Payload().AccountCount() == 0

    execution = decode_execution_event(fixtures["execution.event.EXE1"])
    assert execution.sequence == 1
    assert execution.changes[0].kind == "order_update"
    assert execution.changes[0].payload["order_id"] == "order:fixture"
    orders = cast(
        Any, OrdersSnapshot.GetRootAs(fixtures["execution.orders.PEO1"], 0)
    )
    assert orders.Header().Generation() == 7
    intents = cast(
        Any, IntentSnapshot.GetRootAs(fixtures["execution.intents.PIJ1"], 0)
    )
    assert intents.Header().Generation() == 7

    risk = decode_risk_event(fixtures["risk.event.RKE1"])
    assert risk.sequence == 1
    assert risk.kind == "decision_evaluated"
    assert risk.payload["decision_id"] == "decision:fixture"
    risk_current = cast(
        Any, RiskSnapshot.GetRootAs(fixtures["risk.current.PRK1"], 0)
    )
    assert risk_current.Header().Generation() == 7
    assert risk_current.Payload().BudgetCount() == 0
