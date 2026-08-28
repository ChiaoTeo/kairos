"""Benchmark Market owner-native single versus batch event materialization.

Run after rebuilding the owner extensions:
    uv pip install --python .venv/bin/python -e .
    uv run python scripts/maintenance/benchmark_market_notification_batch.py
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from importlib import import_module
import json
from pathlib import Path
import statistics
import subprocess
import time
import tracemalloc


ROOT = Path(__file__).resolve().parents[2]


def _fixture() -> bytes:
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "-p",
            "kairos-market-contract",
            "--example",
            "emit_quote_event_fixture",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return bytes.fromhex(completed.stdout.strip())


def _percentile(values: list[int], percentile: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * percentile))]


def _measure(
    operation: Callable[[], Sequence[object]],
    *,
    iterations: int,
    frames_per_iteration: int,
) -> dict[str, object]:
    wall_samples: list[int] = []
    cpu_started = time.process_time_ns()
    tracemalloc.start()
    for _ in range(iterations):
        started = time.perf_counter_ns()
        values = operation()
        wall_samples.append(time.perf_counter_ns() - started)
        if len(values) != frames_per_iteration:
            raise RuntimeError("Market notification decoder returned an incomplete batch")
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    cpu_nanos = time.process_time_ns() - cpu_started
    total_frames = iterations * frames_per_iteration
    total_wall_nanos = sum(wall_samples)
    return {
        "frames": total_frames,
        "throughput_frames_per_second": round(total_frames * 1_000_000_000 / total_wall_nanos, 2),
        "batch_latency_p50_nanos": int(statistics.median(wall_samples)),
        "batch_latency_p99_nanos": _percentile(wall_samples, 0.99),
        "cpu_nanos_per_frame": round(cpu_nanos / total_frames, 2),
        "python_tracemalloc_peak_bytes": peak_bytes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--iterations", type=int, default=2_000)
    args = parser.parse_args()
    if args.batch_size <= 0 or args.iterations <= 0:
        raise ValueError("batch size and iterations must be positive")

    native = import_module("kairospy._native_market_contract")
    payloads = [_fixture()] * args.batch_size
    for _ in range(100):
        native.decode_events(payloads)

    report = {
        "batch_size": args.batch_size,
        "iterations": args.iterations,
        "single_call_loop": _measure(
            lambda: [native.decode_event(payload) for payload in payloads],
            iterations=args.iterations,
            frames_per_iteration=args.batch_size,
        ),
        "native_batch": _measure(
            lambda: native.decode_events(payloads),
            iterations=args.iterations,
            frames_per_iteration=args.batch_size,
        ),
        "native_allocation_note": (
            "Python tracemalloc excludes Rust allocator activity; Criterion and process RSS "
            "must be used when native allocation attribution is required."
        ),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
