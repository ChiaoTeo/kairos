from __future__ import annotations

from importlib import import_module

import pytest


@pytest.mark.parametrize(
    ("owner", "reader"),
    (
        ("account", "AccountCurrentView"),
        ("capital", "CapitalCurrentView"),
        ("execution", "ExecutionCurrentView"),
        ("market", "MarketCurrentView"),
        ("risk", "RiskCurrentView"),
    ),
)
def test_owner_contract_extension_is_the_only_business_view_entrypoint(
    owner: str, reader: str
) -> None:
    native = import_module(f"kairospy._native_{owner}_contract")
    info = native.build_info()

    assert info.api_version == 1
    assert info.owner.lower() == owner
    assert getattr(native, reader) is not None
    prefix = owner.title()
    assert issubclass(getattr(native, f"{prefix}InvalidCurrentViewError"), ValueError)
    assert issubclass(
        getattr(native, f"{prefix}CurrentViewUnavailableError"), RuntimeError
    )


def test_generic_native_transport_no_longer_exports_indexed_view_reader() -> None:
    native = import_module("kairospy._native_transport")

    assert not hasattr(native, "IndexedViewReader")
    assert not hasattr(native, "IndexedViewMetadata")


def test_execution_binding_exposes_every_indexed_family() -> None:
    native = import_module("kairospy._native_execution_contract")

    for method in (
        "orders",
        "intents",
        "algorithm_runs",
        "commitments",
        "risk_reservations",
        "unknown_remote_orders",
    ):
        assert hasattr(native.ExecutionCurrentView, method)


def test_atomic_snapshot_bindings_expose_non_summary_families() -> None:
    account = import_module("kairospy._native_account_contract")
    capital = import_module("kairospy._native_capital_contract")

    assert hasattr(account.AccountCurrentSnapshot, "collateral")
    assert hasattr(capital.CapitalCurrentSnapshot, "policies")
    assert hasattr(capital.CapitalCurrentSnapshot, "facts")
