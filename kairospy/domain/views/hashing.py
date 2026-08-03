from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Mapping
from uuid import UUID


def view_hash(value: object) -> str:
    return sha256(json.dumps(_primitive(value), sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _primitive(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _primitive(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_primitive(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (Decimal, datetime, UUID)):
        return str(value)
    if hasattr(value, "to_dict"):
        return _primitive(value.to_dict())
    if hasattr(value, "__dict__"):
        return _primitive(vars(value))
    return value


__all__ = ["view_hash"]
