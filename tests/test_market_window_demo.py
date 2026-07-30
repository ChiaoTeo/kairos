from __future__ import annotations

import importlib.util
from pathlib import Path


def _demo_main():
    path = Path(__file__).resolve().parents[1] / "examples" / "market_window_demo.py"
    spec = importlib.util.spec_from_file_location("market_window_demo", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load market window demo")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main


def test_market_window_demo_runs_synthetic_realtime_source(capsys) -> None:
    _demo_main()(["--source", "synthetic", "--ticks", "1", "--interval", "0"])

    output = capsys.readouterr().out

    assert "| #3 quote | quotes" in output
    assert "market.window.binance_spot_btc_usdt.quotes" in output
    assert "market.window.binance_spot_btc_usdt.trades" in output
    assert "market.window.binance_spot_btc_usdt.orderbooks" in output
    assert "market.window.binance_spot_btc_usdt.bars.1m" in output
    assert "market.window.binance_spot_btc_usdt.rates.funding_rate" in output
