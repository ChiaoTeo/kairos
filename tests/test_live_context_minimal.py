from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from kairospy.core.account import AccountContext, AccountEventKind, AccountRef, AccountSnapshot, AccountSource, Environment, MarginScope, MarginState
from kairospy.context import DataContext, StrategyContext
from kairospy.data import DataSink, DataStore, InMemoryStreamFeed
from kairospy.integrations.ccxt import CcxtAccountPayloadAdapter
from kairospy.modes.live import JsonLiveRuntimeStateStore, LiveEngine, LiveStopToken
from kairospy.modes.live.private_stream import classify_balance_delta
from kairospy.runtime import IterableEventSource
from kairospy.core.reference import MarketResolver
from kairospy.strategy import StrategyBase


class AccountReadingStrategy(StrategyBase):
    strategy_id = "live-account-reader"

    def __init__(self, account_key: str) -> None:
        self.account_key = account_key
        self.pending_counts: list[int] = []

    def on_market(self, context: StrategyContext, event):
        account = context.account(self.account_key)
        self.pending_counts.append(len(account.pending_orders))
        return ()


class LiveOrderingStrategy(StrategyBase):
    strategy_id = "live-ordering"

    def on_market(self, context: StrategyContext, event):
        context.target_position(
            "BTC/USDT",
            Decimal("1"),
            limit_price=Decimal("99"),
            intent_id="enter-live",
        )
        return ()


class StopDrainOrderingStrategy(StrategyBase):
    strategy_id = "stop-drain-ordering"

    def __init__(self) -> None:
        self.stop_messages: list[str] = []

    def on_system(self, context: StrategyContext, event):
        if event.kind != "live.stop_requested":
            return ()
        stop_event = context.latest_data(domain="system", kind="live.stop_requested")
        self.stop_messages.append(str(stop_event.payload["reason"]))
        context.target_position(
            "BTC/USDT",
            Decimal("1"),
            limit_price=Decimal("98"),
            intent_id="stop-exit",
        )
        return ()


class MarginPayloadAdapter(CcxtAccountPayloadAdapter):
    def __init__(self, available: str) -> None:
        self.available = Decimal(available)

    def snapshot(self, context, raw_balance, raw_orders, *, observed_at):
        base = super().snapshot(context, raw_balance, raw_orders, observed_at=observed_at)
        return AccountSnapshot(
            context,
            balances=base.balances,
            margins=(
                MarginState(
                    "USDT",
                    Decimal("10"),
                    Decimal("5"),
                    AccountSource.VENUE,
                    scope=MarginScope.INSTRUMENT,
                    instrument_id="instrument:spot:btc:usdt",
                    available=self.available,
                ),
            ),
            positions=base.positions,
            open_orders=base.open_orders,
            observed_at=base.observed_at,
            source=base.source,
            raw=base.raw,
        )


