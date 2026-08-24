from __future__ import annotations

from types import SimpleNamespace

from kairospy.surface.cli.interactive.models import GuidedCommand, ShellControl
from kairospy.surface.cli.interactive.sections.business import reference


def test_reference_numeric_and_text_navigation(
    interactive_context, capsys
) -> None:
    interactive_context.shell_path = ("reference",)
    reference.print_menu(interactive_context)
    reference.print_help(interactive_context)
    assert reference.handle(interactive_context, ("1",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("reference", "assets")
    assert "Reference 市场目录" in capsys.readouterr().out

    interactive_context.shell_path = ("reference",)
    assert reference.handle(interactive_context, ("assets",)) is ShellControl.HANDLED
    assert interactive_context.shell_path == ("reference", "assets")


def test_reference_option_chain_builder(monkeypatch) -> None:
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "instrument:equity:US:AAPL:common")
    command = reference.choose_option_chain()
    assert isinstance(command, GuidedCommand)
    assert command.argv[:2] == ("reference", "option-chain")


def test_market_selection_accepts_search_and_numeric_choice_but_no_raw_id(
    interactive_context, monkeypatch
) -> None:
    record = SimpleNamespace(
        id="market:binance:spot:BTCUSDT",
        venue_symbol="BTCUSDT",
        exchange_id="exchange:binance",
        instrument_kind="spot",
        base_asset="asset:BTC",
        quote_asset="asset:USDT",
        status="active",
        instrument=SimpleNamespace(display_symbol="BTC/USDT"),
    )

    class Application:
        def find_markets(self, **filters):
            assert filters == {"query": "BTC", "active_only": True, "limit": 25}
            return (record,)

    monkeypatch.setattr(reference, "_application", lambda _context: Application())
    prompts: list[str] = []
    answers = iter(["BTC", "1"])

    def prompt(label, **_kwargs):
        prompts.append(label)
        return next(answers)

    monkeypatch.setattr("typer.prompt", prompt)

    assert reference.select_market(interactive_context) is record
    assert interactive_context.selected_market is record
    assert prompts == [
        "输入代码或名称（直接回车浏览可用标的）",
        "选择标的序号；输入 b 返回",
    ]
    assert all("market id" not in label.lower() for label in prompts)


def test_market_search_ranks_exact_aapl_ahead_of_prefixed_crypto_symbol() -> None:
    equity = SimpleNamespace(
        id="market:nasdaq:equity:AAPL:USD",
        venue_symbol="AAPL",
        instrument=SimpleNamespace(display_symbol="AAPL"),
    )
    crypto = SimpleNamespace(
        id="market:binance:spot:AAPLBUSDT",
        venue_symbol="AAPLBUSDT",
        instrument=SimpleNamespace(display_symbol="AAPLB/USDT"),
    )

    ranked = reference._rank_reference_records("market", (crypto, equity), "AAPL")

    assert ranked == (equity, crypto)


def test_market_selection_filters_to_currently_supported_instrument_kinds(
    interactive_context, monkeypatch, capsys
) -> None:
    spot = SimpleNamespace(
        id="market:binance:spot:BTCUSDT",
        venue_symbol="BTCUSDT",
        exchange_id="exchange:binance",
        instrument_kind="spot",
        base_asset="asset:BTC",
        quote_asset="asset:USDT",
        status="active",
        instrument=SimpleNamespace(display_symbol="BTC/USDT"),
    )
    seen_kinds = []

    class Application:
        def find_markets(self, **filters):
            seen_kinds.append(filters["instrument_kind"])
            assert filters["query"] == "BTC"
            return (spot,) if filters["instrument_kind"] == "spot" else ()

    monkeypatch.setattr(reference, "_application", lambda _context: Application())
    answers = iter(["BTC", "1"])
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(answers))

    selected = reference.select_market(
        interactive_context,
        allowed_instrument_kinds=("spot", "option"),
    )

    assert selected is spot
    assert seen_kinds == ["spot", "option"]
    output = capsys.readouterr().out
    assert "股票" not in output


def test_market_selection_explains_when_no_supported_market_matches(
    interactive_context, monkeypatch, capsys
) -> None:
    class Application:
        def find_markets(self, **_filters):
            return ()

    monkeypatch.setattr(reference, "_application", lambda _context: Application())
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "ACCS")

    selected = reference.select_market(
        interactive_context,
        allowed_instrument_kinds=("spot", "option"),
    )

    assert selected is None
    assert "当前支持：现货、期权" in capsys.readouterr().out
