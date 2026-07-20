"""Contract verifier usable with the Python reference or a future Rust binary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys


ROOT = Path(__file__).parent


def verify(command: str) -> dict[str, object]:
    completed = subprocess.run(
        shlex.split(command), cwd=Path.cwd(), check=True, capture_output=True, text=True,
    )
    observed = tuple(json.loads(line) for line in completed.stdout.splitlines() if line.strip())
    vectors = json.loads((ROOT / "contract_vectors.json").read_text())
    if len(observed) != len(vectors):
        raise RuntimeError(f"gateway emitted {len(observed)} events for {len(vectors)} vectors")
    for event, vector in zip(observed, vectors):
        expected = vector["expected"]
        for field in ("schema_id", "schema_version", "kind", "source", "stream_id", "source_sequence"):
            if event[field] != expected[field]:
                raise RuntimeError(f"{vector['name']} differs at {field}: {event[field]} != {expected[field]}")
        for field, value in expected["payload"].items():
            if _scalar(event["payload"][field]) != value:
                raise RuntimeError(f"{vector['name']} differs at payload.{field}")
        if not event.get("message_id") or not event.get("partition_key"):
            raise RuntimeError(f"{vector['name']} lacks canonical identity")
    return {"command": command, "vectors": len(vectors), "passed": True}


def _scalar(value):
    if isinstance(value, dict) and len(value) == 1:
        return next(iter(value.values()))
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--command",
        default=f"{shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 'python_event_source.py'))}",
        help="gateway command that writes one canonical JSON object per stdout line",
    )
    print(json.dumps(verify(parser.parse_args().command), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
