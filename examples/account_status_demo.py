from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from prettytable import PrettyTable

from kairospy.application.launch import TradingSystemLauncher
from kairospy.config import LaunchConfig


ACCOUNT_CURRENT_PREFIX = "account.current."


def launch_config(config_path: str | Path, *, allow_live: bool = False) -> object:
    path = Path(config_path).expanduser().resolve()
    config = LaunchConfig.load(path)
    launcher = TradingSystemLauncher()
    if config.mode == "backtest":
        return launcher.launch_backtest_config(path)
    if config.mode == "paper":
        return launcher.launch_paper_config(path)
    if config.mode == "live":
        if not allow_live:
            raise RuntimeError("live configs require --allow-live")
        return launcher.launch_live_config(path)
    raise ValueError(f"unsupported launch.mode: {config.mode}")


def render_account_report(result: object, *, max_rows: int = 20) -> str:
    sections = [
        _summary_table(result),
        _account_views_table(result),
        _balances_table(result, max_rows=max_rows),
        _positions_table(result, max_rows=max_rows),
        _fills_table(result, max_rows=max_rows),
        _intents_table(result, max_rows=max_rows),
    ]
    return "\n\n".join(section for section in sections if section)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Launch a strategy config and print account status with PrettyTable.")
    parser.add_argument("config", help="path to a backtest, paper, or live launch config")
    parser.add_argument("--allow-live", action="store_true", help="allow live mode configs to launch")
    parser.add_argument("--max-rows", type=int, default=20, help="maximum rows per detail table")
    args = parser.parse_args(argv)

    result = launch_config(args.config, allow_live=args.allow_live)
    print(render_account_report(result, max_rows=args.max_rows))


def _summary_table(result: object) -> str:
    rows = (
        ("launch_id", _value(result, "launch_id")),
        ("mode", _mode(result)),
        ("events", _value(_value(result, "runtime"), "event_count")),
        ("intents", _value(_value(result, "runtime"), "intent_count")),
        ("initial_equity", _value(result, "initial_equity")),
        ("final_equity", _value(result, "final_equity")),
        ("net_profit", _value(result, "net_profit")),
        ("total_return", _value(result, "total_return")),
    )
    return _table("Launch", ("field", "value"), rows)


def _account_views_table(result: object) -> str:
    rows = []
    for key, view in _account_views(result):
        context = _value(view, "context")
        book = _value(view, "book") or _value(context, "book")
        rows.append(
            (
                key,
                _value(_value(context, "environment"), "value") or _value(context, "environment"),
                _value(_value(context, "identity"), "broker"),
                _value(_value(context, "identity"), "account_id"),
                _value(book, "book_key") or _value(book, "segment") or _value(view, "book_kind"),
                _value(view, "cash"),
                _value(view, "equity"),
                _value(view, "net_profit"),
                _value(view, "total_return"),
                _count(_value(view, "balances")),
                _count(_value(view, "positions")),
                _value(view, "stale"),
                _value(view, "last_event_time"),
            )
        )
    if not rows:
        account_view = _value(result, "account_view")
        if account_view is not None:
            rows.append(
                (
                    "result.account_view",
                    "",
                    "",
                    "",
                    "",
                    _value(account_view, "cash"),
                    _value(account_view, "equity"),
                    _value(account_view, "net_profit"),
                    _value(account_view, "total_return"),
                    _count(_value(account_view, "balances")),
                    _count(_value(account_view, "positions")),
                    _value(account_view, "stale"),
                    _value(account_view, "last_event_time"),
                )
            )
    return _table(
        "Accounts",
        ("key", "env", "broker", "account", "book", "cash", "equity", "pnl", "return", "balances", "positions", "stale", "updated_at"),
        rows,
    )


def _balances_table(result: object, *, max_rows: int) -> str:
    rows = []
    for key, view in _account_views_or_result(result):
        for balance in _items(_value(view, "balances")):
            rows.append(
                (
                    key,
                    _value(balance, "currency"),
                    _value(balance, "total"),
                    _value(balance, "free"),
                    _value(balance, "locked"),
                    _source(balance),
                )
            )
    return _limited_table("Balances", ("account", "currency", "total", "free", "locked", "source"), rows, max_rows=max_rows)


