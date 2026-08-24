from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from decimal import Decimal
from pathlib import Path

from .errors import StrategyStateTypeError


class StrategyState:
    """Instance-scoped JSON state with explicit, statically typed accessors."""

    schema_version = 1

    def __init__(
        self,
        path: Path | None = None,
        *,
        strategy_id: str = "",
        instance_id: str = "",
        initial: Mapping[str, object] | None = None,
    ) -> None:
        self.path = path
        self.strategy_id = strategy_id
        self.instance_id = instance_id
        self._values: dict[str, object] = dict(initial or {})
        if path is not None and path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("strategy state document must be an object")
            if value.get("schema_version") != self.schema_version:
                raise ValueError("unsupported strategy state schema version")
            stored = value.get("state", {})
            if not isinstance(stored, dict):
                raise ValueError("strategy state payload must be an object")
            self._values.update(stored)

    def contains(self, key: str) -> bool:
        return key in self._values

    def delete(self, key: str) -> None:
        self._values.pop(key, None)

    def get_int(self, key: str, default: int = 0) -> int:
        value = self._values.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            self._mismatch(key, "int", value)
        return value

    def get_decimal(self, key: str, default: Decimal = Decimal("0")) -> Decimal:
        value = self._values.get(key, {"$decimal": str(default)})
        if isinstance(value, dict) and set(value) == {"$decimal"}:
            raw = value["$decimal"]
            if isinstance(raw, str):
                return Decimal(raw)
        if isinstance(value, Decimal):
            return value
        self._mismatch(key, "Decimal", value)

    def get_str(self, key: str, default: str = "") -> str:
        value = self._values.get(key, default)
        if not isinstance(value, str):
            self._mismatch(key, "str", value)
        return value

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self._values.get(key, default)
        if not isinstance(value, bool):
            self._mismatch(key, "bool", value)
        return value

    def set_int(self, key: str, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            self._mismatch(key, "int", value)
        self._values[key] = value

    def set_decimal(self, key: str, value: Decimal) -> None:
        if not isinstance(value, Decimal):
            self._mismatch(key, "Decimal", value)
        self._values[key] = {"$decimal": str(value)}

    def set_str(self, key: str, value: str) -> None:
        if not isinstance(value, str):
            self._mismatch(key, "str", value)
        self._values[key] = value

    def set_bool(self, key: str, value: bool) -> None:
        if not isinstance(value, bool):
            self._mismatch(key, "bool", value)
        self._values[key] = value

    def increment(self, key: str, amount: int = 1) -> int:
        if isinstance(amount, bool) or not isinstance(amount, int):
            self._mismatch(key, "int increment", amount)
        value = self.get_int(key) + amount
        self._values[key] = value
        return value

    def keys(self) -> Iterator[str]:
        return iter(self._values)

    def checkpoint(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"schema_version": self.schema_version, "state": self._values},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _mismatch(self, key: str, expected: str, value: object):
        raise StrategyStateTypeError(
            strategy_id=self.strategy_id,
            instance_id=self.instance_id,
            key=key,
            expected=expected,
            actual=_json_type(value),
        )


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__
