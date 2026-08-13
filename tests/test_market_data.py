from __future__ import annotations

import json
from pathlib import Path

from kairospy.application.market import (
    MarketDataApplication,
    materialize_replay_file,
    validate_replay_window,
)


def _bar(time: int) -> dict:
    return {
        "Bar": {
            "market_id": "market:binance:spot:BTCUSDT",
            "instrument_id": "instrument:binance:BTCUSDT",
            "timeframe": "1m",
            "open": "100",
            "high": "101",
            "low": "99",
            "close": "100.5",
            "volume": "12",
            "observed_at_unix_nanos": time,
            "source_id": "binance",
            "derivation": "provider",
        }
    }


def test_market_dataset_ingest_round_trips_parquet_and_materializes_replay(
    tmp_path: Path,
) -> None:
    source = tmp_path / "events.jsonl"
    source.write_text(
        "".join(json.dumps(_bar(time)) + "\n" for time in (1, 2)),
        encoding="utf-8",
    )
    app = MarketDataApplication(tmp_path / "state" / "market")

    entry = app.ingest("btc-1m", source, format="parquet")

    assert entry["format"] == "parquet"
    assert entry["event_count"] == 2
    assert Path(entry["manifest_path"]).is_file()
    assert app.read_events("btc-1m") == [_bar(1), _bar(2)]

    replay = materialize_replay_file(
        Path(entry["path"]), tmp_path / "instance" / "replay.jsonl"
    )
    assert replay.suffix == ".jsonl"
    assert [_bar(1), _bar(2)] == [
        json.loads(line) for line in replay.read_text(encoding="utf-8").splitlines()
    ]

    named_replay = materialize_replay_file(
        Path("dataset:btc-1m"),
        tmp_path / "instance" / "named-replay.jsonl",
        catalog_root=tmp_path / "state" / "market",
    )
    assert named_replay.read_text(encoding="utf-8") == replay.read_text(
        encoding="utf-8"
    )


def test_market_dataset_can_derive_explicit_synthetic_quotes_from_bars(
    tmp_path: Path,
) -> None:
    source = tmp_path / "events.jsonl"
    source.write_text(json.dumps(_bar(1)) + "\n", encoding="utf-8")
    app = MarketDataApplication(tmp_path / "state" / "market")
    app.ingest("btc-1h", source, format="jsonl")

    entry = app.derive_synthetic_quotes("btc-1h", "btc-quotes", spread_bps="10")

    assert entry["observation_types"] == ["Quote"]
    quote = app.read_events("btc-quotes")[0]["Quote"]
    assert quote["bid_price"] == "100.44975"
    assert quote["ask_price"] == "100.55025"
    assert quote["derivation"] == "synthetic_quote"


def test_replay_window_validation_proves_dataset_coverage(tmp_path: Path) -> None:
    source = tmp_path / "events.jsonl"
    source.write_text(
        "".join(json.dumps(_bar(time)) + "\n" for time in (10, 20)),
        encoding="utf-8",
    )

    result = validate_replay_window(
        source,
        start_time_unix_nanos=10,
        end_time_unix_nanos=20,
    )

    assert result == {
        "event_count": 2,
        "first_time_unix_nanos": 10,
        "last_time_unix_nanos": 20,
    }
