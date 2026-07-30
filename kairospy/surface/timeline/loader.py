from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable, Mapping


ARTIFACT_FILES = (
    "timeline.jsonl",
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
        artifact_records = {
            "timelineRecords": _read_jsonl(root / "timeline.jsonl"),
            "decisionTrace": _read_jsonl(root / "decision_trace.jsonl"),
            "riskSnapshots": _read_jsonl(root / "risk_snapshots.jsonl"),
            "equity": _read_jsonl(root / "equity.jsonl"),
            "fills": _read_jsonl(root / "fills.jsonl"),
            "intents": _read_jsonl(root / "intent_states.jsonl"),
            "trades": _read_jsonl(root / "trades.jsonl"),
        }
        records = _records_from_view_snapshots(artifact_records)
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
                "markets": _market_series(records["timelineRecords"]),
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
                "timeline_count": _count_lines(path / "timeline.jsonl"),
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
    return _unique_rows(rows)


def _records_from_view_snapshots(records: Mapping[str, list[dict[str, object]]]) -> dict[str, list[dict[str, object]]]:
    timeline_records = records.get("timelineRecords", [])
    if not any(_sampled_views(row) for row in timeline_records):
        return {key: list(value) for key, value in records.items()}
    derived = {
        "timelineRecords": list(timeline_records),
        "decisionTrace": _decision_trace_from_views(timeline_records),
        "riskSnapshots": _risk_snapshots_from_views(timeline_records),
        "equity": _equity_from_views(timeline_records),
        "fills": list(records.get("fills", [])),
        "intents": _intents_from_views(timeline_records),
        "trades": _market_records_from_views(timeline_records),
    }
    return {
        key: rows if rows else list(records.get(key, []))
        for key, rows in derived.items()
    }


def _sampled_views(row: Mapping[str, object]) -> Mapping[str, object]:
    views = row.get("views")
    return views if isinstance(views, Mapping) else {}


def _view_payload(row: Mapping[str, object], key: str) -> Mapping[str, object]:
    view = _sampled_views(row).get(key)
    if not isinstance(view, Mapping):
        return {}
    payload = view.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def _view_payloads_with_prefix(row: Mapping[str, object], prefix: str) -> list[tuple[str, Mapping[str, object]]]:
    payloads: list[tuple[str, Mapping[str, object]]] = []
    for key, view in _sampled_views(row).items():
        if not isinstance(key, str) or not key.startswith(prefix) or not isinstance(view, Mapping):
            continue
        payload = view.get("payload")
        if isinstance(payload, Mapping):
            payloads.append((key, payload))
    return payloads


def _decision_trace_from_views(timeline_records: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in timeline_records:
        payload = _view_payload(record, "strategy.decision_trace")
        records = payload.get("records")
        if isinstance(records, list):
            rows.extend(dict(item) for item in records if isinstance(item, Mapping))
    return _unique_rows(rows)


def _risk_snapshots_from_views(timeline_records: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in timeline_records:
        payload = _view_payload(record, "account.risk_snapshots")
        snapshots = payload.get("snapshots")
        if isinstance(snapshots, list):
            rows.extend(dict(item) for item in snapshots if isinstance(item, Mapping))
    return _unique_rows(rows)


def _equity_from_views(timeline_records: list[dict[str, object]]) -> list[dict[str, object]]:
    curve_rows: list[dict[str, object]] = []
    current_rows: list[dict[str, object]] = []
    for record in timeline_records:
        record_time = _record_time(record)
        curve = _view_payload(record, "account.equity_curve")
        points = curve.get("points")
        if isinstance(points, list):
            curve_rows.extend(dict(item) for item in points if isinstance(item, Mapping))
        for _key, account in _view_payloads_with_prefix(record, "account.current."):
            time = _first_string(account.get("last_event_time"), record_time)
            if time is None:
                continue
            current_rows.append(
                {
                    "time": time,
                    "equity": account.get("equity"),
                    "cash": account.get("cash"),
                    "net_profit": account.get("net_profit"),
                    "total_return": account.get("total_return"),
                    "positions": account.get("positions", []),
                }
            )
    return _unique_rows(curve_rows or current_rows)


def _intents_from_views(timeline_records: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in timeline_records:
        record_time = _record_time(record)
        payload = _view_payload(record, "intent.journal")
        states = payload.get("states")
        if not isinstance(states, list):
            continue
        for item in states:
            if isinstance(item, Mapping):
                row = dict(item)
                row.setdefault("updated_at", record_time)
                rows.append(row)
    return _unique_rows(rows)


def _market_records_from_views(timeline_records: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in timeline_records:
        for key, attr, source in (
            ("market.bars", "bars", "ohlcv"),
            ("market.trades", "trades", "trade"),
            ("market.quotes", "quotes", "quote"),
            ("market.rates", "rates", "rate"),
        ):
            payload = _view_payload(record, key)
            values = payload.get(attr)
            if not isinstance(values, list):
                continue
            for item in values:
                if isinstance(item, Mapping):
                    rows.append({"source": source, **dict(item)})
    return _unique_rows(rows)


def _unique_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    unique: dict[str, dict[str, object]] = {}
    for row in rows:
        unique[_stable_row_key(row)] = row
    return [unique[key] for key in sorted(unique, key=lambda item: (_record_time(unique[item]) or "", item))]


def _stable_row_key(row: Mapping[str, object]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)


def _timeline(records: Mapping[str, list[dict[str, object]]]) -> list[dict[str, object]]:
    by_time: dict[str, dict[str, object]] = {}
    timeline_records = records.get("timelineRecords", [])
    if timeline_records:
        for row in timeline_records:
            time = _record_time(row)
            if time is None:
                continue
            item = by_time.setdefault(time, {"time": time, "counts": {}})
            counts = item["counts"] if isinstance(item["counts"], dict) else {}
            counts["timelineRecords"] = int(counts.get("timelineRecords", 0)) + 1
            trigger = row.get("trigger")
            if isinstance(trigger, str) and trigger:
                trigger_key = f"trigger:{trigger}"
                counts[trigger_key] = int(counts.get(trigger_key, 0)) + 1
            item["counts"] = counts
        return [by_time[key] for key in sorted(by_time)]
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
    rows = equity if len(equity) >= len(risk) else risk
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


def _market_series(timeline_records: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in timeline_records:
        view_rows = _market_series_from_views(record)
        rows.extend(view_rows)
        if view_rows:
            continue
        record_time = _record_time(record)
        state = record.get("state")
        if not isinstance(state, Mapping):
            continue
        market = state.get("market")
        if not isinstance(market, Mapping):
            continue
        for key, item in market.items():
            if not isinstance(item, Mapping):
                continue
            time = _first_string(item.get("time"), record_time)
            price = item.get("price")
            if time is None or price is None:
                continue
            market_key = str(key)
            rows.append(
                {
                    "time": time,
                    "key": f"market:{market_key}",
                    "label": _market_label(item, market_key),
                    "kind": "price",
                    "marketId": item.get("market_id"),
                    "instrumentId": item.get("instrument_id"),
                    "value": price,
                    "close": price,
                }
            )
    return _unique_rows(rows)


def _market_series_from_views(record: Mapping[str, object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key, attr, kind, price_key in (
        ("market.bars", "bars", "ohlcv", "close"),
        ("market.trades", "trades", "trade", "price"),
        ("market.quotes", "quotes", "price", None),
        ("market.rates", "rates", "rate", "mark_price"),
    ):
        payload = _view_payload(record, key)
        values = payload.get(attr)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, Mapping):
                continue
            time = _first_string(item.get("time"), item.get("observed_at"), _record_time(record))
            if time is None:
                continue
            value = _market_series_value(item, price_key)
            if value is None:
                continue
            market_key = _market_label(item, str(item.get("market_key") or item.get("market_id") or item.get("instrument_id") or item.get("rate_id") or key))
            row = {
                "time": time,
                "key": f"{kind}:{market_key}",
                "label": market_key,
                "kind": kind,
                "marketId": item.get("market_id"),
                "instrumentId": item.get("instrument_id"),
                "value": value,
            }
            if kind == "ohlcv":
                row.update({
                    "open": item.get("open"),
                    "high": item.get("high"),
                    "low": item.get("low"),
                    "close": item.get("close"),
                    "volume": item.get("volume"),
                })
            elif kind == "rate":
                row.update({"rate": item.get("rate"), "close": value})
            else:
                row["close"] = value
            rows.append(row)
    return rows


def _market_series_value(item: Mapping[str, object], price_key: str | None) -> object:
    if price_key is not None:
        return item.get(price_key)
    bid = item.get("bid")
    ask = item.get("ask")
    if bid is not None and ask is not None:
        try:
            return str((float(str(bid)) + float(str(ask))) / 2)
        except ValueError:
            return bid
    return bid if bid is not None else ask


def _market_label(item: Mapping[str, object], fallback: str) -> str:
    for key in ("market_key", "market_id", "instrument_id"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return fallback


def _time_range(timeline: list[dict[str, object]]) -> dict[str, object]:
    if not timeline:
        return {"start": None, "end": None}
    return {"start": timeline[0]["time"], "end": timeline[-1]["time"]}


def _row_count(records: Mapping[str, list[dict[str, object]]], file_name: str) -> int:
    mapping = {
        "timeline.jsonl": "timelineRecords",
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
