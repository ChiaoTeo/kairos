from __future__ import annotations

import json
from pathlib import Path

from kairospy.application.market import MarketDataApplication, materialize_replay_file


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
