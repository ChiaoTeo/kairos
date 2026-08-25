from __future__ import annotations

from kairospy.surface.workbench.screens.effects import (
    AppendActivity,
    SetInteraction,
    SetStatus,
)
from kairospy.surface.workbench.screens.flows import market
from kairospy.surface.workbench.screens.session import GuidedSession
from kairospy.surface.workbench.screens.operation import OperationSpec
from kairospy.surface.workbench.screens.results import ResultKind, ResultRoute
from kairospy.surface.workbench.widgets import ChoiceInteraction, renderable_plain_text

from app_support import market as _market
from app_support import workbench_state as _state


def _spec(kind: ResultKind, qualifier: str | None = None) -> OperationSpec:
    return OperationSpec.create(
        action_name="market.test",
        audit_summary="查询市场",
        route=ResultRoute(kind, qualifier),
        operation=lambda: None,
        running_status="正在查询市场",
    )


def test_market_search_flow_returns_choices_without_activity() -> None:
    session = GuidedSession(context=("market",))

    effects = market.handle_success(
        _state(),
        session,
        _spec(ResultKind.MARKET),
        (_market(),),
    )

    assert effects is not None
    assert not any(isinstance(effect, AppendActivity) for effect in effects)
    interaction = next(
        effect.interaction for effect in effects if isinstance(effect, SetInteraction)
    )
    assert isinstance(interaction, ChoiceInteraction)
    assert len(interaction.actions) == 1
    assert session.context == ("market", "results")


def test_explicit_market_observation_returns_one_terminal_activity() -> None:
    state = _state()
    session = GuidedSession(context=("market", "selected"))
    session.market.selected = _market()
    session.market.observation = "quote"
    result = {
        "symbol": "AAPL",
        "data_type": "quote",
        "provider": "massive",
        "bid_price": "226.50",
        "ask_price": "226.75",
    }

    effects = market.handle_success(
        state,
        session,
        _spec(ResultKind.MARKET_OBSERVATION),
        result,
    )

    assert effects is not None
    activities = tuple(
        effect.activity for effect in effects if isinstance(effect, AppendActivity)
    )
    assert len(activities) == 1
    assert "226.50" in (activities[0].copy_text or "")
    assert any(isinstance(effect, SetStatus) for effect in effects)
    assert "226.50" in renderable_plain_text(session.market.snapshot)
