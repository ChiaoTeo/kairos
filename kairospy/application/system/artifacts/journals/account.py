from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import StrEnum
import json
from pathlib import Path
from typing import Mapping


class RunAccountJournal:
    def __init__(self, run_directory: str | Path, *, run_id: str | None = None, mode: str | None = None) -> None:
        self.directory = Path(run_directory) / "account"
        self.run_id = run_id
        self.mode = mode
        self.current_path = self.directory / "current.json"
        self.equity_path = self.directory / "equity.jsonl"
        self.positions_path = self.directory / "positions.jsonl"
        self.fills_path = self.directory / "fills.jsonl"
        self.orders_path = self.directory / "orders.jsonl"
        self.trades_path = self.directory / "trades.jsonl"

    def record_backtest_result(self, result: object, *, run_id: str | None = None, mode: str | None = None) -> None:
        run_id = run_id or self.run_id
        mode = mode or self.mode
        account_view = getattr(result, "account_view", None)
        summary = {
            "run_id": run_id,
            "mode": mode or getattr(getattr(result, "mode", None), "value", None),
            "account": _jsonable(getattr(account_view, "context", None)),
            "initial_equity": getattr(result, "initial_equity", None),
            "final_equity": getattr(result, "final_equity", None),
            "net_profit": getattr(result, "net_profit", None),
            "total_return": getattr(result, "total_return", None),
            "fills": len(tuple(getattr(result, "fills", ()))),
            "closed_trades": len(tuple(getattr(result, "trades", ()))),
            "account_view": account_view,
        }
        self.write_current(summary)
        self.replace_jsonl(self.equity_path, _equity_rows(result, run_id=run_id, mode=mode))
        self.replace_jsonl(self.positions_path, _position_rows(account_view, run_id=run_id, mode=mode))
        self.replace_jsonl(self.fills_path, _rows(getattr(result, "fills", ()), run_id=run_id, mode=mode))
        self.replace_jsonl(self.orders_path, _order_rows(account_view, run_id=run_id, mode=mode))
        self.replace_jsonl(self.trades_path, _rows(getattr(result, "trades", ()), run_id=run_id, mode=mode))

    def record_account_view(self, account_view: object, *, run_id: str | None = None, mode: str | None = None) -> None:
        run_id = run_id or self.run_id
        mode = mode or self.mode
        current = self.read_current()
        current.update({
            "run_id": run_id,
            "mode": mode,
            "account_view": account_view,
            "equity": getattr(account_view, "equity", None),
            "net_profit": getattr(account_view, "net_profit", None),
            "total_return": getattr(account_view, "total_return", None),
        })
        self.write_current(current)
        self.append_jsonl(self.equity_path, _equity_row(account_view, run_id=run_id, mode=mode))
        self.replace_jsonl(self.positions_path, _position_rows(account_view, run_id=run_id, mode=mode))
        self.replace_jsonl(self.orders_path, _order_rows(account_view, run_id=run_id, mode=mode))

    def record_fill(self, fill: object, *, run_id: str | None = None, mode: str | None = None) -> None:
        self.append_jsonl(self.fills_path, {"run_id": run_id or self.run_id, "mode": mode or self.mode, **_jsonable(fill)})

    def write_current(self, payload: Mapping[str, object]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.current_path.write_text(json.dumps(_jsonable(payload), indent=2, sort_keys=True), encoding="utf-8")

    def read_current(self) -> dict[str, object]:
        return _read_json(self.current_path)

    def read_rows(self, name: str, *, limit: int | None = None) -> list[dict[str, object]]:
        path = self.path_for(name)
        try:
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except FileNotFoundError:
            rows = []
        return rows[-limit:] if limit is not None else rows

    def replace_jsonl(self, path: Path, rows: list[Mapping[str, object]]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(_jsonable(row), sort_keys=True) + "\n" for row in rows), encoding="utf-8")

    def append_jsonl(self, path: Path, row: Mapping[str, object]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_jsonable(row), sort_keys=True) + "\n")

    def path_for(self, name: str) -> Path:
        paths = {
            "summary": self.current_path,
            "current": self.current_path,
            "pnl": self.equity_path,
            "equity": self.equity_path,
            "positions": self.positions_path,
            "fills": self.fills_path,
            "orders": self.orders_path,
            "trades": self.trades_path,
        }
        if name not in paths:
            raise KeyError(f"unknown account journal: {name}")
        return paths[name]


def _equity_rows(result: object, *, run_id: str | None, mode: str | None) -> list[dict[str, object]]:
    account_view = getattr(result, "account_view", None)
    if account_view is None:
        return []
    return [_equity_row(account_view, run_id=run_id, mode=mode)]


def _equity_row(account_view: object, *, run_id: str | None, mode: str | None) -> dict[str, object]:
    return {
        "run_id": run_id,
        "mode": mode,
        "time": getattr(account_view, "last_event_time", None),
        "equity": getattr(account_view, "equity", None),
        "cash": getattr(account_view, "cash", None),
        "net_profit": getattr(account_view, "net_profit", None),
        "total_return": getattr(account_view, "total_return", None),
        "positions": getattr(account_view, "positions", ()),
    }


def _position_rows(account_view: object, *, run_id: str | None, mode: str | None) -> list[dict[str, object]]:
    if account_view is None:
        return []
    return [
        {
            "run_id": run_id,
            "mode": mode,
            "time": getattr(account_view, "last_event_time", None),
            **_jsonable(position),
        }
        for position in tuple(getattr(account_view, "positions", ()))
    ]


def _order_rows(account_view: object, *, run_id: str | None, mode: str | None) -> list[dict[str, object]]:
    if account_view is None:
        return []
    orders = [*tuple(getattr(account_view, "open_orders", ())), *tuple(getattr(account_view, "pending_orders", ()))]
    return [{"run_id": run_id, "mode": mode, **_jsonable(order)} for order in orders]


def _rows(values: object, *, run_id: str | None, mode: str | None) -> list[dict[str, object]]:
    return [{"run_id": run_id, "mode": mode, **_jsonable(value)} for value in tuple(values or ())]


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


__all__ = ["RunAccountJournal"]
