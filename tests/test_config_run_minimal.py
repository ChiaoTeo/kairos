from __future__ import annotations

from decimal import Decimal

from kairospy.config import RunConfig


def test_backtest_run_config_does_not_require_account() -> None:
    config = RunConfig.from_values({
        "run": {
            "id": "momentum-backtest",
            "mode": "backtest",
            "strategy": "strategies.momentum:MomentumStrategy",
        }
    })

    report = config.validation_report()

    assert report.valid is True
    assert report.issues == ()
    assert config.account_defaults.cash == Decimal("100000")
    assert config.account_defaults.currency == "USD"


def test_paper_run_config_does_not_require_account() -> None:
    config = RunConfig.from_values({
        "run": {
            "mode": "paper",
            "strategy": "strategies.momentum:MomentumStrategy",
        },
        "accounts": {
            "binance_main": {
                "index": 1,
                "venue": "binance",
                "cash": "10000",
                "currency": "USDC",
            },
            "binance_hedge": {
                "index": 0,
                "venue": "binance",
                "cash": "10000",
                "currency": "USDC",
            },
        },
    })

    assert config.validation_report().valid is True
    assert config.mode == "paper"
    assert config.run_id == "kairos-run"
    assert tuple(config.accounts) == ("binance_hedge", "binance_main")
    assert config.accounts["binance_hedge"].index == 0
    assert config.accounts["binance_hedge"].credential is None


def test_legacy_sandbox_mode_is_rejected() -> None:
    config = RunConfig.from_values({
        "run": {
            "id": "momentum-sandbox",
            "mode": "sandbox",
            "strategy": "strategies.momentum:MomentumStrategy",
        },
        "account": {
            "environment": "sandbox",
        },
    })

    assert config.validation_report().issues == (
        "run.mode must be one of: backtest, paper, live",
    )


def test_run_config_rejects_data_requirements() -> None:
    config = RunConfig.from_values({
        "run": {
            "id": "momentum-backtest",
            "mode": "backtest",
            "strategy": "strategies.momentum:MomentumStrategy",
        },
        "data": {
            "dataset": "market.ohlcv.binance.btc_usdt.1m",
        },
    })

    assert config.validation_report().issues == (
        "[data] is not valid run config; declare data requirements in strategy code",
    )


def test_live_run_config_requires_explicit_execution_boundary() -> None:
    config = RunConfig.from_values({
        "run": {
            "id": "momentum-live",
            "mode": "live",
            "strategy": "strategies.momentum:MomentumStrategy",
        }
    })

    assert config.validation_report().issues == ("[accounts] table is required for paper/live runs",)


def test_live_run_config_accepts_explicit_execution_boundary() -> None:
    config = RunConfig.from_values({
        "run": {
            "id": "momentum-live",
            "mode": "live",
            "strategy": "strategies.momentum:MomentumStrategy",
        },
        "accounts": {
            "binance_main": {
                "index": 0,
                "venue": "binance",
                "credential": "binance-main",
            },
        },
    })

    assert config.validation_report().valid is True


def test_live_run_config_requires_account_credentials() -> None:
    config = RunConfig.from_values({
        "run": {
            "mode": "live",
            "strategy": "strategies.momentum:MomentumStrategy",
        },
        "accounts": {
            "binance_main": {
                "venue": "binance",
            },
        },
    })

    assert config.validation_report().issues == ("accounts.binance_main.credential is required for live runs",)


def test_run_config_rejects_duplicate_account_indexes() -> None:
    config = RunConfig.from_values({
        "run": {
            "mode": "paper",
            "strategy": "strategies.momentum:MomentumStrategy",
        },
        "accounts": {
            "binance_main": {
                "index": 0,
                "venue": "binance",
            },
            "binance_hedge": {
                "index": 0,
                "venue": "binance",
            },
        },
    })

    assert config.validation_report().issues == (
        "accounts.binance_hedge.index duplicates accounts.binance_main.index",
    )


def test_run_config_requires_mode_and_id() -> None:
    config = RunConfig.from_values({"run": {"strategy": "strategies.momentum:MomentumStrategy"}})

    assert config.validation_report().issues == (
        "run.mode must be a non-empty string",
        "run.mode must be one of: backtest, paper, live",
    )


def test_run_config_rejects_cli_mode_mismatch() -> None:
    config = RunConfig.from_values({
        "run": {
            "id": "momentum-backtest",
            "mode": "backtest",
            "strategy": "strategies.momentum:MomentumStrategy",
        }
    })

    try:
        config.require_mode("live")
    except ValueError as error:
        assert "command requires 'live'" in str(error)
    else:
        raise AssertionError("expected mode mismatch")
