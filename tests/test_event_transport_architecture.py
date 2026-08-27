from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_owner_servers_require_explicit_event_channel() -> None:
    servers = (
        "account/src/bin/kairos-account-server.rs",
        "capital/src/bin/kairos-capital-server.rs",
        "execution/src/bin/kairos-execution-server.rs",
        "reference/src/bin/kairos-reference-server.rs",
        "risk/src/bin/kairos-risk-server.rs",
    )
    for relative in servers:
        source = (ROOT / "crates/modules" / relative).read_text(encoding="utf-8")
        assert "default_value = kairos_conflux::DEFAULT_AERON_CHANNEL" not in source
        assert "default_value = kairos_capital_contract::DEFAULT_AERON_CHANNEL" not in source


def test_market_live_assembly_rejects_missing_event_channel() -> None:
    source = (
        ROOT
        / "crates/modules/market/src/composition/launch/assembly.rs"
    ).read_text(encoding="utf-8")
    assert "requires an explicit System event route" in source