class FakeLiveAccountGateway:
    def __init__(self) -> None:
        self.free = "900"
        self.used = "100"
        self.total = "1000"
        self.duplicate_stream_events = False
        self.include_existing_order = True
        self.bad_order_update = False
        self.stale_order_replay = False
        self.balance_update: dict[str, str] | None = None
        self.balance_reason: str | None = None
        self.balance_positions: list[dict[str, object]] = []
        self.created_orders: list[dict[str, object]] = []

    def fetch_balance(self, *, params=None):
        return {
            "free": {"USDT": self.free},
            "used": {"USDT": self.used},
            "total": {"USDT": self.total},
        }

    def fetch_open_orders(self, symbol=None, *, since=None, limit=None, params=None):
        assert symbol == "BTC/USDT"
        if not self.include_existing_order:
            return ()
        return (
            {
                "id": "venue-existing-1",
                "symbol": "BTC/USDT",
                "side": "buy",
                "type": "limit",
                "amount": "1",
                "filled": "0",
                "remaining": "1",
                "price": "100",
                "cost": "100",
            },
        )

    def create_order(self, symbol, *, side, type, amount, price=None, params=None):
        order = {
            "id": f"venue-created-{len(self.created_orders) + 1}",
            "symbol": symbol,
            "side": side,
            "type": type,
            "amount": str(amount),
            "price": None if price is None else str(price),
            "params": dict(params or {}),
        }
        self.created_orders.append(order)
        return order

    async def watch_balance(self, *, params=None):
        if self.balance_update is not None:
            event = {
                "free": {"USDT": self.balance_update["free"]},
                "used": {"USDT": self.balance_update["used"]},
                "total": {"USDT": self.balance_update["total"]},
            }
            if self.balance_reason is not None:
                event["type"] = self.balance_reason
            if self.balance_positions:
                event["positions"] = tuple(self.balance_positions)
            yield event

    async def watch_orders(self, symbol=None, *, since=None, limit=None, params=None):
        assert symbol == "BTC/USDT"
        if self.bad_order_update:
            yield {
                "symbol": "BTC/USDT",
                "side": "buy",
                "type": "limit",
                "amount": "1",
                "filled": "0.25",
                "remaining": "0.75",
                "price": "100",
                "status": "open",
                "timestamp": 1767225600000,
            }
            return
        event = {
            "id": "venue-existing-1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "limit",
            "amount": "1",
            "filled": "0.25",
            "remaining": "0.75",
            "price": "100",
            "status": "open",
            "timestamp": 1767225600000,
        }
        if self.stale_order_replay:
            yield {
                **event,
                "filled": "0.25",
                "remaining": "0.75",
                "timestamp": 1767225660000,
            }
            yield {
                **event,
                "filled": "0.10",
                "remaining": "0.90",
                "timestamp": 1767225600000,
            }
            return
        yield event
        if self.duplicate_stream_events:
            yield dict(event)

    async def watch_my_trades(self, symbol=None, *, since=None, limit=None, params=None):
        assert symbol == "BTC/USDT"
        event = {
            "id": "trade-1",
            "order": "venue-existing-1",
            "symbol": "BTC/USDT",
            "amount": "0.25",
            "price": "100",
            "cost": "25",
            "timestamp": 1767225660000,
            "fee": {"currency": "USDT", "cost": "0.1"},
        }
        yield event
        if self.duplicate_stream_events:
            yield dict(event)


class FailingEventSource:
    def events(self):
        raise RuntimeError("market stream disconnected")
        yield


class RecordingMonitor:
    def __init__(self) -> None:
        self.events = []

    def heartbeat(self, event) -> None:
        self.events.append(event)


def test_strategy_data_view_reads_historical_rows_without_knowing_store() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write("market.ohlcv.btc_usdt.1m", [
            {"time": "2026-01-01T00:00:00+00:00", "close": 100},
            {"time": "2026-01-01T00:01:00+00:00", "close": 101},
        ])
        ctx = DataContext(store)

        bars = ctx.attach("bars", dataset="market.ohlcv.btc_usdt.1m")

        assert bars.rows(limit=1)[0]["close"] == 100
        assert bars.latest() == {"time": "2026-01-01T00:01:00+00:00", "close": 101}
        assert ctx["bars"].rows(columns=("close",)) == [{"close": 100}, {"close": 101}]


def test_data_view_consumes_stream_events_without_knowing_feed() -> None:
    async def scenario() -> None:
        feed = InMemoryStreamFeed()
        ctx = DataContext(DataStore(":unused:", storage_format="jsonl"), stream_feed=feed)
        book = ctx.attach("book", stream="binance.btc_usdt.orderbook", mode="stream")

        events_iter = book.events()
        await feed.publish("binance.btc_usdt.orderbook", {
            "time": "2026-01-01T00:00:00+00:00",
            "bid": 100,
        })
        await feed.close("binance.btc_usdt.orderbook")

        assert [event async for event in events_iter] == [
            {"time": "2026-01-01T00:00:00+00:00", "bid": 100}
        ]

    asyncio.run(scenario())


def test_stream_events_can_be_persisted_through_data_sink() -> None:
    async def scenario() -> None:
        with TemporaryDirectory() as temporary:
            feed = InMemoryStreamFeed()
            store = DataStore(temporary, storage_format="jsonl")
            sink = DataSink(store, "market.quote.btc_usdt")

            events = feed.subscribe("binance.btc_usdt.quote")
            consumer = asyncio.create_task(sink.consume(events, limit=2))
            await feed.publish("binance.btc_usdt.quote", {
                "time": "2026-01-01T00:00:00+00:00",
                "bid": 100,
            })
            await feed.publish("binance.btc_usdt.quote", {
                "time": "2026-01-01T00:00:01+00:00",
                "bid": 101,
            })

            assert await consumer == 2
            assert [row["bid"] for row in store.read_rows("market.quote.btc_usdt")] == [100, 101]

    asyncio.run(scenario())


