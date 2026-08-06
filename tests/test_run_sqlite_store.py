from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from kairospy.application.support.composition.application.artifacts import launch_output
from kairospy.infrastructure.persistence.services.artifacts.run_sqlite import RunSqliteStore


def test_run_sqlite_store_persists_metadata_records_and_current_state(tmp_path: Path) -> None:
    store = RunSqliteStore(tmp_path / "run.sqlite")

    store.write_json("summary", {"run_id": "run-1", "final_equity": "101"})
    store.append_record("records", {"record_id": "r-1", "time": "2026-01-01T00:00:00+00:00"})
    store.update_current("account", {"equity": "101"})

    assert store.read_json("summary.json")["run_id"] == "run-1"
    assert store.read_records("records") == [{"record_id": "r-1", "time": "2026-01-01T00:00:00+00:00"}]
    assert store.read_current("account")["equity"] == "101"


def test_launch_output_writes_run_sqlite_without_projection_files(tmp_path: Path) -> None:
    output = launch_output(tmp_path, launch_id="run-1", mode="backtest")
    result = SimpleNamespace(
        launch_id="run-1",
        mode="backtest",
        runtime=SimpleNamespace(program_id="strategy", event_count=2),
        metrics={"sharpe": "1.2"},
        equity_curve=(SimpleNamespace(time="2026-01-01T00:00:00+00:00", equity="100"),),
        fills=(),
        trades=(),
        intents=None,
        initial_equity="100",
        final_equity="100",
        net_profit="0",
        total_return="0",
    )

    output.write_result(result=result, normalized_config={"strategy": "strategy"})

    assert (tmp_path / "run.sqlite").exists()
    assert not (tmp_path / "summary.json").exists()
    store = RunSqliteStore(tmp_path / "run.sqlite")
    assert store.read_json("summary")["launch_id"] == "run-1"
    assert len(store.read_records("equity")) == 1
    with sqlite3.connect(tmp_path / "run.sqlite") as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"run_metadata", "run_records", "run_current"} <= tables
