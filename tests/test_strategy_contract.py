from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from kairospy.investment.apps.account.application import (
    AccountApplication,
    AccountNotEnabledError,
    AccountSegmentSnapshot,
    AccountSnapshot,
    DataFreshness,
    SPOT,
)
from kairospy.investment.apps.execution.application import ExecutionApplication
from kairospy.investment.apps.market.application import Bar as ApplicationBar
from kairospy.investment.apps.risk.application import RiskApplication, RiskStatus
from kairospy.strategy import (
    AccountId,
    AggressorSide,
    Bar,
    BarEvent,
    EventMetadata,
    GreeksEvent,
    InstrumentId,
    InstrumentRef,
    ImmediateAlgorithm,
    MarketId,
    ObservationScope,
    OptionGreeks,
    Quote,
    QuoteEvent,
    Strategy,
    StrategyContractError,
    StrategyState,
    StrategyStateTypeError,
    Trade,
    TradeEvent,
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
        algorithm=ImmediateAlgorithm(),
    )

    assert receipt.status.value == "rejected"
    assert receipt.delivery_certainty.value == "not_sent"
    assert receipt.error == "execution is disabled for this launch"


def test_account_application_owns_concrete_multi_account_current_view_selection() -> None:
    main_id = AccountId("main")
    secondary_id = AccountId("secondary")

    class LatestView:
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
            main_id: LatestView(Decimal("100")),
            secondary_id: LatestView(Decimal("200")),
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
    application = (
        root / "kairospy/investment/apps/account/application/application.py"
    ).read_text(
        encoding="utf-8"
    )
    assert "Callable" not in application
    assert not (
        root / "kairospy/application/strategy/services/applications.py"
    ).exists()


def test_risk_application_owns_concrete_current_view_query() -> None:
    class LatestView:
        def status(self, account_id: AccountId) -> dict[str, object]:
            return {
                "account_id": str(account_id),
                "trading_allowed": True,
                "available_notional": "1000",
                "reserved_notional": "0",
                "utilization": "0",
                "violations": [],
                "generation": 1,
            }

    risk = RiskApplication(LatestView())

    status = risk.status(account="main")
    assert status.account_id == AccountId("main")
    assert status.trading_allowed is True
    assert status.available_notional == Decimal("1000")


def test_unavailable_risk_fails_at_the_application_boundary() -> None:
    with pytest.raises(RuntimeError, match="Risk latest view is unavailable"):
        RiskApplication(None).status(account="main")


def test_risk_application_has_no_callable_or_object_adapter() -> None:
    root = Path(__file__).parents[1]
    application = (
        root / "kairospy/investment/apps/risk/application/application.py"
    ).read_text(
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


def test_strategy_default_market_dispatch_selects_one_typed_hook() -> None:
    occurred_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    instrument = InstrumentRef(InstrumentId("instrument:test:SPY"), "SPY")
    metadata = EventMetadata("market.events", 1)
    quote = QuoteEvent(
        Quote(
            ObservationScope.market("market:test:SPY"),
            instrument,
            Decimal("1"),
            None,
            Decimal("2"),
            None,
            occurred_at,
            1_704_067_200_000_000_000,
        ),
        metadata,
    )
    bar = BarEvent(
        Bar(
            ObservationScope.market("market:test:SPY"),
            instrument,
            "1h",
            Decimal("1"),
            Decimal("2"),
            Decimal("0.5"),
            Decimal("1.5"),
            None,
            occurred_at,
            1_704_067_200_000_000_000,
        ),
        metadata,
    )
    trade = TradeEvent(
        Trade(
            ObservationScope.market("market:test:SPY"),
            instrument,
            Decimal("1.5"),
            Decimal("10"),
            AggressorSide.BUY,
            occurred_at,
            1_704_067_200_000_000_000,
        ),
        metadata,
    )
    greeks = GreeksEvent(
        OptionGreeks(
            ObservationScope.market("market:test:SPY"),
            instrument,
            1_710_000_000_000_000_000,
            Decimal("450"),
            Decimal("0.5"),
            Decimal("0.1"),
            Decimal("0.2"),
            Decimal("-0.1"),
            Decimal("0.25"),
            occurred_at,
            1_704_067_200_000_000_000,
        ),
        metadata,
    )

    class TypedHooks(Strategy):
        def __init__(self) -> None:
            self.received: list[str] = []

        def on_quote(self, ctx, event: QuoteEvent) -> None:
            self.received.append(event.kind)

        def on_bar(self, ctx, event: BarEvent) -> None:
            self.received.append(event.kind)

        def on_trade(self, ctx, event: TradeEvent) -> None:
            self.received.append(event.kind)

        def on_greeks(self, ctx, event: GreeksEvent) -> None:
            self.received.append(event.kind)

    strategy = TypedHooks()
    strategy.on_market(None, quote)  # type: ignore[arg-type]
    strategy.on_market(None, bar)  # type: ignore[arg-type]
    strategy.on_market(None, trade)  # type: ignore[arg-type]
    strategy.on_market(None, greeks)  # type: ignore[arg-type]

    assert strategy.received == ["quote", "bar", "trade", "greeks"]


def test_overriding_on_market_takes_control_of_typed_dispatch() -> None:
    occurred_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    quote = QuoteEvent(
        Quote(
            ObservationScope.market("market:test:SPY"),
            InstrumentRef(InstrumentId("instrument:test:SPY"), "SPY"),
            Decimal("1"),
            None,
            Decimal("2"),
            None,
            occurred_at,
            1_704_067_200_000_000_000,
        ),
        EventMetadata("market.events", 1),
    )

    class FullControl(Strategy):
        def __init__(self) -> None:
            self.received: list[str] = []

        def on_market(self, ctx, event) -> None:
            self.received.append("market")

        def on_quote(self, ctx, event) -> None:
            self.received.append("quote")

    strategy = FullControl()
    strategy.on_market(None, quote)

    assert strategy.received == ["market"]


def test_overriding_on_market_can_delegate_to_typed_dispatch() -> None:
    occurred_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    quote = QuoteEvent(
        Quote(
            ObservationScope.market("market:test:SPY"),
            InstrumentRef(InstrumentId("instrument:test:SPY"), "SPY"),
            Decimal("1"),
            None,
            Decimal("2"),
            None,
            occurred_at,
            1_704_067_200_000_000_000,
        ),
        EventMetadata("market.events", 1),
    )

    class DelegatingControl(Strategy):
        def __init__(self) -> None:
            self.received: list[str] = []

        def on_market(self, ctx, event) -> None:
            self.received.append("market")
            super().on_market(ctx, event)

        def on_quote(self, ctx, event) -> None:
            self.received.append("quote")

    strategy = DelegatingControl()
    strategy.on_market(None, quote)

    assert strategy.received == ["market", "quote"]


def test_repository_strategies_do_not_use_the_removed_data_lifecycle() -> None:
    strategy_root = Path(__file__).parents[1] / "strategies"
    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in strategy_root.glob("*.py")
    )

    assert "StrategyBase" not in sources
    assert "def on_data(" not in sources


def test_bar_event_has_typed_data_and_delivery_metadata() -> None:
    occurred_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    bar = Bar(
        ObservationScope.market("market:test:SPY"),
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
