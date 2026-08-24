from pathlib import Path
import re

import pytest

from kairospy.application.events import DataEvent, EventMetadata
from kairospy.primitives.account import AccountId, SegmentKey
from kairospy.primitives.execution import FillId, IntentId, OrderId
from kairospy.primitives.reference import (
    ExchangeId,
    InstrumentId,
    ListingId,
    MarketId,
)
from kairospy.primitives.time import (
    datetime_from_unix_nanos,
    unix_nanos_from_datetime,
)


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


def test_python_primitives_are_grouped_by_governing_vocabulary() -> None:
    primitives = ROOT / "kairospy/primitives"

    assert not (ROOT / "kairospy/domain_types").exists()
    assert (primitives / "account.py").is_file()
    assert (primitives / "execution.py").is_file()
    assert (primitives / "reference.py").is_file()
    assert (primitives / "time.py").is_file()
    assert "import" not in (primitives / "__init__.py").read_text().split('"""')[-1]


def test_python_primitives_do_not_depend_on_application_or_infrastructure() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "kairospy/primitives").glob("*.py")
    )

    for forbidden in (
        "kairospy.application",
        "kairospy.infrastructure",
        "kairospy.domain_types",
        "flatbuffers",
    ):
        assert forbidden not in source


@pytest.mark.parametrize(
    "value_type",
    (
        AccountId,
        SegmentKey,
        FillId,
        IntentId,
        OrderId,
        ExchangeId,
        InstrumentId,
        ListingId,
        MarketId,
    ),
)
def test_text_primitives_reject_invalid_values_without_normalizing(value_type) -> None:
    with pytest.raises(ValueError, match="empty"):
        value_type("")
    with pytest.raises(ValueError, match="whitespace"):
        value_type(" value")
    with pytest.raises(ValueError, match="whitespace"):
        value_type("value ")
    with pytest.raises(TypeError, match="text"):
        value_type(1)


def test_text_primitives_preserve_identity_and_wire_representation() -> None:
    account_id = AccountId("account:main")

    assert str(account_id) == "account:main"
    assert account_id.value == "account:main"
    assert account_id == AccountId("account:main")
    assert account_id != InstrumentId("account:main")
    assert {account_id: "main"}[AccountId("account:main")] == "main"


def test_time_conversions_are_utc_exact_to_python_microsecond_precision() -> None:
    value = 1_725_000_000_123_456_000
    converted = datetime_from_unix_nanos(value)

    assert unix_nanos_from_datetime(converted) == value
    with pytest.raises(ValueError, match="non-negative"):
        datetime_from_unix_nanos(-1)
    with pytest.raises(ValueError, match="timezone-aware"):
        unix_nanos_from_datetime(converted.replace(tzinfo=None))


def test_application_event_messages_live_outside_primitives() -> None:
    metadata = EventMetadata(stream_id="account:main", sequence=1)
    event = DataEvent(data={"status": "active"}, metadata=metadata)

    assert event.metadata is metadata
    assert not (ROOT / "kairospy/primitives/events.py").exists()
