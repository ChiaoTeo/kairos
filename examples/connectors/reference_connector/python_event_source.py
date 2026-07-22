"""Reference gateway: Raw Binance JSON -> one Canonical Event JSON line."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from kairospy.integrations.connectors.binance.market_stream import parse_market_stream_event
from kairospy.integrations.contracts import canonical_from_trading_market_data
from kairospy.identity import InstrumentId
from kairospy.infrastructure.storage.codec import to_primitive


ROOT = Path(__file__).parent


def produce() -> tuple[dict[str, object], ...]:
    vectors = json.loads((ROOT / "contract_vectors.json").read_text())
    events = []
    for receive_sequence, vector in enumerate(vectors):
        instrument = InstrumentId(vector["instrument_id"])
        value = parse_market_stream_event(vector["raw"], {vector["raw"]["s"]: instrument})
        receive_time = datetime.fromtimestamp(vector["raw"]["E"] / 1000, timezone.utc)
        canonical = canonical_from_trading_market_data(
            value, source="binance", source_instance="reference-python-gateway",
            stream_id="btcusdt@bookTicker", receive_time=receive_time,
            published_time=receive_time, source_sequence=vector["raw"]["u"],
            receive_sequence=receive_sequence,
        )[0]
        events.append(to_primitive(canonical))
    return tuple(events)


def main() -> None:
    for event in produce():
        print(json.dumps(event, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
