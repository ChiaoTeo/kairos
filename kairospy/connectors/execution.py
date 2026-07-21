from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable

from kairospy.trading.capability import ExecutionCapabilities
from kairospy.trading.identity import AccountKey, InstitutionId, VenueId
from kairospy.ports import (
    ComboOrderRequest,
    Environment,
    OrderAck,
    OrderRequest,
    VenueOrderRecovery,
)


@runtime_checkable
class ExecutionService(Protocol):
    service_id: str
    service_kind: str
    institution_id: InstitutionId
    venue_id: VenueId
    environment: Environment
    capabilities: ExecutionCapabilities

    def place_order(self, request: OrderRequest) -> OrderAck:
        ...

    def cancel_order(self, account: AccountKey, venue_order_id: str) -> None:
        ...

    def open_orders(self, account: AccountKey) -> tuple[str, ...]:
        ...

    def recover_order(
        self,
        account: AccountKey,
        request: OrderRequest | ComboOrderRequest,
        venue_order_id: str | None,
    ) -> VenueOrderRecovery:
        ...


@runtime_checkable
class ComboExecutionService(ExecutionService, Protocol):
    def place_combo_order(self, request: ComboOrderRequest) -> OrderAck:
        ...


@dataclass(frozen=True, slots=True)
class ExecutionServiceSpec:
    service_id: str
    institution_id: str
    venue_id: str
    environment: str
    capabilities: Mapping[str, object] = field(default_factory=dict)

    service_kind: str = "execution"

    def __post_init__(self) -> None:
        for name in ("service_id", "institution_id", "venue_id", "environment", "service_kind"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"execution service spec {name} cannot be empty")
