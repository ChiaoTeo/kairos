from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
import json
from pathlib import Path

from typer.testing import CliRunner

from kairospy.application.service.modes.paper import configured_paper
from kairospy.surface.products.run import run_app


def test_configured_paper_runs_new_engine(tmp_path) -> None:
    config_path = _write_paper_project(tmp_path)

    result = configured_paper(config_path).run()

    assert result.mode.value == "paper"
    assert result.runtime.event_count == 2
    assert len(result.fills) == 1
    assert result.account_view.cash == result.final_equity


def test_run_paper_command_uses_new_config_runner(tmp_path) -> None:
    config_path = _write_paper_project(tmp_path)

    result = CliRunner().invoke(run_app, ["paper", "--config", str(config_path), "--format", "json"], catch_exceptions=False)

    assert result.exit_code == 0
    assert '"mode": "paper"' in result.output
    assert '"event_count": 2' in result.output


def test_configured_paper_can_stream_market_data_from_integration_feed(tmp_path) -> None:
    config_path = _write_streaming_paper_project(tmp_path)

    result = configured_paper(config_path, market_feed_factory=lambda venue: FakePaperFeed()).run()

    assert result.mode.value == "paper"
    assert result.runtime.event_count == 2
    assert len(result.fills) == 1
    assert result.account_view.cash == result.final_equity


def test_configured_paper_selects_account_id_for_streaming_feed(tmp_path) -> None:
    config_path = _write_streaming_paper_project(tmp_path, extra_accounts=True, account_id="alt")

    configured = configured_paper(config_path, market_feed_factory=lambda venue: FakePaperFeed())
    result = configured.run()

    assert result.account.account.account_id == "alt"
    assert configured.normalized_config["account"]["cash"] == 500


class FakePaperFeed:
    async def watch_ticker(self, symbol: str, *, params: Mapping[str, object] | None = None) -> AsyncIterator[Mapping[str, object]]:
        yield {"timestamp": 1767225600000, "bid": "100", "ask": "101"}

    async def watch_order_book(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        if False:
            yield {}

    async def watch_trades(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        if False:
            yield {}


def _write_paper_project(root: Path) -> Path:
    market_id = "market:binance:spot:btc_usdt"
    instrument_id = "instrument:spot:btc:usdt"
    (root / "strategy_mod.py").write_text(
        "\n".join([
            "from decimal import Decimal",
            "from kairospy.application.strategy import StrategyBase",
            "from kairospy.core.intent import target_position_intent",
            "class PaperStrategy(StrategyBase):",
            "    strategy_id = 'paper-strategy'",
            "    def __init__(self, instrument_id, market_id):",
            "        self.instrument_id = instrument_id",
            "        self.market_id = market_id",
            "        self.entered = False",
            "    def on_data(self, context, signal):",
            "        if self.entered:",
            "            return None",
            "        self.entered = True",
            "        context.intent(target_position_intent(",
            "            strategy_id=self.strategy_id,",
            "            instrument_id=self.instrument_id,",
            "            market_id=self.market_id,",
            "            target_quantity=Decimal('1'),",
            "            at=signal.time,",
            "            intent_id='paper-intent',",
            "        ))",
        ]),
        encoding="utf-8",
    )
    (root / "events.jsonl").write_text(
        json.dumps(
            {
                "time": "2026-01-01T00:00:00+00:00",
                "kind": "quote",
                "market_id": market_id,
                "instrument_id": instrument_id,
                "market_key": "binance_spot_btc_usdt",
                "bid": "100",
                "ask": "101",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    config_path = root / "paper.toml"
    config_path.write_text(
        "\n".join([
            "[run]",
            'id = "paper-1"',
            'mode = "paper"',
            'strategy = "strategy_mod:PaperStrategy"',
            "",
            "[strategy.params]",
            f'instrument_id = "{instrument_id}"',
            f'market_id = "{market_id}"',
            "",
            "[accounts.main]",
            'venue = "paper"',
            "cash = 1000",
            'currency = "USDT"',
            "",
            "[paper]",
            'events = "events.jsonl"',
            'price_field = "ask"',
        ])
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _write_streaming_paper_project(root: Path, *, extra_accounts: bool = False, account_id: str | None = None) -> Path:
    market_id = "market:binance:spot:btc_usdt"
    instrument_id = "instrument:spot:btc:usdt"
    (root / "strategy_mod.py").write_text(
        "\n".join([
            "from decimal import Decimal",
            "from kairospy.application.strategy import StrategyBase",
            "from kairospy.core.intent import target_position_intent",
            "class PaperStrategy(StrategyBase):",
            "    strategy_id = 'paper-strategy'",
            "    def __init__(self, instrument_id, market_id):",
            "        self.instrument_id = instrument_id",
            "        self.market_id = market_id",
            "        self.entered = False",
            "    def on_data(self, context, signal):",
            "        if self.entered:",
            "            return None",
            "        self.entered = True",
            "        context.intent(target_position_intent(",
            "            strategy_id=self.strategy_id,",
            "            instrument_id=self.instrument_id,",
            "            market_id=self.market_id,",
            "            target_quantity=Decimal('1'),",
            "            at=signal.time,",
            "            intent_id='paper-intent',",
            "        ))",
        ]),
        encoding="utf-8",
    )
    config_path = root / "paper-streaming.toml"
    lines = [
            "[run]",
            'id = "paper-streaming-1"',
            'mode = "paper"',
            'strategy = "strategy_mod:PaperStrategy"',
            "",
            "[strategy.params]",
            f'instrument_id = "{instrument_id}"',
            f'market_id = "{market_id}"',
            "",
            "[accounts.main]",
            'venue = "binance"',
            "cash = 1000",
            'currency = "USDT"',
    ]
    if extra_accounts:
        lines.extend([
            "",
            "[accounts.alt]",
            "index = 1",
            'venue = "binance"',
            "cash = 500",
            'currency = "USDT"',
        ])
    lines.extend([
            "",
            "[paper]",
            'venue = "binance"',
            'market = "spot"',
            'symbol = "BTC/USDT"',
            'price_field = "ask"',
    ])
    if account_id is not None:
        lines.append(f'account_id = "{account_id}"')
    config_path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    return config_path
