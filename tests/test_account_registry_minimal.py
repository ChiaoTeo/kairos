from __future__ import annotations

from kairospy.config import RunConfig
from kairospy.application.service.operations.run import AccountRegistry


def test_account_registry_resolves_single_account_for_venue() -> None:
    config = RunConfig.from_values({
        "run": {
            "mode": "paper",
            "strategy": "strategies.momentum:MomentumStrategy",
        },
        "accounts": {
            "ops_binance_main": {
                "index": 0,
                "venue": "binance",
                "cash": "10000",
                "currency": "USDC",
            },
        },
    })

    registry = AccountRegistry.from_config(config.accounts.values())

    account = registry.resolve(venue="binance")

    assert account.account_id == "ops_binance_main"
    assert account.index == 0
    assert account.credential is None


def test_account_registry_requires_explicit_account_when_venue_has_multiple_accounts() -> None:
    config = RunConfig.from_values({
        "run": {
            "mode": "paper",
            "strategy": "strategies.momentum:MomentumStrategy",
        },
        "accounts": {
            "ops_binance_main": {
                "index": 0,
                "venue": "binance",
                "cash": "10000",
                "currency": "USDC",
            },
            "ops_binance_hedge": {
                "index": 1,
                "venue": "binance",
                "cash": "10000",
                "currency": "USDC",
            },
        },
    })
    registry = AccountRegistry.from_config(config.accounts.values())

    try:
        registry.resolve(venue="binance")
    except ValueError as error:
        assert "multiple accounts configured for venue" in str(error)
        assert "specify account index: 0, 1" in str(error)
    else:
        raise AssertionError("expected ambiguous account selection")

    assert registry.resolve(venue="binance", account=1).account_id == "ops_binance_hedge"
    assert registry.resolve(venue="binance", account_id="ops_binance_main").index == 0
