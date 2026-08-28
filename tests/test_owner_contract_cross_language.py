from __future__ import annotations

from importlib import import_module
import subprocess

import pytest

from kairospy.primitives.decimal import Price, PriceLike, Quantity, QuantityLike


def _rust_event(owner: str, example: str) -> object:
    native = import_module(f"kairospy._native_{owner}_contract")
    return native.decode_event(_rust_event_bytes(owner, example))


def _rust_event_bytes(owner: str, example: str) -> bytes:
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "-p",
            f"kairos-{owner}-contract",
            "--example",
            example,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return bytes.fromhex(completed.stdout.strip())


@pytest.mark.parametrize(
    ("owner", "example"),
    (
        ("account", "emit_status_event_fixture"),
        ("capital", "emit_policy_event_fixture"),
        ("execution", "emit_lifecycle_event_fixture"),
        ("market", "emit_quote_event_fixture"),
        ("risk", "emit_circuit_event_fixture"),
    ),
)
def test_rust_owner_fixture_corruption_has_stable_python_error_code(
    owner: str, example: str
) -> None:
    native = import_module(f"kairospy._native_{owner}_contract")
    payload = _rust_event_bytes(owner, example)

    with pytest.raises(getattr(native, f"{owner.title()}InvalidEventError")) as error:
        native.decode_event(payload[:8])
    assert error.value.code == "invalid_wire_data"


def test_rust_account_event_fixture_has_the_same_python_typed_fields() -> None:
    event = _rust_event("account", "emit_status_event_fixture")

    assert event.account_id == "main"
    assert event.metadata.sequence == 1
    assert event.kind == "account_status_changed"
    assert event.provenance.source_id == "binance:spot"
    assert event.provenance.provider_sequence == 10


def test_market_native_batch_projects_stable_owned_events() -> None:
    native = import_module("kairospy._native_market_contract")
    payload = _rust_event_bytes("market", "emit_quote_event_fixture")

    events = native.decode_events([payload, payload])
    del payload

    assert len(events) == 2
    assert events[0].kind == "quote_updated"
    assert events[0].data.bid_price.value == Price("123.45").value
    assert events[1].data.ask_price.value == Price("123.55").value


def test_rust_capital_event_fixture_preserves_decimal_text_and_absence() -> None:
    event = _rust_event("capital", "emit_policy_event_fixture")

    assert event.kind == "policy_changed"
    assert event.metadata.sequence == 11
    assert event.metadata.launch_id == "launch"
    assert event.metadata.instance_id == "instance"
    assert event.data.version == 7
    assert event.data.minimum.semantic_type == "quantity"
    assert event.data.minimum.value == Quantity("10.25").value
    assert event.data.default_target.value == Quantity("20.5").value


def test_rust_execution_event_fixture_preserves_metadata_and_nested_values() -> None:
    event = _rust_event("execution", "emit_lifecycle_event_fixture")

    assert event.kind == "intent_lifecycle_changed"
    assert event.metadata.sequence == 2
    assert event.data.intent_id == "intent-1"
    assert event.data.status == "satisfied"
    assert event.data.previous_status == "executing"
    assert event.data.intent.strategy_decision_id == "strategy-a:decision:1"


def test_rust_risk_event_fixture_preserves_typed_optional_fields() -> None:
    event = _rust_event("risk", "emit_circuit_event_fixture")

    assert event.kind == "circuit_opened"
    assert event.metadata.sequence == 13
    assert event.metadata.launch_id == "launch"
    assert event.metadata.instance_id == "instance"
    assert event.data.open is True
    assert event.data.opened_at_unix_nanos == 12
    assert event.data.reset_at_unix_nanos is None


def test_rust_market_event_fixture_preserves_decimal_and_scope_values() -> None:
    event = _rust_event("market", "emit_quote_event_fixture")

    assert event.kind == "quote_updated"
    assert event.metadata.sequence == 17
    assert event.metadata.launch_id == "launch"
    assert event.metadata.instance_id == "instance"
    assert event.data.scope.market_id == "market:fixture"
    assert event.data.bid_price.mantissa == 12345
    assert event.data.bid_price.scale == 2
    assert event.data.ask_price.mantissa == 12355
    assert event.data.bid_price.semantic_type == "price"
    assert isinstance(event.data.bid_price, PriceLike)
    assert Price(event.data.bid_price) == Price("123.45")


@pytest.mark.parametrize(
    "owner", ("account", "capital", "execution", "market", "reference", "risk")
)
def test_owner_extensions_do_not_publish_generic_native_decimal(owner: str) -> None:
    native = import_module(f"kairospy._native_{owner}_contract")

    assert not hasattr(native, "NativeDecimal")
    assert not hasattr(native, "_NativeSemanticDecimal")


def test_native_market_values_preserve_distinct_semantics_without_rewrapping() -> None:
    native = import_module("kairospy._native_market_contract")
    event = native.MarketEvent.simulation_bar(
        sequence=1,
        market_id="market:test",
        instrument_id="instrument:test",
        provider="simulation",
        bar_spec_id="1m",
        open="10",
        high="12",
        low="9",
        close="11",
        volume="2.5",
        occurred_at_unix_nanos=1,
    )

    assert event.data.close.semantic_type == "price"
    assert isinstance(event.data.close, PriceLike)
    assert event.data.volume.semantic_type == "quantity"
    assert isinstance(event.data.volume, QuantityLike)
    with pytest.raises(TypeError, match="cannot be constructed"):
        Price(event.data.volume)
