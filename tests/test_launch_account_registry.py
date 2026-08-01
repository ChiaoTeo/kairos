from __future__ import annotations

from decimal import Decimal

import pytest

from kairospy.application.support.launch.config.common import AccountConfigRegistry, ConfiguredAccount


def test_account_config_registry_resolves_by_index_or_id_with_venue_guard() -> None:
    registry = AccountConfigRegistry(
        (
            ConfiguredAccount("main", 0, "binance", Decimal("1000"), "USDT"),
            ConfiguredAccount("alt", 1, "binance", Decimal("500"), "USDT"),
            ConfiguredAccount("okx-main", 2, "okx", Decimal("200"), "USDT"),
        )
    )

    assert registry.resolve(venue="binance", account_index=1).account_id == "alt"
    assert registry.resolve(venue="okx", account_id="okx-main").index == 2

    with pytest.raises(ValueError, match="multiple accounts configured"):
        registry.resolve(venue="binance")
    with pytest.raises(ValueError, match="not 'okx'"):
        registry.resolve(venue="okx", account="main")
