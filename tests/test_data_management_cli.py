from __future__ import annotations

import json

from typer.testing import CliRunner

from kairospy.infrastructure.data import DataStore
from kairospy.surface.products.data import data_app


def test_data_cli_lists_inspects_aliases_and_prunes(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    data_root = tmp_path / ".kairos" / "data"
    store = DataStore(data_root, storage_format="jsonl")
    store.write(
        "market.ohlcv.binance_spot_btc_usdt.1m",
        (
            {"time": "2026-01-01T00:00:00+00:00", "close": "100"},
            {"time": "2026-01-01T00:01:00+00:00", "close": "101"},
        ),
        mode="replace",
    )

    listed = CliRunner().invoke(data_app, ["list", "--format", "jsonl", "--output", "json"], catch_exceptions=False)
    inspected = CliRunner().invoke(
        data_app,
        ["inspect", "market.ohlcv.binance_spot_btc_usdt.1m", "--format", "jsonl", "--output", "json"],
        catch_exceptions=False,
    )
    aliased = CliRunner().invoke(
        data_app,
        ["alias", "market.ohlcv.binance_spot_btc_usdt.1m", "btc-bars", "--format", "jsonl"],
        catch_exceptions=False,
    )
    pruned = CliRunner().invoke(
        data_app,
        [
            "prune",
            "btc-bars",
            "--start",
            "2026-01-01T00:00:00+00:00",
            "--end",
            "2026-01-01T00:01:00+00:00",
            "--format",
            "jsonl",
            "--output",
            "json",
        ],
        catch_exceptions=False,
    )

    assert listed.exit_code == 0
    assert "market.ohlcv.binance_spot_btc_usdt.1m" in json.loads(listed.output)["datasets"]
    assert json.loads(inspected.output)["rows"] == 2
    assert json.loads(aliased.output)["alias"] == "btc-bars"
    assert json.loads(pruned.output)["deleted_rows"] == 1
    operations = (tmp_path / ".kairos" / "state" / "operations.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(operations[-2])["action"] == "data.alias"
    assert json.loads(operations[-1])["action"] == "data.prune"


def _write_workspace_manifest(root) -> None:
    kairos = root / ".kairos"
    kairos.mkdir(parents=True, exist_ok=True)
    (kairos / "kairos.toml").write_text("[project]\nname = \"test\"\n[data]\nstorage_format = \"jsonl\"\n", encoding="utf-8")
