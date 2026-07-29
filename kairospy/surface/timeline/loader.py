from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable, Mapping


ARTIFACT_FILES = (
    "decision_trace.jsonl",
    "risk_snapshots.jsonl",
    "equity.jsonl",
    "fills.jsonl",
    "intent_states.jsonl",
    "trades.jsonl",
)


@dataclass(frozen=True, slots=True)
class TimelineDataLoader:
    instance_path: Path

    def load(self) -> dict[str, object]:
        root = self.instance_path.expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"run instance directory was not found: {root}")
        summary = _read_json(root / "summary.json")
        metrics = _read_json(root / "metrics.json")
        config = _read_json(root / "config.normalized.json")
        state = _read_json(root / "state.json")
        records = {
            "decisionTrace": _read_jsonl(root / "decision_trace.jsonl"),
            "riskSnapshots": _read_jsonl(root / "risk_snapshots.jsonl"),
            "equity": _read_jsonl(root / "equity.jsonl"),
            "fills": _read_jsonl(root / "fills.jsonl"),
            "intents": _read_jsonl(root / "intent_states.jsonl"),
            "trades": _read_jsonl(root / "trades.jsonl"),
        }
        timeline = _timeline(records)
        files = {
            name: {"path": str(root / name), "exists": (root / name).exists(), "rows": _row_count(records, name)}
            for name in ARTIFACT_FILES
        }
        return {
            "instance": {
                "path": str(root),
                "runId": _first_string(state.get("run_id"), summary.get("run_id"), _run_id_from_path(root)),
                "mode": _first_string(state.get("mode"), summary.get("mode"), _mode_from_path(root)),
                "runInstanceId": _first_string(state.get("run_instance_id"), root.name),
                "strategyId": _first_string(summary.get("strategy_id"), _context_value(state, "strategy")),
                "timeRange": _time_range(timeline),
                "files": files,
                "counts": {key: len(value) for key, value in records.items()},
            },
            "summary": summary,
            "metrics": metrics,
            "config": config,
            "state": state,
            "series": {
                "equity": _equity_series(records["equity"], records["riskSnapshots"]),
                "risk": _risk_series(records["riskSnapshots"]),
                "fundingRates": _funding_series(records["riskSnapshots"]),
            },
            "records": records,
            "timeline": timeline,
        }


def find_latest_instance(root: Path, *, mode: str | None = None, run_id: str | None = None) -> Path:
    base = root.expanduser().resolve()
    candidates: list[Path] = []
    pattern = "*/*/instances/*" if mode is None and run_id is None else None
    if pattern is not None:
        candidates = [path for path in base.glob(pattern) if path.is_dir()]
    elif mode is not None and run_id is not None:
        candidates = [path for path in (base / mode / run_id / "instances").glob("*") if path.is_dir()]
    elif mode is not None:
        candidates = [path for path in (base / mode).glob("*/instances/*") if path.is_dir()]
    else:
        candidates = [path for path in base.glob(f"*/{run_id}/instances/*") if path.is_dir()]
    candidates = [path for path in candidates if _has_timeline_file(path)]
    if not candidates:
        hint = f" under {base}"
        if mode is not None:
            hint += f" mode={mode}"
        if run_id is not None:
            hint += f" run_id={run_id}"
        raise ValueError(f"no timeline-capable run instances found{hint}")
    return max(candidates, key=_mtime)


def list_instances(root: Path, *, mode: str | None = None, run_id: str | None = None) -> list[dict[str, object]]:
    base = root.expanduser().resolve()
    if not base.exists():
        return []
    if mode is None and run_id is None:
        candidates = [path for path in base.glob("*/*/instances/*") if path.is_dir()]
    elif mode is not None and run_id is not None:
        candidates = [path for path in (base / mode / run_id / "instances").glob("*") if path.is_dir()]
    elif mode is not None:
        candidates = [path for path in (base / mode).glob("*/instances/*") if path.is_dir()]
    else:
        candidates = [path for path in base.glob(f"*/{run_id}/instances/*") if path.is_dir()]
    rows = []
    for path in sorted(candidates, key=_mtime, reverse=True):
        summary = _read_json(path / "summary.json")
        state = _read_json(path / "state.json")
        rows.append(
            {
                "mode": _first_string(state.get("mode"), summary.get("mode"), _mode_from_path(path)),
                "run_id": _first_string(state.get("run_id"), summary.get("run_id"), _run_id_from_path(path)),
                "run_instance_id": _first_string(state.get("run_instance_id"), path.name),
                "strategy_id": _first_string(summary.get("strategy_id"), _context_value(state, "strategy")),
                "updated_at": _mtime(path),
                "directory": str(path),
                "decision_trace_count": _count_lines(path / "decision_trace.jsonl"),
                "risk_snapshot_count": _count_lines(path / "risk_snapshots.jsonl"),
                "equity_count": _count_lines(path / "equity.jsonl"),
            }
        )
    return rows


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON file: {path}") from error
    return dict(value) if isinstance(value, Mapping) else {}


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSONL file: {path}:{line_number}") from error
            if isinstance(value, Mapping):
                rows.append(dict(value))
    return rows