def test_data_context_snapshot_is_only_bindings() -> None:
    ctx = DataContext(DataStore(".kairos/data", storage_format="jsonl"))
    ctx.attach("bars", dataset="market.ohlcv.btc_usdt.1m")
    ctx.attach("book", stream="binance.btc_usdt.orderbook", mode="stream")

    assert ctx.snapshot() == {
        "bindings": {
            "bars": {
                "name": "bars",
                "dataset": "market.ohlcv.btc_usdt.1m",
                "mode": "history",
            },
            "book": {
                "name": "book",
                "stream": "binance.btc_usdt.orderbook",
                "mode": "stream",
            },
        }
    }


def test_both_mode_requires_history_and_stream_sources() -> None:
    ctx = DataContext(DataStore(".kairos/data", storage_format="jsonl"))

    with pytest.raises(ValueError, match="dataset and stream"):
        ctx.attach("mixed", dataset="market.ohlcv.btc_usdt.1m", mode="both")
    with pytest.raises(ValueError, match="dataset and stream"):
        ctx.attach("mixed", stream="binance.btc_usdt.orderbook", mode="both")


def test_live_strategy_runner_bootstraps_account_and_applies_order_ws_updates() -> None:
    account = AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)
    account_key = "account.current.live.binance.main.spot"
    gateway = FakeLiveAccountGateway()
    strategy = AccountReadingStrategy(account_key)
    runner = LiveEngine(
        strategy,
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        gateway,
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
    )

    result = runner.run(
        IterableEventSource(
            "binance.quote.BTC/USDT",
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": "100",
                    "ask": "101",
                }
            ],
        ),
        symbol="BTC/USDT",
        max_order_events=1,
    )

    assert result.runtime.event_count == 1
    assert strategy.pending_counts == [1]
    assert result.bootstrap.imported_orders[0].local_order_id == "external:binance:main:spot:venue-existing-1"
    assert result.account_view.pending_orders[0].status.value == "partially_filled"
    assert result.account_view.pending_orders[0].remaining_quantity == Decimal("0.75")

    gateway.free = "875"
    gateway.used = "125"
    gateway.include_existing_order = False
    reconciliation = runner.reconcile_account(
        symbol="BTC/USDT",
        at=result.account_view.last_event_time,
    )

    assert [(item.kind, item.key, item.local, item.external) for item in reconciliation.differences] == [
        ("balance.free", "USDT", Decimal("900"), Decimal("875")),
        ("balance.locked", "USDT", Decimal("100"), Decimal("125")),
        ("open_order.present", "venue-existing-1", Decimal("1"), Decimal("0")),
        ("pending_order.venue_present", "venue-existing-1", Decimal("1"), Decimal("0")),
    ]
    assert reconciliation.event.payload.projection.balance("USDT").free == Decimal("875")


def test_live_strategy_runner_applies_private_trade_updates_to_ledger() -> None:
    account = AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)
    runner = LiveEngine(
        AccountReadingStrategy("account.current.live.binance.main.spot"),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        FakeLiveAccountGateway(),
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
    )

    result = runner.run(
        IterableEventSource(
            "empty",
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": "100",
                    "ask": "101",
                }
            ],
        ),
        symbol="BTC/USDT",
        max_trade_events=1,
    )

    assert [event.cash_delta for event in result.coordinator.ledger.events] == [
        Decimal("-25"),
        Decimal("-0.1"),
    ]
    assert result.account_view.pending_orders[0].filled_quantity == Decimal("0.25")


