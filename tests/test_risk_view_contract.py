from pathlib import Path

from kairospy.infrastructure.contracts.risk import RiskViewKey


def test_risk_view_key_matches_rust_resource_identity() -> None:
    key = RiskViewKey(actor_id="risk:instance-1")

    assert key.canonical_key() == "risk.latest"
    assert key.resource_path(Path("/runtime")) == Path(
        "/runtime/risk/risk%3Ainstance-1/latest/current.snapshot"
    )
