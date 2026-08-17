from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def rust_sources(path: Path) -> str:
    return "\n".join(source.read_text() for source in path.rglob("*.rs"))


def test_account_does_not_reintroduce_shared_identity_definitions() -> None:
    account = rust_sources(ROOT / "crates/modules/account")
    shared_identities = (
        "AccountId",
        "AssetId",
        "FillId",
        "InstrumentId",
        "SegmentKey",
        "RemoteOrderId",
    )

    for name in shared_identities:
        definition = rf"\b(?:struct|enum|type)\s+{name}\b"
        assert re.search(definition, account) is None, (
            f"Account must use kairos-primitives::{name}, not define a parallel type"
        )


def test_removed_account_identity_compatibility_name_stays_removed() -> None:
    crates = rust_sources(ROOT / "crates")
    assert "ExternalOrderId" not in crates
    assert "FillSide" not in crates
