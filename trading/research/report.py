from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path

from .analyzer import ResearchResult


def write_csv(result: ResearchResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=result.columns)
        writer.writeheader()
        for row in result.rows:
            writer.writerow(asdict(row))
    return path


def summarize(result: ResearchResult) -> str:
    return "\n".join(
        (
            f"Run: {result.run_id}",
            f"Contracts: {len(result.rows)}",
            f"Completeness: {result.completeness_rate:.1%}",
            f"Stale: {result.stale_rate:.1%}",
            f"Snapshot span: {result.snapshot_span_seconds:.3f}s",
            f"IV smile points: {len(result.iv_smile)}",
            f"Put/call pairs: {len(result.put_call_pairs)}",
        )
    )
