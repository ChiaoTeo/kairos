from pathlib import Path

from kairospy.contracts.risk import RiskCurrentView


def test_risk_indexed_path_matches_rust_contract() -> None:
    assert RiskCurrentView(Path("/runtime"), "risk:instance-1", "workspace").path == Path(
        "/runtime/views/v3/Risk/risk-risk%3Ainstance-1/epoch-1/current.lmdb"
    )
