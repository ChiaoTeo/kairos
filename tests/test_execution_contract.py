from base64 import b64decode
from pathlib import Path

from kairospy.contracts.execution import (
    ExecutionCurrentView,
    decode_event,
)


INTENT_ACCEPTED = b64decode(
    "EAAAAEVJQTIIAAwACAAEAAgAAABMAAAAHAAAABgALAAoACQAHAAYABQAAAAQAAAAAAAEABgAAAAKAAAAAAAAAAAAAAAsAAAAOAAAAEgAAAABAAAAAAAAAEwAAABgAAAACAAAAGludGVudC0xAAAAAAoAAABpbnN0YW5jZS0xAAAOAAAAd29ya3NwYWNlOmRlbW8AAAkAAABleGVjdXRpb24AAAAQAAAAZXhlY3V0aW9uLmV2ZW50cwAAAAAHAAAAZXZlbnQtMQA="
)
INTENT_LIFECYCLE_CHANGED = b64decode(
    "HAAAAEVJTDIUADAALAAoACcAJgAgAAQAHAAYABQAAAABAAAAAAAAAAAAAAAAAAAAAAAAAFwAAABoAQAAWAAAAAAABwSAAAAAHAAAABgALAAoACQAHAAYABQAEAAMAAAAAAAEABgAAAAUAAAAAAAAAOgBAAD0AQAAAAIAABACAAACAAAAAAAAABQCAAAoAgAAAAAAAAEAAAAUAQAAKAAsACgAJAAgABwAAAAPAAgAGAAAAAAAAAAAAAAAAAAUAAAABAAQACgAAABUAQAAJAAAAAAAAAEgAAAAZAAAAGQAAAB4AQAAhAEAAFABAABcAQAAWP///wEAAAAUAAAAEAAwAC8AKAAkACAADAAEABAAAAAKAAAAAAAAAGQAAAAAAAAAAAAAAAAAAAAAAAAAmAAAAKwAAADcAAAAAAAAAQAAAAABAAAAIAAAAAAAGgA0ADAALAAoACQAIAAcAAAABAAAAAAAGAAaAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAgAAAAOAAAAEAAAABUAAAAbAAAAHQAAAB8AAAABAAEAAQAAAAGAAAAZmlsbGVkAAAHAAAAb3JkZXItMQAHAAAAcm91dGUtMQATAAAAbWFya2V0OnRlc3Q6QlRDVVNEVAAXAAAAaW5zdHJ1bWVudDp0ZXN0OkJUQ1VTRFQABAAAAHNwb3QAAAAABAAAAG1haW4AAAAABQAAAGxlZy0xAAAAFQAAAHN0cmF0ZWd5LWE6ZGVjaXNpb246MQAAAAoAAABzdHJhdGVneS1hAAAIAAAAaW50ZW50LTEAAAAACgAAAGluc3RhbmNlLTEAAAgAAABsYXVuY2gtMQAAAAAOAAAAd29ya3NwYWNlOmRlbW8AAAkAAABleGVjdXRpb24AAAAQAAAAZXhlY3V0aW9uLmV2ZW50cwAAAAAHAAAAZXZlbnQtMgA="
)


def test_execution_indexed_path_matches_rust_contract() -> None:
    assert ExecutionCurrentView("/tmp/workspace", "workspace").path == Path(
        "/tmp/workspace/views/v3/Execution/execution-main/epoch-1/current.lmdb"
    )


def test_execution_event_decoder_returns_owner_native_event() -> None:
    decoded = decode_event(INTENT_ACCEPTED)
    assert type(decoded).__module__ == "kairospy._native_execution_contract"
    assert decoded.kind == "intent_accepted"
    assert decoded.data.intent_id == "intent-1"
    assert decoded.metadata.stream_id == "execution.events"


def test_intent_lifecycle_changed_is_strategy_scoped_and_decision_correlated() -> None:
    event = decode_event(INTENT_LIFECYCLE_CHANGED)
    assert event.kind == "intent_lifecycle_changed"
    assert event.strategy_id == "strategy-a"
    assert event.data.status == "satisfied"
    assert event.data.previous_status == "executing"
    assert event.data.order_ids == ["order-1"]
    assert event.data.intent.strategy_decision_id == "strategy-a:decision:1"
    assert event.data.intent.account_ids == ["main"]
    benchmark = event.data.intent.execution_benchmarks[0]
    assert benchmark.kind == "arrival"
    assert benchmark.leg_id == "leg-1"
    assert benchmark.instrument_id == "instrument:test:BTCUSDT"
    assert benchmark.market_id == "market:test:BTCUSDT"
    assert benchmark.price.value == 100
    assert benchmark.observed_at_unix_nanos == 10