def test_live_strategy_runner_records_balance_total_delta_as_account_adjustment() -> None:
    account = AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)
    gateway = FakeLiveAccountGateway()
    gateway.balance_update = {"free": "910", "used": "100", "total": "1010"}
    runner = LiveEngine(
        AccountReadingStrategy("account.current.live.binance.main.spot"),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        gateway,
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
    )

    result = runner.run(
        IterableEventSource(
            "empty",
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": "100",
                    "ask": "101",
                }
            ],
        ),
        symbol="BTC/USDT",
        max_balance_events=1,
    )

    assert [(event.kind, event.cash_delta) for event in result.coordinator.ledger.events] == [
        (AccountEventKind.ADJUSTMENT, Decimal("10")),
    ]
    assert result.account_view.projection.balance("USDT").total == Decimal("1010")


def test_live_strategy_runner_updates_margin_view_from_balance_stream() -> None:
    account = AccountContext(AccountRef("binance", "main", "um_futures"), Environment.LIVE)
    gateway = FakeLiveAccountGateway()
    gateway.balance_update = {"free": "900", "used": "100", "total": "1000"}
    gateway.balance_positions = [
        {
            "symbol": "BTC/USDT",
            "marginAsset": "USDT",
            "initialMargin": "40",
            "maintenanceMargin": "20",
            "availableMargin": "50",
        }
    ]
    runner = LiveEngine(
        AccountReadingStrategy("account.current.live.binance.main.um_futures"),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        gateway,
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
    )

    result = runner.run(
        IterableEventSource(
            "empty",
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": "100",
                    "ask": "101",
                }
            ],
        ),
        symbol="BTC/USDT",
        max_balance_events=1,
    )

    assert [
        (item.scope, item.instrument_id, item.available)
        for item in result.account_view.margins
    ] == [(MarginScope.INSTRUMENT, "instrument:unknown:btc:usdt", Decimal("50"))]


@pytest.mark.parametrize(
    ("reason", "total", "expected_kind", "expected_delta"),
    [
        ("deposit", "1010", AccountEventKind.DEPOSIT, Decimal("10")),
        ("withdrawal", "990", AccountEventKind.WITHDRAWAL, Decimal("-10")),
        ("funding", "995", AccountEventKind.FUNDING, Decimal("-5")),
        ("unknown", "1005", AccountEventKind.ADJUSTMENT, Decimal("5")),
    ],
)
def test_live_strategy_runner_classifies_explicit_balance_delta_reason(
    reason: str,
    total: str,
    expected_kind: AccountEventKind,
    expected_delta: Decimal,
) -> None:
    account = AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)
    gateway = FakeLiveAccountGateway()
    gateway.balance_update = {"free": total, "used": "0", "total": total}
    gateway.balance_reason = reason
    runner = LiveEngine(
        AccountReadingStrategy("account.current.live.binance.main.spot"),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        gateway,
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
    )

    result = runner.run(
        IterableEventSource(
            "empty",
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": "100",
                    "ask": "101",
                }
            ],
        ),
        symbol="BTC/USDT",
        max_balance_events=1,
    )

    assert [(event.kind, event.cash_delta) for event in result.coordinator.ledger.events] == [
        (expected_kind, expected_delta),
    ]


def test_balance_delta_classifier_reads_nested_exchange_reason() -> None:
    assert classify_balance_delta({"info": {"eventType": "funding-fee"}}, Decimal("1")) is AccountEventKind.FUNDING
    assert classify_balance_delta({"info": {"reason": "transfer out"}}, Decimal("-1")) is AccountEventKind.WITHDRAWAL


def test_live_strategy_runner_deduplicates_replayed_private_stream_events() -> None:
    account = AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)
    gateway = FakeLiveAccountGateway()
    gateway.duplicate_stream_events = True
    runner = LiveEngine(
        AccountReadingStrategy("account.current.live.binance.main.spot"),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        gateway,
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
    )

    result = runner.run(
        IterableEventSource(
            "empty",
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": "100",
                    "ask": "101",
                }
            ],
        ),
        symbol="BTC/USDT",
        max_order_events=2,
        max_trade_events=2,
    )

    assert [event.cash_delta for event in result.coordinator.ledger.events] == [
        Decimal("-25"),
        Decimal("-0.1"),
    ]
    assert result.account_view.pending_orders[0].filled_quantity == Decimal("0.25")
    assert result.account_view.event_count == 3


