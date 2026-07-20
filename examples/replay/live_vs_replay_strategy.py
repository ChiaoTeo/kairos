"""Prove Projection, Decision and Intent parity from a canonical capture."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples._support import MidpointTargetStrategy, example_context
from kairospy.contracts import canonical_from_domain_market_data
from kairospy.domain.identity import InstrumentId
from kairospy.domain.market_data import Quote
from kairospy.market_data import CanonicalCaptureWriter, CapturedCanonicalEventSource, IterableEventSource
from kairospy.storage.codec import to_primitive
from kairospy.strategies import CanonicalStrategyEventSession


def fixture_events():
    instrument = InstrumentId("crypto:binance:spot:BTCUSDT")
    start = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    for index, (bid, ask) in enumerate((("98", "99"), ("100", "101"), ("97", "98")), 1):
        at = start + timedelta(seconds=index)
        yield canonical_from_domain_market_data(
            Quote(instrument, Decimal(bid), Decimal(ask), Decimal("1"), Decimal("1"), at),
            source="binance", source_instance="example", stream_id="btcusdt@bookTicker",
            receive_time=at, published_time=at, source_sequence=index, receive_sequence=index,
        )[0]


async def compare(path: Path | None) -> dict[str, object]:
    temporary = tempfile.TemporaryDirectory() if path is None else None
    try:
        capture = path or Path(temporary.name) / "fixture.canonical.jsonl"
        if path is None:
            events = tuple(fixture_events())
            writer = CanonicalCaptureWriter(capture, session_id="example-replay", source="binance")
            for event in events:
                writer.append(event)
            writer.finalize()
        else:
            events = tuple([event async for event in CapturedCanonicalEventSource(capture).events()])
        threshold = Decimal("100")
        live = await CanonicalStrategyEventSession(
            IterableEventSource(events), MidpointTargetStrategy(threshold), example_context,
        ).run()
        replay = await CanonicalStrategyEventSession(
            CapturedCanonicalEventSource(capture), MidpointTargetStrategy(threshold), example_context,
        ).run()
        if live != replay:
            raise RuntimeError("live and replay strategy sessions diverged")
        return {
            "capture": str(capture), "events": len(events),
            "projection_hash": live.projection_hash, "decision_hash": live.decision_hash,
            "intent_hash": live.intent_hash, "audit_hash": live.audit_hash,
            "live_equals_replay": True,
            "decisions": to_primitive(live.decisions), "intents": to_primitive(live.intents),
        }
    finally:
        if temporary is not None:
            temporary.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, help="canonical JSONL with its .manifest.json")
    print(json.dumps(to_primitive(asyncio.run(compare(parser.parse_args().capture))), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
