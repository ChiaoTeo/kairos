from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
from pathlib import Path


def append_jsonl(path: str | Path, row: Mapping[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), sort_keys=True) + "\n")


def replace_jsonl(path: str | Path, rows: Iterable[object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def read_jsonl_objects(path: str | Path, *, limit: int | None = None) -> list[dict[str, object]]:
    try:
        rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    except FileNotFoundError:
        rows = []
    objects = [row for row in rows if isinstance(row, dict)]
    return objects[-limit:] if limit is not None else objects


__all__ = ["append_jsonl", "read_jsonl_objects", "replace_jsonl"]
