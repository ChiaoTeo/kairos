from __future__ import annotations

from kairospy.config import RunConfig


def test_run_config_accepts_account_ref_for_live() -> None:
    values = {
        "run": {"mode": "live", "strategy": "strategy_mod:factory"},
        "account": {"ref": "binance_live"},
        "live": {"venue": "binance", "symbol": "BTC/USDT"},
    }

    assert RunConfig.from_values(values).validation_report().valid


def test_run_config_rejects_inline_accounts() -> None:
    values = {
        "run": {"mode": "paper", "strategy": "strategy_mod:factory"},
        "accounts": {"main": {"venue": "binance", "currency": "USDT"}},
        "paper": {"venue": "binance", "symbol": "BTC/USDT"},
    }

    report = RunConfig.from_values(values).validation_report()

    assert not report.valid
    assert "[accounts] is not valid run config; configure accounts in .kairos/accounts and reference them with account.ref" in report.issues


def test_run_config_rejects_broker_table_for_every_mode() -> None:
    values = {
        "run": {"mode": "backtest", "strategy": "strategy_mod:factory"},
        "broker": {"venue": "binance"},
    }

    report = RunConfig.from_values(values).validation_report()

    assert not report.valid
    assert "[broker] is not valid run config; configure broker/provider via .kairos/accounts" in report.issues