def test_live_engine_restores_private_stream_watermark_and_ledger_after_restart(tmp_path: Path) -> None:
    account = AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)
    state_store = JsonLiveRuntimeStateStore(tmp_path / "live-state.json")
    first = LiveEngine(
        AccountReadingStrategy("account.current.live.binance.main.spot"),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        FakeLiveAccountGateway(),
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
        state_store=state_store,
    )
    first.run(
        IterableEventSource(
            "empty",
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": "100",
                    "ask": "101",
                }
            ],
        ),
        symbol="BTC/USDT",
        max_trade_events=1,
    )

    second = LiveEngine(
        AccountReadingStrategy("account.current.live.binance.main.spot"),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        FakeLiveAccountGateway(),
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
        state_store=state_store,
    )
    result = second.run(
        IterableEventSource(
            "empty",
            [
                {
                    "time": "2026-01-01T00:01:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": "100",
                    "ask": "101",
                }
            ],
        ),
        symbol="BTC/USDT",
        max_trade_events=1,
    )

    assert [event.cash_delta for event in result.coordinator.ledger.events] == [
        Decimal("-25"),
        Decimal("-0.1"),
    ]
    assert result.account_view.pending_orders[0].filled_quantity == Decimal("0.25")


def test_live_loop_reports_iteration_failure_and_continues_with_backoff_disabled(tmp_path: Path) -> None:
    account = AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)
    state_store = JsonLiveRuntimeStateStore(tmp_path / "live-state.json")
    monitor = RecordingMonitor()
    runner = LiveEngine(
        AccountReadingStrategy("account.current.live.binance.main.spot"),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        FakeLiveAccountGateway(),
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
        state_store=state_store,
    )

    def source_factory(iteration: int):
        if iteration == 1:
            return FailingEventSource()
        return IterableEventSource(
            "binance.quote.BTC/USDT",
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": "100",
                    "ask": "101",
                }
            ],
        )

    result = runner.run_loop(
        source_factory,
        symbol="BTC/USDT",
        max_iterations=2,
        monitor=monitor,
        retry_backoff_seconds=0,
    )

    assert [iteration.succeeded for iteration in result.iterations] == [False, True]
    assert [incident.kind for incident in result.incidents] == ["live.loop.error"]
    assert result.latest.runtime.event_count == 1
    assert state_store.load() is not None
    assert [(event.status, event.iteration, event.consecutive_failures) for event in monitor.events] == [
        ("starting", 1, 0),
        ("failed", 1, 1),
        ("starting", 2, 1),
        ("succeeded", 2, 0),
    ]


def test_live_loop_stop_predicate_can_end_after_successful_iteration() -> None:
    account = AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)
    runner = LiveEngine(
        AccountReadingStrategy("account.current.live.binance.main.spot"),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        FakeLiveAccountGateway(),
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
    )

    result = runner.run_loop(
        lambda iteration: IterableEventSource(
            "binance.quote.BTC/USDT",
            [
                {
                    "time": f"2026-01-01T00:0{iteration}:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": "100",
                    "ask": "101",
                }
            ],
        ),
        symbol="BTC/USDT",
        stop=lambda iteration: iteration.succeeded,
        retry_backoff_seconds=0,
    )

    assert len(result.iterations) == 1
    assert result.succeeded_count == 1
    assert result.latest.account_view.event_count == 1


def test_live_loop_stop_token_can_prevent_starting_iterations() -> None:
    account = AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)
    token = LiveStopToken()
    token.request_stop("shutdown")
    monitor = RecordingMonitor()
    runner = LiveEngine(
        AccountReadingStrategy("account.current.live.binance.main.spot"),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        FakeLiveAccountGateway(),
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
    )

    result = runner.run_loop(
        lambda iteration: FailingEventSource(),
        symbol="BTC/USDT",
        stop_token=token,
        monitor=monitor,
        retry_backoff_seconds=0,
    )

    assert len(result.iterations) == 1
    assert result.iterations[0].succeeded
    assert token.reason == "shutdown"
    assert [(event.status, event.iteration, event.stop_reason) for event in monitor.events] == [
        ("draining", 1, "shutdown"),
        ("stopped", 1, "shutdown"),
    ]


