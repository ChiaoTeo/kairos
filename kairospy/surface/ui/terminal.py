from __future__ import annotations

import json
from typing import Iterable, Mapping, TextIO


def write_jsonl(rows: Iterable[Mapping[str, object]], stream: TextIO) -> int:
    count = 0
    for row in rows:
        stream.write(json.dumps(dict(row), sort_keys=True, separators=(",", ":")) + "\n")
        count += 1
    return count
