"""Versioned command transport contract for strategy capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from time import time_ns
from typing import Mapping, TypeVar


TCommand = TypeVar("TCommand")


@dataclass(frozen=True, slots=True)
class CommandSource:
    stream_id: str | None = None
    sequence: int | None = None
    snapshot_id: str | None = None

    def as_dict(self) -> dict[str, object] | None:
        if (
            self.stream_id is None
            and self.sequence is None
            and self.snapshot_id is None
        ):
            return None
        return {
            "stream_id": self.stream_id,
            "sequence": self.sequence,
            "snapshot_id": self.snapshot_id,
        }


@dataclass(frozen=True, slots=True)
class CommandEnvelope:
    command_id: str
    operation: str
    strategy_id: str
    instance_id: str
    payload: Mapping[str, object]
    launch_id: str | None = None
    idempotency_key: str | None = None
    issued_at_unix_nanos: int | None = None
    source: CommandSource | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported command schema version")
        if not all(
            (
                self.command_id.strip(),
                self.operation.strip(),
                self.strategy_id.strip(),
                self.instance_id.strip(),
            )
        ):
            raise ValueError("command identity and operation are required")
        if self.issued_at_unix_nanos is not None and self.issued_at_unix_nanos < 0:
            raise ValueError("command timestamp cannot be negative")

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "command_id": self.command_id,
            "idempotency_key": self.idempotency_key or self.command_id,
            "issued_at_unix_nanos": self.issued_at_unix_nanos or time_ns(),
            "operation": self.operation,
            "strategy_id": self.strategy_id,
            "launch_id": self.launch_id,
            "instance_id": self.instance_id,
            "source": None if self.source is None else self.source.as_dict(),
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class StrategyCommand:
    """A request delivered to a strategy's optional command lifecycle hook."""

    request_id: str
    kind: str
    source: str = ""
    payload: object | None = None

    def __post_init__(self) -> None:
        if not self.request_id.strip() or not self.kind.strip():
            raise ValueError("strategy command request_id and kind are required")

    def as_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "kind": self.kind,
            "source": self.source,
            "payload": dict(self.payload)
            if isinstance(self.payload, Mapping)
            else self.payload,
        }

    def require_payload(self, model_type: type[TCommand]) -> TCommand:
        """Validate an open command payload and restore its concrete type."""
        if isinstance(self.payload, model_type):
            return self.payload
        if self.payload is None:
            raise ValueError(f"command {self.kind!r} requires a payload")
        validator = getattr(model_type, "model_validate", None)
        if callable(validator):
            value = validator(self.payload)
            if not isinstance(value, model_type):
                raise TypeError("command payload validator returned the wrong type")
            return value
        if isinstance(self.payload, Mapping):
            try:
                return model_type(**dict(self.payload))
            except TypeError as error:
                raise ValueError(
                    f"invalid payload for command {self.kind!r}: {error}"
                ) from error
        raise ValueError(
            f"command {self.kind!r} payload cannot be converted to {model_type.__name__}"
        )
