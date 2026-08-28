from __future__ import annotations

from rich.console import Console

from kairospy.surface.workbench.screens.flows.market.workspace import (
    WORKSPACE_MARKET_ACTIONS,
    WorkspaceMarketPromptState,
    subscriptions_renderable,
)


def _text(value: object) -> str:
    console = Console(width=160, record=True)
    console.print(value)
    return console.export_text()


def test_market_workspace_exposes_both_subscription_scopes_and_mutations() -> None:
    actions = {action.id: action for action in WORKSPACE_MARKET_ACTIONS}
    assert actions["session-subscriptions"].shortcut == "3"
    assert actions["subscriptions"].shortcut == "4"
    assert actions["subscribe"].shortcut == "s"
    assert actions["unsubscribe"].shortcut == "u"


def test_operator_subscription_prompt_keeps_platform_owner_outside_strategy_state() -> None:
    prompt = WorkspaceMarketPromptState(
        "subscribe", owner_id="operator:kairos-i:session-1"
    )
    for name, value in (
        ("market-id", "market:binance:spot:BTCUSDT"),
        ("observations", "quote,trade"),
        ("provider", ""),
    ):
        prompt.accept(name, value)
    assert prompt.owner_id == "operator:kairos-i:session-1"
    assert "owner_id" not in prompt.summary()


def test_subscription_inventory_rendering_shows_owner_and_pending_state() -> None:
    panel = subscriptions_renderable(
        {
            "subscriptions": [
                {
                    "subscription_id": "sub-1",
                    "owner_id": "operator:kairos-i:session-1",
                    "market_ids": ["market:binance:spot:BTCUSDT"],
                    "observations": ["quote"],
                    "selected_providers": ["binance"],
                    "state": "active",
                    "pending_reason": None,
                }
            ]
        },
        current_session=True,
    )
    rendered = _text(panel)
    assert "当前 Kairos I 订阅" in rendered
    assert "operator:kairos-i:session-1" in rendered
    assert "market:binance:spot:BTCUSDT" in rendered
