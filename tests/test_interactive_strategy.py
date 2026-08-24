from __future__ import annotations

import asyncio

from kairospy.strategy.api.interactive import InteractiveStrategy
from kairospy.strategy import StrategyCommand


class Context:
    account = "account"
    execution = "execution"
    market = "market"


def test_interactive_strategy_executes_source_and_keeps_namespace() -> None:
    async def scenario() -> None:
        strategy = InteractiveStrategy()
        first = await strategy.on_command(
            Context(),
            StrategyCommand(
                "one", "interactive.python", "print('hello')\nvalue = 40 + 2\nvalue"
            ),
        )
        second = await strategy.on_command(
            Context(),
            StrategyCommand("two", "interactive.python", "value + 1"),
        )
        assert first.status == "completed"
        assert first.result["value"] == 42
        assert first.stdout == "hello\n"
        assert second.result["value"] == 43

    asyncio.run(scenario())


def test_interactive_strategy_supports_top_level_await() -> None:
    async def scenario() -> None:
        strategy = InteractiveStrategy()
        result = await strategy.on_command(
            Context(),
            StrategyCommand(
                "await-1",
                "interactive.python",
                "import asyncio\nawait asyncio.sleep(0)\n42",
            ),
        )
        assert result.status == "completed"
        assert result.result["value"] == 42

    asyncio.run(scenario())