def test_live_loop_stop_token_can_stop_after_current_iteration() -> None:
    account = AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)
    token = LiveStopToken()
    monitor = RecordingMonitor()
    runner = LiveEngine(
        AccountReadingStrategy("account.current.live.binance.main.spot"),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        FakeLiveAccountGateway(),
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
    )

    def source_factory(iteration: int):
        token.request_stop("single iteration")
        return IterableEventSource(
            "binance.quote.BTC/USDT",
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": "100",
                    "ask": "101",
                }
            ],
        )

    result = runner.run_loop(
        source_factory,
        symbol="BTC/USDT",
        max_iterations=3,
        stop_token=token,
        monitor=monitor,
        retry_backoff_seconds=0,
    )

    assert len(result.iterations) == 2
    assert result.iterations[0].result.runtime.event_count == 1
    assert result.latest.runtime.event_count == 0
    assert token.reason == "single iteration"
    assert [(event.status, event.iteration, event.stop_reason) for event in monitor.events] == [
        ("starting", 1, ""),
        ("succeeded", 1, ""),
        ("draining", 2, "single iteration"),
        ("stopped", 2, "single iteration"),
    ]


def test_live_loop_stop_token_drains_strategy_intents_before_stopping() -> None:
    account = AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)
    token = LiveStopToken()
    token.request_stop("operator shutdown")
    monitor = RecordingMonitor()
    gateway = FakeLiveAccountGateway()
    gateway.include_existing_order = False
    strategy = StopDrainOrderingStrategy()
    resolver = MarketResolver(default_venue="binance", default_market="spot")
    runner = LiveEngine(
        strategy,
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        gateway,
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
        market_resolver=resolver,
    )

    result = runner.run_loop(
        lambda iteration: FailingEventSource(),
        symbol="BTC/USDT",
        stop_token=token,
        monitor=monitor,
        retry_backoff_seconds=0,
    )

    assert len(result.iterations) == 1
    assert strategy.stop_messages == ["operator shutdown"]
    assert gateway.created_orders == [
        {
            "id": "venue-created-1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "limit",
            "amount": "1",
            "price": "98",
            "params": {},
        }
    ]
    assert any(record.hook == "on_system" and record.intents for record in result.latest.runtime.callbacks)
    assert result.latest.coordinator.orders.states[0].venue_order_id == "venue-created-1"
    assert [(event.status, event.iteration, event.stop_reason) for event in monitor.events] == [
        ("draining", 1, "operator shutdown"),
        ("stopped", 1, "operator shutdown"),
    ]


def test_live_loop_stop_drain_timeout_skips_unsettled_strategy_intents() -> None:
    account = AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)
    token = LiveStopToken()
    token.request_stop("operator shutdown")
    gateway = FakeLiveAccountGateway()
    gateway.include_existing_order = False
    strategy = StopDrainOrderingStrategy()
    resolver = MarketResolver(default_venue="binance", default_market="spot")
    runner = LiveEngine(
        strategy,
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        gateway,
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
        market_resolver=resolver,
    )

    result = runner.run_loop(
        lambda iteration: FailingEventSource(),
        symbol="BTC/USDT",
        stop_token=token,
        retry_backoff_seconds=0,
        stop_drain_timeout_seconds=0,
    )

    assert len(result.iterations) == 1
    assert strategy.stop_messages == ["operator shutdown"]
    assert gateway.created_orders == []
    assert [state.status.value for state in result.latest.runtime.intent_states] == ["created"]
    assert result.latest.account_view.pending_orders == ()


def test_live_strategy_runner_ignores_stale_private_order_replay() -> None:
    account = AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)
    gateway = FakeLiveAccountGateway()
    gateway.stale_order_replay = True
    runner = LiveEngine(
        AccountReadingStrategy("account.current.live.binance.main.spot"),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        gateway,
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
    )

    result = runner.run(
        IterableEventSource(
            "empty",
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": "100",
                    "ask": "101",
                }
            ],
        ),
        symbol="BTC/USDT",
        max_order_events=2,
    )

    assert result.account_view.pending_orders[0].filled_quantity == Decimal("0.25")
    assert result.account_view.pending_orders[0].remaining_quantity == Decimal("0.75")
    assert result.account_view.event_count == 2


