from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4


class ControlRequestKind(StrEnum):
    SUBSCRIPTION = "subscription"
    PAUSE = "pause"
    RESUME = "resume"
    REDUCE_ONLY = "reduce_only"
    PARAMETER_UPDATE = "parameter_update"


@dataclass(frozen=True, slots=True)
class ControlRequest:
    request_id: str
    strategy_id: str
    kind: ControlRequestKind
    requested_at: datetime | None = None
    payload: Mapping[str, object] = MappingProxyType({})
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.request_id.strip() or not self.strategy_id.strip():
            raise ValueError("control request identity fields are required")
        if self.requested_at is not None and self.requested_at.tzinfo is None:
            raise ValueError("control request timestamp must be timezone-aware")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


class ControlJournal:
    def __init__(self, requests: tuple[ControlRequest, ...] = ()) -> None:
        self._requests: list[ControlRequest] = list(requests)

    def record(self, request: ControlRequest) -> ControlRequest:
        self._requests.append(request)
        return request

    def list(self, *, strategy_id: str | None = None, kind: ControlRequestKind | str | None = None) -> tuple[ControlRequest, ...]:
        requests = self._requests
        if strategy_id is not None:
            requests = [item for item in requests if item.strategy_id == strategy_id]
        if kind is not None:
            expected = ControlRequestKind(kind)
            requests = [item for item in requests if item.kind is expected]
        return tuple(requests)


class ControlFactory:
    def __init__(self, *, strategy_id: str, requested_at: datetime | None, journal: ControlJournal) -> None:
        self.strategy_id = strategy_id
        self.requested_at = requested_at
        self.journal = journal

    def request_subscription(
        self,
        stream: str,
        *,
        action: str = "add",
        reason: str = "",
        request_id: str | None = None,
    ) -> ControlRequest:
        return self.request(
            ControlRequestKind.SUBSCRIPTION,
            {"stream": stream, "action": action},
            reason=reason,
            request_id=request_id,
        )

    def request_pause(self, *, reason: str = "", request_id: str | None = None) -> ControlRequest:
        return self.request(ControlRequestKind.PAUSE, {}, reason=reason, request_id=request_id)

    def request_resume(self, *, reason: str = "", request_id: str | None = None) -> ControlRequest:
        return self.request(ControlRequestKind.RESUME, {}, reason=reason, request_id=request_id)

    def request_reduce_only(
        self,
        enabled: bool = True,
        *,
        reason: str = "",
        request_id: str | None = None,
    ) -> ControlRequest:
        return self.request(ControlRequestKind.REDUCE_ONLY, {"enabled": enabled}, reason=reason, request_id=request_id)

    def request_parameter_update(
        self,
        name: str,
        value: object,
        *,
        reason: str = "",
        request_id: str | None = None,
    ) -> ControlRequest:
        return self.request(
            ControlRequestKind.PARAMETER_UPDATE,
            {"name": name, "value": value},
            reason=reason,
            request_id=request_id,
        )

    def request(
        self,
        kind: ControlRequestKind | str,
        payload: Mapping[str, object],
        *,
        reason: str = "",
        request_id: str | None = None,
    ) -> ControlRequest:
        return self.journal.record(
            ControlRequest(
                request_id=request_id or f"control-{uuid4()}",
                strategy_id=self.strategy_id,
                kind=ControlRequestKind(kind),
                requested_at=self.requested_at,
                payload=payload,
                reason=reason,
            )
        )


__all__ = [
    "ControlFactory",
    "ControlJournal",
    "ControlRequest",
    "ControlRequestKind",
]
