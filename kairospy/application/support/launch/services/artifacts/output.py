from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Mapping


class LaunchOutput:
    def __init__(
        self,
        store: object,
        *,
        launch_id: str | None = None,
        mode: str | None = None,
        write_legacy_jsonl: bool = False,
    ) -> None:
        self.launch_id = launch_id
        self.mode = mode
        self.write_legacy_jsonl = write_legacy_jsonl
        self.store = store

    def write_result(self, *, result: object, normalized_config: Mapping[str, object]) -> Mapping[str, object]:
        summary = self.summary(result)
        self.store.json("summary").write(summary)
        self.store.json("config.normalized").write(normalized_config)
        self.store.json("metrics").write(getattr(result, "metrics", {}))
        if not self.write_legacy_jsonl:
            return summary
        self.store.jsonl("equity").replace(tuple(getattr(result, "equity_curve", ()) or ()))
        self.store.jsonl("fills").replace(tuple(getattr(result, "fills", ()) or ()))
        self.store.jsonl("trades").replace(tuple(getattr(result, "trades", ()) or ()))
        self.store.jsonl("intent_states").replace(_intent_states(result))
        return summary

    def summary(self, result: object) -> Mapping[str, object]:
        runtime = getattr(result, "runtime", None)
        mode = getattr(result, "mode", None)
        return jsonable(
            {
                "launch_id": getattr(result, "launch_id", None),
                "mode": getattr(mode, "value", mode),
                "strategy_id": getattr(runtime, "program_id", None),
                "event_count": getattr(runtime, "event_count", None),
                "intent_count": _intent_count(result),
                "fills": len(tuple(getattr(result, "fills", ()) or ())),
                "closed_trades": len(tuple(getattr(result, "trades", ()) or ())),
                "initial_equity": getattr(result, "initial_equity", None),
                "final_equity": getattr(result, "final_equity", None),
                "net_profit": getattr(result, "net_profit", None),
                "total_return": getattr(result, "total_return", None),
                "metrics": getattr(result, "metrics", {}),
            }
        )

    def append_history(self, stream: str, record: Mapping[str, object]) -> None:
        self.store.jsonl(stream).append(record)

    def update_current(self, namespace: str, payload: Mapping[str, object]) -> None:
        current = self.store.namespace(namespace).json("current").read()
        current.update({"launch_id": self.launch_id, "mode": self.mode, **payload})
        self.store.namespace(namespace).json("current").write(current)
        if not self.write_legacy_jsonl or namespace != "account":
            return
        account_view = payload.get("account_view")
        account = self.store.namespace("account")
        account.jsonl("equity").append(_equity_row(account_view, launch_id=self.launch_id, mode=self.mode))
        account.jsonl("positions").replace(_position_rows(account_view, launch_id=self.launch_id, mode=self.mode))
        account.jsonl("orders").replace(_order_rows(account_view, launch_id=self.launch_id, mode=self.mode))


def _intent_states(result: object) -> tuple[object, ...]:
    runtime = getattr(result, "runtime", None)
    existing = getattr(runtime, "intent_states", None)
    if existing is not None:
        return tuple(existing)
    intents = getattr(result, "intents", None)
    list_intents = getattr(intents, "list", None)
    if not callable(list_intents):
        return ()
    strategy_id = getattr(runtime, "program_id", None)
    if strategy_id is None:
        return tuple(list_intents())
    return tuple(list_intents(strategy_id=strategy_id))


def _equity_row(account_view: object, *, launch_id: str | None, mode: str | None) -> dict[str, object]:
    return {
        "launch_id": launch_id,
        "mode": mode,
        "time": getattr(account_view, "last_event_time", None),
        "equity": getattr(account_view, "equity", None),
        "cash": getattr(account_view, "cash", None),
        "net_profit": getattr(account_view, "net_profit", None),
        "total_return": getattr(account_view, "total_return", None),
        "positions": getattr(account_view, "positions", ()),
    }


def _position_rows(account_view: object, *, launch_id: str | None, mode: str | None) -> list[dict[str, object]]:
    if account_view is None:
        return []
    return [
        {
            "launch_id": launch_id,
            "mode": mode,
            "time": getattr(account_view, "last_event_time", None),
            **jsonable(position),
        }
        for position in tuple(getattr(account_view, "positions", ()))
    ]


def _order_rows(account_view: object, *, launch_id: str | None, mode: str | None) -> list[dict[str, object]]:
    if account_view is None:
        return []
    orders = [*tuple(getattr(account_view, "open_orders", ())), *tuple(getattr(account_view, "pending_orders", ()))]
    return [{"launch_id": launch_id, "mode": mode, **jsonable(order)} for order in orders]


def jsonable(value: object) -> object:
    if is_dataclass(value):
        return {field.name: jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    slots = getattr(value, "__slots__", None)
    if isinstance(slots, (tuple, list)):
        return {str(name): jsonable(getattr(value, name)) for name in slots if isinstance(name, str) and hasattr(value, name)}
    if hasattr(value, "__dict__"):
        return {str(key): jsonable(item) for key, item in vars(value).items()}
    return value


def _intent_count(result: object) -> int | None:
    intents = getattr(result, "intents", None)
    listing = getattr(intents, "list", None)
    return len(listing()) if callable(listing) else None


__all__ = ["LaunchOutput"]
