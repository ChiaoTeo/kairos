from __future__ import annotations

from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

from kairospy.contracts import _native as native_loader


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
    assert info.contract_fingerprint == f"kairos.{owner}.contract.v2"
    assert getattr(native, reader) is not None
    prefix = owner.title()
    assert issubclass(getattr(native, f"{prefix}InvalidCurrentViewError"), ValueError)
    assert issubclass(
        getattr(native, f"{prefix}CurrentViewUnavailableError"), RuntimeError
    )


@pytest.mark.parametrize(
    ("owner", "identity"),
    (
        ("account", {"account_id": "main"}),
        ("capital", {"capital_group_id": "group-a"}),
        ("execution", {}),
        ("market", {}),
        ("risk", {"actor_id": "risk-main"}),
    ),
)
def test_owner_named_client_unifies_control_events_and_optional_current(
    owner: str, identity: dict[str, str]
) -> None:
    native = import_module(f"kairospy._native_{owner}_contract")
    prefix = owner.title()
    client = getattr(native, f"{prefix}Client")(
        f"/tmp/{owner}.sock",
        workspace_id="workspace-a",
        **identity,
    )

    assert type(client.control).__name__ == f"{prefix}ControlClient"
    subscription = getattr(native, f"{prefix}LiveSubscription")
    assert subscription.__name__ == f"{prefix}LiveSubscription"
    assert subscription.__module__ == f"kairospy._native_{owner}_contract"
    assert client.current is None

    with pytest.raises(
        getattr(native, f"{prefix}InvalidInputError"), match="stream_id"
    ) as invalid:
        getattr(native, f"{prefix}Client")(
            f"/tmp/{owner}.sock",
            workspace_id="workspace-a",
            stream_id=0,
            **identity,
        )
    assert invalid.value.code == "invalid_input"


@pytest.mark.parametrize("owner", ("account", "capital", "execution", "market", "risk"))
def test_owner_errors_expose_stable_contract_codes(owner: str) -> None:
    native = import_module(f"kairospy._native_{owner}_contract")
    prefix = owner.title()

    with pytest.raises(getattr(native, f"{prefix}InvalidEventError")) as invalid:
        native.decode_event(b"invalid")
    assert invalid.value.code == "invalid_wire_data"
    assert getattr(native, f"{prefix}ControlUnavailableError").code == (
        "transport_unavailable"
    )
    assert getattr(native, f"{prefix}ControlRejectedError").code == (
        "operation_rejected"
    )
    assert getattr(native, f"{prefix}CurrentViewUnavailableError").code == (
        "current_view_unavailable"
    )


@pytest.mark.parametrize("owner", ("account", "capital", "execution", "market", "risk"))
def test_missing_control_socket_is_transport_unavailable(owner: str) -> None:
    native = import_module(f"kairospy._native_{owner}_contract")
    prefix = owner.title()
    client = getattr(native, f"{prefix}ControlClient")(
        f"/tmp/kairos-missing-{owner}-control.sock"
    )

    with pytest.raises(getattr(native, f"{prefix}ControlUnavailableError")) as error:
        client.health()
    assert error.value.code == "transport_unavailable"


def test_public_owner_facades_export_the_native_owner_named_client() -> None:
    for owner in ("account", "capital", "execution", "market", "risk"):
        facade = import_module(f"kairospy.contracts.{owner}")
        native = import_module(f"kairospy._native_{owner}_contract")
        name = f"{owner.title()}Client"
        assert getattr(facade, name) is getattr(native, name)
        assert not hasattr(facade, "indexed_environment_path")


@pytest.mark.parametrize(
    ("owner", "reader", "identity", "method", "arguments"),
    (
        ("account", "AccountCurrentView", ("main", "workspace-a"), "snapshot", ()),
        ("capital", "CapitalCurrentView", ("group-a", "workspace-a"), "snapshot", ()),
        ("execution", "ExecutionCurrentView", ("workspace-a",), "orders", ()),
        ("market", "MarketCurrentView", ("workspace-a",), "quote", ("market:a", "test")),
        ("risk", "RiskCurrentView", ("risk-main", "workspace-a"), "snapshot", ()),
    ),
)
def test_native_current_views_own_lazy_path_and_closed_lifecycle(
    tmp_path: Path,
    owner: str,
    reader: str,
    identity: tuple[str, ...],
    method: str,
    arguments: tuple[str, ...],
) -> None:
    native = import_module(f"kairospy._native_{owner}_contract")
    view = getattr(native, reader)(tmp_path, *identity)

    assert view.path.is_relative_to(tmp_path)
    assert view.path.name == "current.lmdb"
    view.close()
    with pytest.raises(RuntimeError, match="closed"):
        getattr(view, method)(*arguments)


