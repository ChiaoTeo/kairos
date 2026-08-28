from decimal import Decimal
from pathlib import Path
import re

import pytest

from kairospy.investment.application.eventing import DataEvent, EventMetadata
from kairospy.primitives.account import AccountId, SegmentKey
from kairospy.primitives.capital import CapitalGroupId, FundingObjectiveId
from kairospy.primitives.decimal import (
    MAX_DECIMAL_SCALE,
    DecimalValue,
    Money,
    MoneyLike,
    Price,
    PriceDelta,
    PriceLike,
    Quantity,
    QuantityLike,
    Rate,
    SignedQuantity,
)
from kairospy.primitives.execution import FillId, IntentId, OrderId
from kairospy.primitives.reference import (
    ExchangeId,
    InstrumentId,
    ListingId,
    MarketId,
    MarketIdRead,
)
from kairospy.primitives.time import (
    datetime_from_unix_nanos,
    unix_nanos_from_datetime,
)
from kairospy.primitives.runtime import InstanceId, LaunchId, WorkspaceId
from kairospy.primitives.risk import DecisionId, PolicyId, ReservationId


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
    assert (primitives / "capital.py").is_file()
    assert (primitives / "decimal.py").is_file()
    assert (primitives / "execution.py").is_file()
    assert (primitives / "reference.py").is_file()
    assert (primitives / "risk.py").is_file()
    assert (primitives / "runtime.py").is_file()
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
        CapitalGroupId,
        FundingObjectiveId,
        WorkspaceId,
        LaunchId,
        InstanceId,
        DecisionId,
        PolicyId,
        ReservationId,
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


def test_read_identity_nominal_type_does_not_allocate_a_wrapper() -> None:
    native_value = "market:main"

    read_value = MarketIdRead(native_value)

    assert read_value is native_value
    assert read_value == "market:main"


@pytest.mark.parametrize(
    ("value_type", "value"),
    (
        (Quantity, "42.100"),
        (SignedQuantity, "-42.100"),
        (Price, "42.100"),
        (PriceDelta, "-0.1250"),
        (Money, "-100.2500"),
        (Rate, "-0.00100"),
    ),
)
def test_decimal_primitives_normalize_exact_values(value_type, value: str) -> None:
    result = value_type(value)

    assert result.value == result.value
    assert result.scale <= MAX_DECIMAL_SCALE
    assert str(result) == format(result.value, "f")
    assert value_type(result) == result


def test_decimal_primitives_preserve_distinct_business_meanings() -> None:
    price = Price("42.10")
    quantity = Quantity("2.00")

    assert price.mantissa == 421
    assert price.scale == 1
    assert quantity.mantissa == 2
    assert quantity.scale == 0
    assert price != quantity
    assert isinstance(price, DecimalValue)
    assert isinstance(price, PriceLike)
    assert isinstance(quantity, QuantityLike)
    assert price.semantic_type == "price"
    assert quantity.semantic_type == "quantity"
    with pytest.raises(TypeError, match="cannot be constructed"):
        Price(quantity)


def test_decimal_primitives_enforce_rust_value_limits() -> None:
    with pytest.raises(ValueError, match="positive"):
        Price("0")
    with pytest.raises(ValueError, match="negative"):
        Quantity("-0.1")
    with pytest.raises(ValueError, match="scale exceeds"):
        Rate("0.0000000000000000001")
    with pytest.raises(ValueError, match="coefficient exceeds"):
        Money(str(2**63))
    with pytest.raises(ValueError, match="finite"):
        Money(Decimal("NaN"))
    with pytest.raises(TypeError, match="exact decimal"):
        Money(0.1)
    with pytest.raises(TypeError, match="exact decimal"):
        Money(True)


def test_decimal_primitive_checked_operations_preserve_semantics() -> None:
    price = Price("42.5")
    quantity = Quantity("2")
    signed_quantity = SignedQuantity("-2")

    assert price * quantity == Money("85")
    assert quantity * price == Money("85")
    assert signed_quantity * price == Money("-85")
    assert price - Price("40") == PriceDelta("2.5")
    assert Money("85") / SignedQuantity("2") == Price("42.5")
    assert Quantity("1.25").is_multiple_of(Quantity("0.05"))
    with pytest.raises(ValueError, match="negative"):
        Quantity("1") - Quantity("2")
    with pytest.raises(ValueError, match="zero"):
        Money("1") / SignedQuantity("0")


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
