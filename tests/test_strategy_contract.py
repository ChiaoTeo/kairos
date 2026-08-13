from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from kairospy.application.account import (
    AccountApplication,
    AccountNotEnabledError,
    AccountSegmentSnapshot,
    AccountSnapshot,
    DataFreshness,
    SPOT,
)
from kairospy.application.execution import ExecutionApplication
from kairospy.application.market import Bar as ApplicationBar
from kairospy.application.risk import RiskApplication, RiskStatus
from kairospy.strategy import (
    AccountId,
    Bar,
    BarEvent,
    EventMetadata,
    InstrumentId,
    InstrumentRef,
    MarketId,
    Strategy,
    StrategyContractError,
    StrategyState,
    StrategyStateTypeError,
    validate_strategy,
)


def test_strategy_reexports_module_owned_types_without_copying() -> None:
    assert Bar is ApplicationBar


def test_market_application_has_no_internal_port_or_contract_facade() -> None:
    root = Path(__file__).parents[1]
    assert not (root / "kairospy/application/market/ports.py").exists()
    assert not (root / "kairospy/infrastructure/contracts/market.py").exists()
    assert not (
        root / "kairospy/application/strategy/services/applications.py"
    ).exists()


def test_execution_application_has_no_internal_port_or_bound_adapter() -> None:
    root = Path(__file__).parents[1]
    assert not (root / "kairospy/application/execution/ports.py").exists()
    assert not (
        root / "kairospy/application/strategy/services/applications.py"
    ).exists()


def test_disabled_execution_returns_a_typed_rejected_receipt() -> None:
    execution = ExecutionApplication(
        None,
        None,
        strategy_id="disabled",
        instance_id="instance-1",
    )

    receipt = execution.target_position(
        InstrumentId("instrument:test:SPY"),
        Decimal("1"),
        account="main",
    )

    assert receipt.status.value == "rejected"
    assert receipt.delivery_certainty.value == "not_sent"
    assert receipt.error == "execution is disabled for this launch"


def test_account_application_owns_concrete_multi_account_projection_selection() -> None:
    main_id = AccountId("main")
    secondary_id = AccountId("secondary")

    class Projection:
        def __init__(self, equity: Decimal) -> None:
            self.equity = equity

        def snapshot(self, account_id: AccountId) -> AccountSnapshot:
            return AccountSnapshot(
                account_id,
                (
                    AccountSegmentSnapshot(
                        account_id,
                        SPOT,
                        "paper",
                        "paper",
                        "no_margin",
                        self.equity,
                        (),
                        (),
                        DataFreshness.FRESH,
                        1,
                    ),
                ),
                1,
            )

    account = AccountApplication(
        {
            main_id: Projection(Decimal("100")),
            secondary_id: Projection(Decimal("200")),
        }
    )

    assert account.account_ids == (main_id, secondary_id)
    assert account.accounts[0].segment(SPOT).equity == Decimal("100")
    assert account.account("secondary").segment(SPOT).equity == Decimal("200")
    assert [value.generation for value in account.snapshot().accounts] == [1, 1]
    with pytest.raises(AccountNotEnabledError, match="not enabled"):
        account.account("outside")


def test_account_application_has_no_callable_or_object_adapter() -> None:
    root = Path(__file__).parents[1]
    application = (root / "kairospy/application/account/application.py").read_text(
        encoding="utf-8"
    )
    assert "Callable" not in application
    assert not (
        root / "kairospy/application/strategy/services/applications.py"
    ).exists()


def test_risk_application_owns_concrete_projection_query() -> None:
    class Projection:
        def status(self, account_id: AccountId) -> RiskStatus:
            return RiskStatus(
                account_id,
                True,
                Decimal("1000"),
                Decimal("0"),
                Decimal("0"),
                (),
                1,
            )

    risk = RiskApplication(Projection())

    status = risk.status(account="main")
    assert status.account_id == AccountId("main")
    assert status.trading_allowed is True
    assert status.available_notional == Decimal("1000")


def test_unavailable_risk_fails_at_the_application_boundary() -> None:
    with pytest.raises(RuntimeError, match="Risk projection is unavailable"):
        RiskApplication(None).status(account="main")


def test_risk_application_has_no_callable_or_object_adapter() -> None:
    root = Path(__file__).parents[1]
    application = (root / "kairospy/application/risk/application.py").read_text(
        encoding="utf-8"
    )
    assert "Callable" not in application
    assert not (
        root / "kairospy/application/strategy/services/applications.py"
    ).exists()


def test_strategy_validation_requires_the_typed_lifecycle() -> None:
    class Incomplete:
        strategy_id = "incomplete"

    with pytest.raises(StrategyContractError, match="missing lifecycle callbacks"):
        validate_strategy(Incomplete())


def test_strategy_implements_the_public_lifecycle() -> None:
    strategy = Strategy()
    validate_strategy(strategy)
    assert strategy.strategy_id == "strategy"


def test_bar_event_has_typed_data_and_delivery_metadata() -> None:
    occurred_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    bar = Bar(
        MarketId("market:test:SPY"),
        InstrumentRef(InstrumentId("instrument:test:SPY"), "SPY"),
        "1h",
        Decimal("1"),
        Decimal("2"),
        Decimal("0.5"),
        Decimal("1.5"),
        None,
        occurred_at,
        1_704_067_200_000_000_000,
    )
    event = BarEvent(bar, EventMetadata("market.events", 1))
    assert event.data.close == Decimal("1.5")
    assert event.metadata.sequence == 1


def test_strategy_state_never_coerces_an_incompatible_json_value() -> None:
    state = StrategyState(strategy_id="s", instance_id="i", initial={"count": "1"})
    with pytest.raises(StrategyStateTypeError, match="expected=int"):
        state.get_int("count")
    assert state.increment("missing") == 1
