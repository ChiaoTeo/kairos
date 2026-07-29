from __future__ import annotations

from kairospy.config import RunConfig


def test_run_config_accepts_mode_account_selectors() -> None:
    values = {
        "run": {"mode": "live", "strategy": "strategy_mod:factory"},
        "accounts": {
            "main": {"venue": "binance", "currency": "USDT", "credential": "env:main"},
            "alt": {"index": 1, "venue": "binance", "currency": "USDT", "credential": "env:alt"},
        },
        "live": {"venue": "binance", "symbol": "BTC/USDT", "account_index": 1},
    }

    assert RunConfig.from_values(values).validation_report().valid


def test_run_config_rejects_invalid_mode_account_selector() -> None:
    values = {
        "run": {"mode": "paper", "strategy": "strategy_mod:factory"},
        "accounts": {"main": {"venue": "binance", "currency": "USDT"}},
        "paper": {"venue": "binance", "symbol": "BTC/USDT", "account_index": -1},
    }

    report = RunConfig.from_values(values).validation_report()

    assert not report.valid
    assert "paper.account_index cannot be negative" in report.issues
