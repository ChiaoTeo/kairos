from pathlib import Path

from kairospy.infrastructure.contracts.risk import indexed_environment_path


def test_risk_indexed_path_matches_rust_contract() -> None:
    assert indexed_environment_path(Path("/runtime"), "risk:instance-1", "workspace", None, None) == Path(
        "/runtime/views/v3/Risk/risk-risk%3Ainstance-1/epoch-1/current.lmdb"
    )
