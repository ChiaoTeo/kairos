from __future__ import annotations

from importlib import import_module
import subprocess

import pytest


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
    assert event.sequence == 1
    assert event.change.kind == "status_changed"
    assert event.provenance.source_id == "binance:spot"
    assert event.provenance.provider_sequence == 10


def test_rust_capital_event_fixture_preserves_decimal_text_and_absence() -> None:
    event = _rust_event("capital", "emit_policy_event_fixture")

    assert event.kind == "policy_changed"
    assert event.sequence == 11
    assert event.launch_id == "launch"
    assert event.instance_id == "instance"
    assert event.payload.version == 7
    assert event.payload.minimum == "10.25"
    assert event.payload.default_target == "20.5"


def test_rust_execution_event_fixture_preserves_metadata_and_nested_values() -> None:
    event = _rust_event("execution", "emit_lifecycle_event_fixture")

    assert event.kind == "intent_update"
    assert event.sequence == 2
    assert event.data.intent_id == "intent-1"
    assert event.data.status == "satisfied"
    assert event.data.previous_status == "executing"
    assert event.data.intent.strategy_decision_id == "strategy-a:decision:1"


def test_rust_risk_event_fixture_preserves_typed_optional_fields() -> None:
    event = _rust_event("risk", "emit_circuit_event_fixture")

    assert event.kind == "circuit_changed"
    assert event.sequence == 13
    assert event.launch_id == "launch"
    assert event.instance_id == "instance"
    assert event.payload.open is True
    assert event.payload.opened_at_unix_nanos == 12
    assert event.payload.reset_at_unix_nanos is None


def test_rust_market_event_fixture_preserves_decimal_and_scope_values() -> None:
    event = _rust_event("market", "emit_quote_event_fixture")

    assert event.kind == "quote"
    assert event.sequence == 17
    assert event.launch_id == "launch"
    assert event.instance_id == "instance"
    assert event.data.scope.market_id == "market:fixture"
    assert event.data.bid_price.mantissa == 12345
    assert event.data.bid_price.scale == 2
    assert event.data.ask_price.mantissa == 12355
