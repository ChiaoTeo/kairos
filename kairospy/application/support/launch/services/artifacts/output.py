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
    ) -> None:
        self.launch_id = launch_id
        self.mode = mode
        self.store = store

    def write_result(self, *, result: object, normalized_config: Mapping[str, object]) -> Mapping[str, object]:
        summary = self.summary(result)
        self.store.write_json("summary", summary)
        self.store.write_json("config.normalized", jsonable(normalized_config))
        self.store.write_json("metrics", jsonable(getattr(result, "metrics", {})))
        self.store.replace_records("equity", tuple(getattr(result, "equity_curve", ()) or ()))
        self.store.replace_records("fills", tuple(getattr(result, "fills", ()) or ()))
        self.store.replace_records("trades", tuple(getattr(result, "trades", ()) or ()))
        self.store.replace_records("intent_states", _intent_states(result))
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
        self.store.append_record(stream, jsonable(record))

    def update_current(self, namespace: str, payload: Mapping[str, object]) -> None:
        current = self.store.read_current(namespace)
        current.update({"launch_id": self.launch_id, "mode": self.mode, **payload})
        self.store.update_current(namespace, jsonable(current))


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