def test_live_engine_reports_bad_private_stream_event_as_incident_and_continues() -> None:
    account = AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)
    account_key = "account.current.live.binance.main.spot"
    gateway = FakeLiveAccountGateway()
    gateway.bad_order_update = True
    strategy = AccountReadingStrategy(account_key)
    runner = LiveEngine(
        strategy,
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        gateway,
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
    )

    result = runner.run(
        IterableEventSource(
            "binance.quote.BTC/USDT",
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": "100",
                    "ask": "101",
                }
            ],
        ),
        symbol="BTC/USDT",
        max_order_events=1,
    )

    assert result.runtime.event_count == 1
    assert strategy.pending_counts == [1]
    assert [incident.kind for incident in result.incidents] == ["live.account.order.error"]
    assert result.incidents[0].payload["error_type"] == "ValueError"


def test_live_engine_submits_strategy_target_position_to_broker_and_updates_account_view() -> None:
    account = AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)
    gateway = FakeLiveAccountGateway()
    gateway.include_existing_order = False
    resolver = MarketResolver(default_venue="binance", default_market="spot")
    runner = LiveEngine(
        LiveOrderingStrategy(),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        gateway,
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
        market_resolver=resolver,
    )

    result = runner.run(
        IterableEventSource(
            "binance.quote.BTC/USDT",
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": "100",
                    "ask": "101",
                }
            ],
        ),
        symbol="BTC/USDT",
        order_params={"timeInForce": "GTC"},
    )

    assert gateway.created_orders == [
        {
            "id": "venue-created-1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "limit",
            "amount": "1",
            "price": "99",
            "params": {"timeInForce": "GTC"},
        }
    ]
    assert [state.status.value for state in result.runtime.intent_states] == ["ordering"]
    assert result.account_view.pending_orders[0].local_order_id == "enter-live-live-order"
    assert result.account_view.pending_orders[0].venue_order_id == "venue-created-1"


def test_live_engine_applies_margin_check_before_submitting_strategy_order() -> None:
    account = AccountContext(AccountRef("binance", "main", "um_futures"), Environment.LIVE)
    gateway = FakeLiveAccountGateway()
    gateway.include_existing_order = False
    resolver = MarketResolver(default_venue="binance", default_market="spot")
    runner = LiveEngine(
        LiveOrderingStrategy(),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        gateway,
        account_payload_adapter=MarginPayloadAdapter("10"),
        equity_currency="USDT",
        market_resolver=resolver,
    )

    result = runner.run(
        IterableEventSource(
            "binance.quote.BTC/USDT",
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": "100",
                    "ask": "101",
                }
            ],
        ),
        symbol="BTC/USDT",
        order_params={
            "timeInForce": "GTC",
            "marginCurrency": "USDT",
            "marginNotional": "200",
            "marginLeverage": "5",
        },
    )

    assert gateway.created_orders == []
    assert [state.status.value for state in result.runtime.intent_states] == ["failed"]
    assert result.account_view.pending_orders == ()


def test_live_engine_filters_local_margin_params_after_margin_check_accepts() -> None:
    account = AccountContext(AccountRef("binance", "main", "um_futures"), Environment.LIVE)
    gateway = FakeLiveAccountGateway()
    gateway.include_existing_order = False
    resolver = MarketResolver(default_venue="binance", default_market="spot")
    runner = LiveEngine(
        LiveOrderingStrategy(),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        gateway,
        account_payload_adapter=MarginPayloadAdapter("50"),
        equity_currency="USDT",
        market_resolver=resolver,
    )

    result = runner.run(
        IterableEventSource(
            "binance.quote.BTC/USDT",
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": "100",
                    "ask": "101",
                }
            ],
        ),
        symbol="BTC/USDT",
        order_params={
            "timeInForce": "GTC",
            "marginCurrency": "USDT",
            "marginNotional": "200",
            "marginLeverage": "5",
        },
    )

    assert gateway.created_orders[0]["params"] == {"timeInForce": "GTC"}
    assert result.account_view.pending_orders[0].status.value == "acknowledged"
    assert result.coordinator.reservations.reservations[0].amount == Decimal("40")
