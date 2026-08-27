"""Risk events backed only by the owner native contract."""

from typing import TYPE_CHECKING, Any

from kairospy.infrastructure.contracts._native import load_owner_contract

if TYPE_CHECKING:
    from kairospy._native_risk_contract import RiskEvent


def _native() -> Any:
    return load_owner_contract("Risk")


def decode_event(payload: bytes) -> RiskEvent:
    return _native().decode_event(payload)


if not TYPE_CHECKING:
    RiskEvent = _native().RiskEvent


__all__ = ["RiskEvent", "decode_event"]
