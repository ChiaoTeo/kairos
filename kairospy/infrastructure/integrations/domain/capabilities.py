from __future__ import annotations

from dataclasses import dataclass

from kairospy.infrastructure.integrations.domain.participants import integration_key


@dataclass(frozen=True, slots=True)
class ProductLine:
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", integration_key(self.value))


@dataclass(frozen=True, slots=True)
class CapabilityRef:
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", integration_key(self.value))


MARKET_DATA_CAPABILITY = CapabilityRef("market_data")
REFERENCE_CAPABILITY = CapabilityRef("reference")
ACCOUNT_CAPABILITY = CapabilityRef("account")
ORDER_EXECUTION_CAPABILITY = CapabilityRef("order_execution")
PRIVATE_STREAM_CAPABILITY = CapabilityRef("private_stream")


__all__ = [
    "ACCOUNT_CAPABILITY",
    "CapabilityRef",
    "MARKET_DATA_CAPABILITY",
    "ORDER_EXECUTION_CAPABILITY",
    "PRIVATE_STREAM_CAPABILITY",
    "ProductLine",
    "REFERENCE_CAPABILITY",
]
