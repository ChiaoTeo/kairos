from __future__ import annotations

from kairospy.config import LaunchConfig


def test_launch_config_accepts_account_ref_for_paper_launches() -> None:
    config = LaunchConfig.from_values(
        {
            "launch": {"id": "paper", "mode": "paper", "strategy": "examples.strategies.sma:strategy"},
            "account": {"ref": "binance_testnet_spot"},
        }
    )

    assert config.account_ref == "binance_testnet_spot"
    assert config.validation_report().valid is True


def test_launch_config_account_ref_is_enough_for_live_account_binding() -> None:
    config = LaunchConfig.from_values(
        {
            "launch": {"id": "live", "mode": "live", "strategy": "examples.strategies.sma:strategy"},
            "account": {"ref": "binance_live_spot"},
            "live": {},
        }
    )

    report = config.validation_report()

    assert report.valid is True
    assert not report.issues
