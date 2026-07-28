from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from kairospy.infrastructure.data import DataId, DataStore


@pytest.mark.parametrize("storage_format", ["parquet", "jsonl"])
def test_data_id_maps_to_single_data_file_without_metadata(storage_format: str) -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format=storage_format)
        path = store.write("research.signal", [
            {"time": "2026-01-01T00:00:00+00:00", "value": 1},
        ])

        assert path == Path(temporary) / "datasets" / "research" / "signal" / f"data.{storage_format}"
        assert not (Path(temporary) / "datasets" / "research" / "signal" / "meta.json").exists()
        assert store.list() == (DataId("research.signal"),)


@pytest.mark.parametrize("storage_format", ["parquet", "jsonl"])
def test_write_requires_time_and_rejects_naive_time(storage_format: str) -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format=storage_format)

        with pytest.raises(ValueError, match="time field"):
            store.write("research.signal", [{"value": 1}])
        with pytest.raises(ValueError, match="timezone-aware"):
            store.write("research.signal", [{"time": "2026-01-01T00:00:00", "value": 1}])


@pytest.mark.parametrize("storage_format", ["parquet", "jsonl"])
def test_read_filters_by_time_columns_and_limit(storage_format: str) -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format=storage_format)
        store.write("research.signal", [
            {"time": datetime(2026, 1, 2, tzinfo=timezone.utc), "value": 2, "extra": "b"},
            {"time": "2026-01-01T00:00:00+00:00", "value": 1, "extra": "a"},
            {"time": "2026-01-03T00:00:00+00:00", "value": 3, "extra": "c"},
        ])

        rows = store.read(
            "research.signal",
            start="2026-01-02T00:00:00+00:00",
            end="2026-01-04T00:00:00+00:00",
            columns=("time", "value"),
            limit=1,
        )

        assert rows.to_dict("records") == [{"time": "2026-01-02T00:00:00+00:00", "value": 2}]


@pytest.mark.parametrize("storage_format", ["parquet", "jsonl"])
def test_alias_is_only_a_pointer(storage_format: str) -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format=storage_format)
        store.write("market.ohlcv.btc_usdt.1d", [
            {"time": "2026-01-01T00:00:00+00:00", "close": 100},
        ])

        alias_path = store.alias("market.ohlcv.btc_usdt.1d", "btc_daily")

        assert alias_path.read_text(encoding="utf-8").strip() == "market.ohlcv.btc_usdt.1d"
        assert store.read_rows("btc_daily")[0]["close"] == 100


@pytest.mark.parametrize("storage_format", ["parquet", "jsonl"])
def test_delete_and_replace_window(storage_format: str) -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format=storage_format)
        store.write("research.signal", [
            {"time": "2026-01-01T00:00:00+00:00", "value": 1},
            {"time": "2026-01-02T00:00:00+00:00", "value": 2},
            {"time": "2026-01-03T00:00:00+00:00", "value": 3},
        ])

        deleted = store.delete_window(
            "research.signal",
            start="2026-01-02T00:00:00+00:00",
            end="2026-01-03T00:00:00+00:00",
        )
        replaced = store.replace_window(
            "research.signal",
            [{"time": "2026-01-03T00:00:00+00:00", "value": 30}],
            start="2026-01-03T00:00:00+00:00",
            end="2026-01-04T00:00:00+00:00",
        )

        assert deleted["deleted_rows"] == 1
        assert replaced["replaced_rows"] == 1
        assert [row["value"] for row in store.read_rows("research.signal")] == [1, 30]


def test_default_storage_format_is_parquet() -> None:
    with TemporaryDirectory() as temporary:
        path = DataStore(temporary).write("research.signal", [
            {"time": "2026-01-01T00:00:00+00:00", "value": 1},
        ])

        assert path.name == "data.parquet"
