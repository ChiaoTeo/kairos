"""Fail-closed loader for private owner-contract extensions."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType


_ABI_VERSION = 1
_FINGERPRINTS = {
    "Account": "kairos.account.contract.v2",
    "Capital": "kairos.capital.contract.v2",
    "Execution": "kairos.execution.contract.v2",
    "Market": "kairos.market.contract.v2",
    "Risk": "kairos.risk.contract.v2",
}


def load_owner_contract(owner: str) -> ModuleType:
    """Load one private extension only when all owner ABI facts match."""

    expected_fingerprint = _FINGERPRINTS.get(owner)
    if expected_fingerprint is None:
        raise ImportError(f"unsupported owner contract {owner!r}")
    module = import_module(f"kairospy._native_{owner.lower()}_contract")
    info = module.build_info()
    actual = (
        getattr(info, "owner", None),
        getattr(info, "api_version", None),
        getattr(info, "contract_fingerprint", None),
    )
    expected = (owner, _ABI_VERSION, expected_fingerprint)
    if actual != expected:
        raise ImportError(
            f"kairospy {owner} native contract ABI mismatch: "
            f"expected {expected!r}, received {actual!r}"
        )
    return module


__all__ = ["load_owner_contract"]
