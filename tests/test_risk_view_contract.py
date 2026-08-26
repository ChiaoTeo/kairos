from pathlib import Path

from kairospy.infrastructure.contracts.risk import risk_indexed_environment_path, risk_indexed_key


def test_risk_indexed_path_and_key_match_rust_contract() -> None:
    assert risk_indexed_environment_path(Path("/runtime"), "risk:instance-1") == Path(
        "/runtime/views/v3/Risk/risk-risk%3Ainstance-1/epoch-1/current.lmdb"
    )
    assert risk_indexed_key("policy") == b"\x01\x00\x06policy"
