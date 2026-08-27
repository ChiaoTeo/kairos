from pathlib import Path

from kairospy.infrastructure.contracts.risk import risk_indexed_environment_path


def test_risk_indexed_path_matches_rust_contract() -> None:
    assert risk_indexed_environment_path(Path("/runtime"), "risk:instance-1") == Path(
        "/runtime/views/v3/Risk/risk-risk%3Ainstance-1/epoch-1/current.lmdb"
    )