def _positions_table(result: object, *, max_rows: int) -> str:
    rows = []
    for key, view in _account_views_or_result(result):
        for position in _items(_value(view, "positions")):
            rows.append(
                (
                    key,
                    _value(position, "instrument_id"),
                    _value(position, "quantity"),
                    _value(position, "average_price"),
                    _value(position, "mark_price"),
                    _value(position, "unrealized_pnl"),
                    _source(position),
                )
            )
    return _limited_table(
        "Positions",
        ("account", "instrument", "quantity", "avg_price", "mark_price", "unrealized_pnl", "source"),
        rows,
        max_rows=max_rows,
    )


def _fills_table(result: object, *, max_rows: int) -> str:
    rows = []
    for fill in _items(_value(result, "fills")):
        rows.append(
            (
                _value(fill, "order_id"),
                _value(fill, "intent_id"),
                _value(fill, "instrument_id"),
                _value(fill, "side"),
                _value(fill, "quantity") or _value(fill, "fill_quantity"),
                _value(fill, "price") or _value(fill, "fill_price"),
                _value(fill, "fee"),
                _value(fill, "occurred_at"),
            )
        )
    return _limited_table("Fills", ("order", "intent", "instrument", "side", "quantity", "price", "fee", "time"), rows, max_rows=max_rows)


def _intents_table(result: object, *, max_rows: int) -> str:
    intents = _value(result, "intents")
    rows = []
    if intents is not None and hasattr(intents, "list"):
        states = intents.list()
    else:
        states = ()
    for state in _items(states):
        intent = _value(state, "intent")
        rows.append(
            (
                _value(intent, "intent_id"),
                _value(intent, "kind"),
                _value(intent, "instrument_id"),
                _value(intent, "target_quantity"),
                _value(state, "status"),
                _value(state, "active"),
                _value(state, "updated_at") or _value(intent, "created_at"),
                _value(intent, "reason") or _value(state, "reason"),
            )
        )
    return _limited_table("Intents", ("intent", "kind", "instrument", "target", "status", "active", "updated_at", "reason"), rows, max_rows=max_rows)


def _account_views(result: object) -> tuple[tuple[str, object], ...]:
    views = _value(result, "views")
    envelopes = views.envelopes() if views is not None and hasattr(views, "envelopes") else {}
    return tuple(
        (str(key), envelope.payload)
        for key, envelope in _mapping_items(envelopes)
        if str(key).startswith(ACCOUNT_CURRENT_PREFIX)
    )


def _account_views_or_result(result: object) -> tuple[tuple[str, object], ...]:
    views = _account_views(result)
    if views:
        return views
    account_view = _value(result, "account_view")
    return () if account_view is None else (("result.account_view", account_view),)


def _limited_table(title: str, headers: Sequence[str], rows: Sequence[Sequence[object]], *, max_rows: int) -> str:
    if not rows:
        return _table(title, headers, (("none", *("" for _ in headers[1:])),))
    visible = tuple(rows[:max_rows])
    if len(rows) > max_rows:
        visible = (*visible, (f"... {len(rows) - max_rows} more", *("" for _ in headers[1:])))
    return _table(title, headers, visible)


def _table(title: str, headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    table = PrettyTable()
    table.title = title
    table.field_names = [str(header) for header in headers]
    table.align = "l"
    for row in rows:
        table.add_row([_text(value) for value in row])
    return table.get_string()


def _mode(result: object) -> object:
    mode = _value(result, "mode")
    return _value(mode, "value") or mode


def _source(value: object) -> object:
    source = _value(value, "source")
    return _value(source, "value") or source


def _value(value: object, name: str, default: object = None) -> object:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _items(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return tuple(value) if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)) else ()


def _mapping_items(value: object) -> tuple[tuple[object, object], ...]:
    if isinstance(value, Mapping):
        return tuple(value.items())
    return ()


def _count(value: object) -> int:
    return len(_items(value))


def _text(value: object) -> str:
    if value is None:
        return ""
    text = str(_value(value, "value") or value)
    return text.replace("\n", " ")


if __name__ == "__main__":
    main()
