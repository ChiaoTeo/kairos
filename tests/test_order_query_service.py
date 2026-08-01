from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

import pytest

from kairospy.application.query import OrderQueryService


class _Views:
    def __init__(self, payloads: Mapping[str, object]) -> None:
        self.payloads = dict(payloads)

    def get(self, key: str, default: object = None) -> object:
        return self.payloads.get(key, default)

    def require(self, key: str) -> object:
        try:
            return self.payloads[key]
        except KeyError as error:
            raise KeyError(f"view has no value: {key}") from error

    def envelopes(self) -> Mapping[str, object]:
        return MappingProxyType(self.payloads)


def test_order_query_service_reads_execution_current_order_status() -> None:
    service = OrderQueryService(
        _Views(
            {
                "execution.current": {
                    "latest_order": {"order_id": "order-2", "status": "filled"},
                    "orders": (
                        {"order_id": "order-1", "status": "acknowledged"},
                        {"order_id": "order-2", "status": "filled"},
                    ),
                }
            }
        )
    )

    status = service.status("order-1")

    assert status["status"] == "acknowledged"
    assert status["order"]["order_id"] == "order-1"


def test_order_query_service_rejects_unknown_order() -> None:
    service = OrderQueryService(_Views({"execution.current": {"orders": ()}}))

    with pytest.raises(KeyError):
        service.status("missing")
