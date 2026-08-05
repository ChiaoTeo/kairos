from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from kairospy.application.usecases.account.application.results import (
    AccountBalanceResult,
    AccountOpenOrdersResult,
    AccountSnapshotResult,
)
from kairospy.application.usecases.account.protocol import AccountOrderExecutor, AccountReadRequest
from kairospy.domain.account import AccountSnapshot
from kairospy.application.usecases.account.application.configuration import AccountStore


ROOT = Path(__file__).parents[1]


def test_account_legacy_abstractions_are_deleted() -> None:
    assert not (ROOT / "kairospy/application/usecases/account/domain/books.py").exists()
    assert not (ROOT / "kairospy/application/usecases/account/services/snapshots.py").exists()
    assert not (ROOT / "kairospy/application/usecases/workspace/domain/workspace/accounts.py").exists()
    assert not (ROOT / "kairospy/application/usecases/account/application/commands.py").exists()


def test_account_cli_composition_exposes_explicit_application_capabilities() -> None:
    source = (ROOT / "kairospy/application/support/composition/application/cli.py").read_text(encoding="utf-8")
    assert "AccountCommandApplication" not in source
    for capability in ("administration", "connection", "queries", "simulation", "leases", "model"):
        assert f"{capability}:" in source


def test_account_cli_has_scope_boundary_without_legacy_book_or_vendor_params() -> None:
    source = (ROOT / "kairospy/surface/cli/commands/account.py").read_text(encoding="utf-8")
    assert "--book" not in source
    assert "--params-json" not in source
    assert 'account_app.command("create")' not in source
    assert 'account_app.command("connect")' in source
    assert 'account_app.command("simulate")' in source


def test_account_actor_assembly_use_named_contracts_for_dependencies() -> None:
    for relative in (
        "kairospy/application/actor/account/application/actor.py",
        "kairospy/application/actor/account/application/assembly.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert ": object" not in source
        assert "object | None" not in source


def test_account_read_protocol_does_not_carry_untyped_options() -> None:
    assert {field.name for field in fields(AccountReadRequest)} == {
        "context",
        "observed_at",
        "symbol",
        "fetch_orders",
    }


def test_account_snapshot_and_query_results_are_strongly_typed() -> None:
    assert "raw" not in {field.name for field in fields(AccountSnapshot)}
    assert AccountBalanceResult.__annotations__["rows"] == "tuple[AccountBalanceRow, ...]"
    assert AccountOpenOrdersResult.__annotations__["orders"] == "tuple[OpenOrderSnapshot, ...]"
    assert AccountSnapshotResult.__annotations__["snapshot"] == "AccountSnapshot"
    assert AccountOrderExecutor is not None


def test_local_binding_alias_does_not_replace_remote_account_identity(tmp_path: Path) -> None:
    path = tmp_path / "accounts" / "main.toml"
    path.parent.mkdir()
    path.write_text(
        '[account]\n'
        'id = "main"\n'
        'broker = "binance"\n'
        'environment = "live"\n'
        '\n'
        '[discovery]\n'
        'remote_identity = "remote-42"\n'
        '\n'
        '[segments.spot]\n'
        'product_family = "spot"\n',
        encoding="utf-8",
    )
    record = AccountStore.load(path.parent).get("main")
    assert record.identity.account_id == "remote-42"
    assert record.account_id == "main"
