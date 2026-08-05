from decimal import Decimal

import pytest

from kairospy.domain.account import (
    AccountPolicy,
    AccountPolicySet,
    FeePolicy,
    MarginMode,
    MarginPolicy,
    PositionMode,
    PositionPolicy,
    SettlementPolicy,
)


def test_account_policy_set_keeps_account_and_position_rules_separate() -> None:
    policies = AccountPolicySet(
        account=AccountPolicy(can_trade=True, can_borrow=True),
        margin=MarginPolicy((MarginMode.CROSS, MarginMode.ISOLATED), initial_ratio=Decimal("0.1")),
        position=PositionPolicy((PositionMode.ONE_WAY, PositionMode.HEDGE)),
        fee=FeePolicy(taker=Decimal("0.0004"), payment_currency="USDT"),
        settlement=SettlementPolicy(("USDT", "USD")),
    )
    assert policies.account.can_borrow
    assert policies.margin.modes == (MarginMode.CROSS, MarginMode.ISOLATED)
    assert policies.position.modes == (PositionMode.ONE_WAY, PositionMode.HEDGE)


def test_policy_values_reject_invalid_margin_and_fee_rules() -> None:
    with pytest.raises(ValueError):
        MarginPolicy(initial_ratio=Decimal("1.1"))
    with pytest.raises(ValueError):
        FeePolicy(taker=Decimal("-0.1"))
