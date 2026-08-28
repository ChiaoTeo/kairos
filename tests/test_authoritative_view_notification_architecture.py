from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_owner_processes_commit_current_view_before_notification_attempt() -> None:
    owners = {
        "account": "fn publish(&mut self",
        "risk": "fn publish_contract_outputs(&mut self",
        "capital": "fn publish_contract_outputs(",
        "market": "async fn publish(&mut self",
    }
    for owner, marker in owners.items():
        source = _source(f"crates/modules/{owner}/src/application/conflux.rs")
        publication = source[source.index(marker) :]
        assert publication.index(".indexed") < publication.index(".aeron.publish")

    execution = _source("crates/modules/execution/src/application/conflux.rs")
    publication = execution[execution.index("fn publish(&mut self") :]
    assert publication.index("self.publish_views(context") < publication.index(
        ".aeron.publish"
    )
    assert ".indexed" in execution[execution.index("fn publish_views(") :]


def test_python_current_views_project_borrowed_lmdb_values_directly() -> None:
    for owner in ("account", "risk", "capital"):
        source = _source(f"crates/modules/{owner}/contract/py/src/lib.rs")
        assert "project_direct(reader)" in source or "project_snapshot_direct(reader)" in source
        assert "reader.snapshot()" not in source

    execution = _source("crates/modules/execution/contract/py/src/lib.rs")
    for method in (
        "map_orders",
        "map_intents",
        "map_algorithm_runs",
        "map_commitments",
        "map_risk_reservations",
        "map_unknown_remote_orders",
    ):
        assert method in execution
    assert "reader.snapshot()" not in execution


def test_live_consumers_do_not_recover_notifications_from_current_views() -> None:
    application_root = ROOT / "kairospy/investment/apps"
    sources = "\n".join(
        (application_root / owner / "application/application.py").read_text(encoding="utf-8")
        for owner in ("account", "execution", "market", "risk")
    )
    for obsolete in (
        "_recover_from_current_view",
        "recovery_snapshot",
        "_latest_trades",
        "def latest_trade",
    ):
        assert obsolete not in sources
