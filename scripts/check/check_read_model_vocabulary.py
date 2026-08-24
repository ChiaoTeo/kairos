#!/usr/bin/env python3
"""Reject generic read-model terminology outside approved contexts."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCANNED_ROOTS = ("crates", "kairospy", "schemas", "scripts", "tests", "docs")
SCANNED_SUFFIXES = {".md", ".py", ".rs", ".sql", ".toml", ".fbs"}
IGNORED_PARTS = {".git", ".agent-work", "target", "generated", "__pycache__"}
TERM = re.compile("pro" + r"jection|pro" + "jected", re.IGNORECASE)
NAMING_GUIDE = Path("docs/architecture/read-model-and-query-naming.md")
COLUMN_SELECTION = Path("kairospy/research/apps/data/application/readers.py")
VOCABULARY_CHECK = Path("scripts/check/check_read_model_vocabulary.py")


def allowed(relative: Path, line: str) -> bool:
    if relative == NAMING_GUIDE:
        return True
    if relative == VOCABULARY_CHECK:
        return True
    return relative == COLUMN_SELECTION and "column " + "pro" + "jection" in line


def main() -> int:
    failures: list[str] = []
    for root_name in SCANNED_ROOTS:
        for path in (ROOT / root_name).rglob("*"):
            if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
                continue
            relative = path.relative_to(ROOT)
            if any(part in IGNORED_PARTS for part in relative.parts):
                continue
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if TERM.search(line) and not allowed(relative, line):
                    failures.append(f"{relative}:{line_number}: {line.strip()}")
    if failures:
        print("read-model vocabulary check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("read-model vocabulary check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
