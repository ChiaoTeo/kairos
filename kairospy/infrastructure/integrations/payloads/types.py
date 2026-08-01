from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from typing import TypeAlias


IntegrationParams: TypeAlias = Mapping[str, object]
RawPayload: TypeAlias = Mapping[str, object]
RawPayloadRows: TypeAlias = Iterable[RawPayload]
RawPayloadStream: TypeAlias = AsyncIterator[RawPayload]
RawOrderResponse: TypeAlias = Mapping[str, object]
OrderSubmissionResponse: TypeAlias = Mapping[str, object]


__all__ = [
    "IntegrationParams",
    "OrderSubmissionResponse",
    "RawOrderResponse",
    "RawPayload",
    "RawPayloadRows",
    "RawPayloadStream",
]
