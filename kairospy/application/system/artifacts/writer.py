from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import StrEnum
import json
from pathlib import Path
from typing import Mapping


class RunArtifactWriter:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def write(self, *, result: object, normalized_config: Mapping[str, object]) -> Mapping[str, object]:
        self.directory.mkdir(parents=True, exist_ok=True)
        summary = self.summary(result)
        self._write_json("summary.json", summary)
        self._write_json("config.normalized.json", normalized_config)
        self._write_json("metrics.json", getattr(result, "metrics", {}))
        self._write_jsonl("equity.jsonl", getattr(result, "equity_curve", ()))
        self._write_jsonl("fills.jsonl", getattr(result, "fills", ()))
        self._write_jsonl("trades.jsonl", getattr(result, "trades", ()))
        self._write_jsonl("intent_states.jsonl", _intent_states(result))
        self._write_jsonl("decision_trace.jsonl", getattr(result, "decision_trace", ()))
        self._write_jsonl("risk_snapshots.jsonl", getattr(result, "risk_snapshots", ()))
        return summary

    def summary(self, result: object) -> Mapping[str, object]:
        runtime = getattr(result, "runtime", None)
        mode = getattr(result, "mode", None)
        return _jsonable(
            {
                "run_id": getattr(result, "run_id", None),
                "mode": getattr(mode, "value", mode),
                "strategy_id": getattr(runtime, "strategy_id", None),
                "event_count": getattr(runtime, "event_count", None),
                "intent_count": getattr(runtime, "intent_count", None),
                "fills": len(tuple(getattr(result, "fills", ()) or ())),
                "closed_trades": len(tuple(getattr(result, "trades", ()) or ())),
                "decision_trace_count": len(tuple(getattr(result, "decision_trace", ()) or ())),
                "risk_snapshot_count": len(tuple(getattr(result, "risk_snapshots", ()) or ())),
                "initial_equity": getattr(result, "initial_equity", None),
                "final_equity": getattr(result, "final_equity", None),
                "net_profit": getattr(result, "net_profit", None),
                "total_return": getattr(result, "total_return", None),
                "metrics": getattr(result, "metrics", {}),
            }
        )

    def _write_json(self, filename: str, value: object) -> None:
        (self.directory / filename).write_text(json.dumps(_jsonable(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _write_jsonl(self, filename: str, values: object) -> None:
        rows = tuple(values or ())
        (self.directory / filename).write_text("".join(json.dumps(_jsonable(row), sort_keys=True) + "\n" for row in rows), encoding="utf-8")


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


def _intent_states(result: object) -> tuple[object, ...]:
    runtime = getattr(result, "runtime", None)
    existing = getattr(runtime, "intent_states", None)
    if existing is not None:
        return tuple(existing)
    intents = getattr(result, "intents", None)
    list_intents = getattr(intents, "list", None)
    if not callable(list_intents):
        return ()
    strategy_id = getattr(runtime, "strategy_id", None)
    if strategy_id is None:
        return tuple(list_intents())
    return tuple(list_intents(strategy_id=strategy_id))


__all__ = ["RunArtifactWriter"]
