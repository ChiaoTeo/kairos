from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from kairospy.application.support.query.projections.catalog import ProjectionSpec, LaunchProjectionCatalog
from kairospy.application.support.query.projections.protocol import ProjectionReader

CATALOG = LaunchProjectionCatalog()
HISTORY_PROJECTIONS = CATALOG.history()
ARTIFACT_FILES = tuple(spec.resource for spec in HISTORY_PROJECTIONS)
RECORD_KEYS_BY_FILE = {spec.resource: spec.record_key for spec in HISTORY_PROJECTIONS if spec.record_key is not None}


@dataclass(frozen=True, slots=True)
class LaunchProjectionService:
    reader: ProjectionReader
    catalog: LaunchProjectionCatalog = CATALOG

    @property
    def instance_path(self) -> Path:
        return self.reader.root

    def list_datasets(self) -> tuple[ProjectionSpec, ...]:
        return self.catalog.list()

    def load_run_view(self) -> dict[str, object]:
        root = self.reader.root.expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"launch instance directory was not found: {root}")
        summary = self.reader.read_json("summary.json")
        metrics = self.reader.read_json("metrics.json")
        config = self.reader.read_json("config.normalized.json")
        state = self.reader.read_json("state.json")
        artifact_records = _history_records(self.reader, self.catalog)
        records = _records_from_view_snapshots(artifact_records)
        timeline = _timeline(records)
        files = {
            name: {"path": str(root / name), "exists": self.reader.exists(name), "rows": _row_count(records, name)}
            for name in ARTIFACT_FILES
        }
        return {
            "instance": {
                "path": str(root),
                "launchId": _first_string(state.get("launch_id"), summary.get("launch_id"), _launch_id_from_path(root)),
                "mode": _first_string(state.get("mode"), summary.get("mode"), _mode_from_path(root)),
                "launchInstanceId": _first_string(state.get("launch_instance_id"), root.name),
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
                "equity": _equity_series(records["equity"]),
                "markets": _market_series(records["timelineRecords"]),
            },
            "records": records,
            "timeline": timeline,
        }

    def load(self, name: str) -> object:
        spec = self.catalog.require(name)
        if spec.kind in {"snapshot", "current"}:
            return self.reader.read_json(spec.resource)
        if spec.record_key is None:
            return {}
        records = self.load_run_view()["records"]
        return records[spec.record_key] if isinstance(records, Mapping) else []


def _history_records(reader: ProjectionReader, catalog: LaunchProjectionCatalog) -> dict[str, list[dict[str, object]]]:
    return {
        spec.record_key: reader.read_jsonl(spec.resource)
        for spec in catalog.history()
        if spec.record_key is not None
    }


def _records_from_view_snapshots(records: Mapping[str, list[dict[str, object]]]) -> dict[str, list[dict[str, object]]]:
    timeline_records = records.get("timelineRecords", [])
    if not any(_sampled_views(row) for row in timeline_records):
        return {key: list(value) for key, value in records.items()}
    derived = {
        "timelineRecords": list(timeline_records),
        "equity": _equity_from_views(timeline_records),
        "fills": _fills_from_views(timeline_records),
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
                    "selected_balance": account.get("selected_balance"),
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
        payload = _view_payload(record, "system.intents") or _view_payload(record, "intent.journal")
        states = payload.get("states")
        if not isinstance(states, list):
            continue
        for item in states:
            if isinstance(item, Mapping):
                row = dict(item)
                row.setdefault("updated_at", record_time)
                rows.append(row)
    return _unique_rows(rows)


def _fills_from_views(timeline_records: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in timeline_records:
        payload = _view_payload(record, "execution.fills")
        fills = payload.get("fills")
        if not isinstance(fills, list):
            continue
        for item in fills:
            if not isinstance(item, Mapping):
                continue
            row = dict(item)
            row.setdefault("time", row.get("occurred_at") or _record_time(record))
            rows.append(row)
    return _unique_rows(rows)


def _market_records_from_views(timeline_records: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in timeline_records:
        for key, payload in _view_payloads_with_prefix(record, "market.window."):
            source = _market_window_source(key)
            if source is None:
                continue
            for item in _market_window_items(payload):
                rows.append({"source": source, **dict(item)})
    return _unique_rows(rows)


def _unique_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    unique: dict[str, dict[str, object]] = {}
    for row in rows:
        unique[_stable_row_key(row)] = row
    return [unique[key] for key in sorted(unique, key=lambda item: (_record_time(unique[item]) or "", item))]


def _stable_row_key(row: Mapping[str, object]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)


def _market_window_items(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    items = payload.get("items")
    if not isinstance(items, list):
        return ()
    return tuple(item for item in items if isinstance(item, Mapping))


def _market_window_source(key: str) -> str | None:
    if ".bars." in key:
        return "ohlcv"
    if key.endswith(".trades"):
        return "trade"
    if key.endswith(".quotes"):
        return "quote"
    if key.endswith(".option_greeks"):
        return "option_greeks"
    if ".rates." in key:
        return "rate"
    return None


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


def _equity_series(equity: list[dict[str, object]]) -> list[dict[str, object]]:
    rows = equity
    return [
        {
            "time": row.get("time"),
            "equity": row.get("equity"),
            "selected_balance": row.get("selected_balance"),
        }
        for row in rows
        if isinstance(row.get("time"), str)
    ]


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
    for key, payload in _view_payloads_with_prefix(record, "market.window."):
        kind = _market_window_source(key)
        if kind is None:
            continue
        price_key = _market_window_price_key(kind)
        for item in _market_window_items(payload):
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


def _market_window_price_key(kind: str) -> str | None:
    if kind == "ohlcv":
        return "close"
    if kind == "trade":
        return "price"
    if kind == "rate":
        return "mark_price"
    return None


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
    key = RECORD_KEYS_BY_FILE[file_name]
    return len(records.get(key, []))


def _first_string(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return None


def _context_value(state: Mapping[str, object], key: str) -> object:
    context = state.get("context")
    return context.get(key) if isinstance(context, Mapping) else None


__all__ = ["LaunchProjectionService"]
