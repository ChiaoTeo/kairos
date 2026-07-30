from __future__ import annotations

from kairospy.config import LaunchConfig


def test_launch_config_accepts_account_ref_for_live() -> None:
    values = {
        "launch": {"mode": "live", "strategy": "strategy_mod:factory"},
        "account": {"ref": "binance_live"},
        "live": {"venue": "binance", "symbol": "BTC/USDT"},
    }

    assert LaunchConfig.from_values(values).validation_report().valid


def test_launch_config_rejects_inline_accounts() -> None:
    values = {
        "launch": {"mode": "paper", "strategy": "strategy_mod:factory"},
        "accounts": {"main": {"venue": "binance", "currency": "USDT"}},
        "paper": {"venue": "binance", "symbol": "BTC/USDT"},
    }

    report = LaunchConfig.from_values(values).validation_report()

    assert not report.valid
    assert "[accounts] inline account definitions are not valid launch config; configure accounts in .kairos/accounts and reference them with accounts.<alias>.ref" in report.issues


def test_launch_config_accepts_launch_account_references() -> None:
    values = {
        "launch": {"mode": "paper", "strategy": "strategy_mod:factory"},
        "accounts": {
            "account1": {"ref": "binance_main", "books": ["spot", "funding"], "trade": False},
            "account2": {"ref": "okx_main", "index": 1, "books": ["swap"]},
        },
        "paper": {"venue": "binance", "symbol": "BTC/USDT"},
    }

    config = LaunchConfig.from_values(values)
    report = config.validation_report()

    assert report.valid
    assert config.launch_accounts["account1"].ref == "binance_main"
    assert config.launch_accounts["account1"].books == ("spot", "funding")
    assert config.launch_accounts["account1"].trade is False
    assert config.launch_accounts["account2"].trade is True


def test_launch_config_rejects_broker_table_for_every_mode() -> None:
    values = {
        "launch": {"mode": "backtest", "strategy": "strategy_mod:factory"},
        "broker": {"venue": "binance"},
    }

    report = LaunchConfig.from_values(values).validation_report()

    assert not report.valid
    assert "[broker] is not valid launch config; configure broker/provider via .kairos/accounts" in report.issues


def test_launch_config_rejects_system_reserved_launch_id() -> None:
    values = {
        "launch": {"id": "kairos-system", "mode": "backtest", "strategy": "strategy_mod:factory"},
        "backtest": {"events": "events.jsonl"},
    }

    report = LaunchConfig.from_values(values).validation_report()

    assert not report.valid
    assert "launch.id 'kairos-system' is reserved for the built-in system runtime" in report.issues