def test_owner_native_loader_rejects_a_fingerprint_mismatch(monkeypatch) -> None:
    incompatible = SimpleNamespace(
        build_info=lambda: SimpleNamespace(
            owner="Market",
            api_version=1,
            contract_fingerprint="wrong",
        )
    )
    monkeypatch.setattr(native_loader, "import_module", lambda _name: incompatible)

    with pytest.raises(ImportError, match="ABI mismatch"):
        native_loader.load_owner_contract("Market")


def test_generic_native_transport_no_longer_exports_indexed_view_reader() -> None:
    native = import_module("kairospy._native_transport")

    assert not hasattr(native, "IndexedViewReader")
    assert not hasattr(native, "IndexedViewMetadata")


def test_reference_contract_extension_owns_sqlite_catalog_reads() -> None:
    native = import_module("kairospy._native_reference_contract")
    info = native.build_info()

    assert info.api_version == 1
    assert info.owner == "Reference"
    assert native.ReferenceCatalog is not None
    assert native.ReferenceReadSession is not None
    assert issubclass(native.ReferenceInvalidCatalogError, ValueError)
    assert issubclass(native.ReferenceCatalogUnavailableError, RuntimeError)


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


@pytest.mark.parametrize("field", ["bid_price", "bid_quantity", "ask_price", "ask_quantity"])
def test_execution_backtest_quote_validates_optional_decimal_fields(field: str) -> None:
    native = import_module("kairospy._native_execution_contract")

    with pytest.raises(native.ExecutionInvalidInputError, match=field):
        native.ExecutionBacktestMarketRequest.quote(
            market_id="market:BTCUSDT",
            instrument_id="instrument:BTCUSDT",
            observed_at_unix_nanos=1,
            source_id="test",
            bid_price="not-a-decimal" if field == "bid_price" else None,
            bid_quantity="not-a-decimal" if field == "bid_quantity" else None,
            ask_price="not-a-decimal" if field == "ask_price" else None,
            ask_quantity="not-a-decimal" if field == "ask_quantity" else None,
        )


def test_execution_backtest_and_replace_inputs_accept_semantic_values() -> None:
    from kairospy.primitives.decimal import Money, Price, Quantity, Rate

    native = import_module("kairospy._native_execution_contract")

    native.ReplaceOrderRequest(quantity=Quantity("2.5"), limit_price=Price("100.25"))
    native.ExecutionBacktestEquityPoint(1, Money("1000"))
    native.ExecutionBacktestInputFill(
        instrument_id="instrument:BTCUSDT",
        side="buy",
        quantity=Quantity("1"),
        price=Price("100"),
        occurred_at_unix_nanos=1,
        fee=Money("0.1"),
    )
    native.ExecutionBacktestSimulationConfig(
        fee_bps=Rate("1"), slippage_bps=Rate("2")
    )
    native.ExecutionBacktestMarketRequest.quote(
        market_id="market:BTCUSDT",
        instrument_id="instrument:BTCUSDT",
        observed_at_unix_nanos=1,
        source_id="test",
        bid_price=Price("99"),
        bid_quantity=Quantity("2"),
    )

    with pytest.raises(native.ExecutionInvalidInputError, match="cannot be constructed"):
        native.ReplaceOrderRequest(quantity=Price("2.5"))


def test_atomic_snapshot_bindings_expose_non_summary_families() -> None:
    account = import_module("kairospy._native_account_contract")
    capital = import_module("kairospy._native_capital_contract")

    assert hasattr(account.AccountCurrentSnapshot, "collateral")
    assert hasattr(capital.CapitalCurrentSnapshot, "policies")
    assert hasattr(capital.CapitalCurrentSnapshot, "facts")


def test_account_binding_owns_typed_control_requests_and_client() -> None:
    native = import_module("kairospy._native_account_contract")

    assert native.AccountControlClient is not None
    assert native.AccountSegmentsRequest(["spot"]).segments == ["spot"]
    mark = native.MarkToMarketRequest(
        "spot", "instrument:BTCUSDT", "USDT", "100.25", 10
    )
    assert mark.segment_key == "spot"
    assert native.AdvanceAccountTimeRequest(11).event_time_unix_nanos == 11
    assert native.SimulatedSettlement(
        "fill-1",
        "spot",
        "instrument:BTCUSDT",
        "1",
        "100",
        "buy",
        12,
    ) is not None
    assert native.SimulatedCapitalMutation(
        "mutation-1", "spot", "USDT", "1", "debit_liquid", 13
    ) is not None
    assert native.SimulatedCapitalMutationQuery("mutation-1", "spot") is not None

    with pytest.raises(ValueError):
        native.MarkToMarketRequest("", "instrument:BTCUSDT", "USDT", "100", 10)
    with pytest.raises(ValueError):
        native.AccountControlClient("/tmp/account.sock", timeout=0)
