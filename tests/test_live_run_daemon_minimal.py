from __future__ import annotations

import json
import threading
import time
from io import StringIO
from decimal import Decimal

from kairospy.accounts import AccountContext, AccountRef, Environment
from kairospy.context import DataContext
from kairospy.data import DataStore
from kairospy.integrations.ccxt import CcxtAccountPayloadAdapter
from kairospy.live import LiveEngine, LiveEngineDaemonTarget
from kairospy.runtime import IterableEventSource
from kairospy.runtime.daemon import LiveRunControlPlane, LiveRunDaemonPhase
from kairospy.surface import cli
from kairospy.strategy import StrategyBase


class DaemonAccountReadingStrategy(StrategyBase):
    strategy_id = "daemon-live-account-reader"


class DaemonFakeGateway:
    def fetch_balance(self, *, params=None):
        return {
            "free": {"USDT": "1000"},
            "used": {"USDT": "0"},
            "total": {"USDT": "1000"},
        }

    def fetch_open_orders(self, symbol=None, *, params=None):
        return []

    def watch_balance(self, *, params=None):
        return _empty_async()

    def watch_orders(self, symbol=None, *, params=None):
        return _empty_async()

    def watch_my_trades(self, symbol=None, *, params=None):
        return _empty_async()


def test_live_run_foreground_publishes_heartbeats_and_stops_after_duration(tmp_path) -> None:
    control = LiveRunControlPlane("duration-run", root=tmp_path)

    status = control.run_foreground(poll_seconds=0.01, duration_seconds=0.02)

    assert status.phase is LiveRunDaemonPhase.STOPPED
    assert status.reason == "duration elapsed"
    stored = control.status()
    assert stored.phase is LiveRunDaemonPhase.STOPPED
    assert stored.heartbeat_at is not None


def test_live_run_stop_request_stops_foreground_daemon(tmp_path) -> None:
    control = LiveRunControlPlane("operator-stop", root=tmp_path)
    result = {}

    thread = threading.Thread(
        target=lambda: result.setdefault(
            "status",
            control.run_foreground(poll_seconds=0.01),
        ),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and control.status().phase is not LiveRunDaemonPhase.RUNNING:
        time.sleep(0.01)

    command = control.request_stop(reason="test stop", actor="pytest")
    thread.join(timeout=1.0)

    assert command["reason"] == "test stop"
    assert not thread.is_alive()
    assert result["status"].phase is LiveRunDaemonPhase.STOPPED
    assert result["status"].reason == "test stop"


def test_cli_run_live_status_outputs_json() -> None:
    stdout = StringIO()

    result = cli.execute_argv(["run", "live", "status", "--run-id", "cli-smoke"], stdout)

    assert result == 0
    payload = json.loads(stdout.getvalue())
    assert payload["run_id"] == "cli-smoke"
    assert payload["phase"] in {"created", "stopped", "running", "stale"}


def test_live_engine_daemon_target_runs_loop_and_persists_iteration_status(tmp_path) -> None:
    control = LiveRunControlPlane("target-run", root=tmp_path)
    account = AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)
    engine = LiveEngine(
        DaemonAccountReadingStrategy(),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        DaemonFakeGateway(),
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
    )
    target = LiveEngineDaemonTarget(
        engine,
        lambda iteration: IterableEventSource(
            "binance.quote.BTC/USDT",
            [
                {
                    "time": f"2026-01-01T00:0{iteration}:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": Decimal("100"),
                    "ask": Decimal("101"),
                }
            ],
        ),
        symbol="BTC/USDT",
        max_iterations=1,
        retry_backoff_seconds=0,
    )

    status = control.run_foreground(poll_seconds=0.01, target=target)

    assert status.phase is LiveRunDaemonPhase.STOPPED
    assert status.reason == "target completed"
    assert status.result["iterations"] == 1
    assert status.result["succeeded_count"] == 1
    assert status.result["latest_strategy_id"] == "daemon-live-account-reader"
    assert control.status().metrics["iteration"] == 1


async def _empty_async():
    if False:
        yield None