def _timeline(records: Mapping[str, list[dict[str, object]]]) -> list[dict[str, object]]:
    by_time: dict[str, dict[str, object]] = {}
    for key, rows in records.items():
        for row in rows:
            time = _record_time(row)
            if time is None:
                continue
            item = by_time.setdefault(time, {"time": time, "counts": {}})
            counts = item["counts"] if isinstance(item["counts"], dict) else {}
            counts[key] = int(counts.get(key, 0)) + 1
            item["counts"] = counts
    return [by_time[key] for key in sorted(by_time)]


def _record_time(row: Mapping[str, object]) -> str | None:
    for key in ("time", "occurred_at", "updated_at"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value
    intent = row.get("intent")
    if isinstance(intent, Mapping):
        value = intent.get("created_at")
        if isinstance(value, str) and value.strip():
            return value
    return None


def _equity_series(equity: list[dict[str, object]], risk: list[dict[str, object]]) -> list[dict[str, object]]:
    rows = equity if equity else risk
    return [
        {
            "time": row.get("time"),
            "equity": row.get("equity"),
            "cash": row.get("cash"),
        }
        for row in rows
        if isinstance(row.get("time"), str)
    ]


def _risk_series(risk: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "time": row.get("time"),
            "equity": row.get("equity"),
            "cash": row.get("cash"),
            "grossNotional": row.get("gross_notional"),
            "netNotional": row.get("net_notional"),
            "positionCount": len(row.get("positions", [])) if isinstance(row.get("positions"), list) else 0,
        }
        for row in risk
        if isinstance(row.get("time"), str)
    ]


def _funding_series(risk: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for snapshot in risk:
        rates = snapshot.get("funding_rates")
        if not isinstance(rates, list):
            continue
        for rate in rates:
            if isinstance(rate, Mapping):
                rows.append({"snapshotTime": snapshot.get("time"), **dict(rate)})
    return rows


def _time_range(timeline: list[dict[str, object]]) -> dict[str, object]:
    if not timeline:
        return {"start": None, "end": None}
    return {"start": timeline[0]["time"], "end": timeline[-1]["time"]}


def _row_count(records: Mapping[str, list[dict[str, object]]], file_name: str) -> int:
    mapping = {
        "decision_trace.jsonl": "decisionTrace",
        "risk_snapshots.jsonl": "riskSnapshots",
        "equity.jsonl": "equity",
        "fills.jsonl": "fills",
        "intent_states.jsonl": "intents",
        "trades.jsonl": "trades",
    }
    return len(records.get(mapping[file_name], []))


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _has_timeline_file(path: Path) -> bool:
    return any((path / name).exists() for name in ARTIFACT_FILES)


def _mtime(path: Path) -> float:
    candidates = [path / "summary.json", path / "state.json", path]
    existing = [item for item in candidates if item.exists()]
    return max(item.stat().st_mtime for item in existing)


def _mode_from_path(path: Path) -> str | None:
    parts = path.parts
    if "runs" in parts:
        index = parts.index("runs")
        if len(parts) > index + 1:
            return parts[index + 1]
    return None


def _run_id_from_path(path: Path) -> str | None:
    parts = path.parts
    if "runs" in parts:
        index = parts.index("runs")
        if len(parts) > index + 2:
            return parts[index + 2]
    return None


def _first_string(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return None


def _context_value(state: Mapping[str, object], key: str) -> object:
    context = state.get("context")
    return context.get(key) if isinstance(context, Mapping) else None

