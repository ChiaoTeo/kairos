from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
import math
from pathlib import Path

from trading.storage.data_lake import write_json

from .catalog import DataCatalog
from .client import ResearchDataClient
from .models import DatasetRelease, QualityLevel


@dataclass(frozen=True, slots=True)
class QualityCheck:
    name: str
    passed: bool
    value: object
    requirement: str


@dataclass(frozen=True, slots=True)
class QualityAssessment:
    release_id: str
    profile: str
    level: QualityLevel
    passed: bool
    checks: tuple[QualityCheck, ...]
    report_hash: str


class DatasetQualityService:
    def __init__(self, root: str | Path = "data") -> None:
        self.root = Path(root)
        self.catalog = DataCatalog(self.root)
        self.client = ResearchDataClient(self.root)

    def assess(self, dataset: str) -> QualityAssessment:
        release = self.catalog.release(dataset)
        profile = self._profile(release, [])
        streaming = profile in {"trade", "market_event"} and self._has_parquet(release)
        rows = [] if profile == "market_slice" or streaming else self.client.load_rows(release.release_id)
        profiles = {
            "ohlcv": self._ohlcv,
            "quote": self._quote,
            "trade": self._trade,
            "market_event": self._market_event,
            "option_snapshot": self._option_snapshot,
            "feature": self._feature,
            "reference": self._reference,
            "generic": self._integrity,
            "integrity": self._integrity,
        }
        try:
            if profile == "market_slice":
                checks = self._market_slice(release)
            elif streaming:
                checks = self._streaming_trade(release) if profile == "trade" else self._streaming_market_event(release)
            else:
                checks = profiles[profile](rows, release)
        except KeyError as error:
            raise ValueError(f"unsupported dataset quality profile: {profile}") from error
        passed = all(item.passed for item in checks)
        if not passed:
            level = QualityLevel.ARCHIVED
        elif profile == "market_slice":
            level = QualityLevel.BACKTEST
        elif profile == "ohlcv" and len(rows) >= 365:
            level = QualityLevel.BACKTEST
        else:
            level = QualityLevel.RESEARCH
        primitive = {
            "quality_schema_version": 2,
            "release_id": release.release_id,
            "logical_key": str(release.product_key),
            "profile": profile,
            "level": level.value,
            "passed": passed,
            "checks": [
                {"name": item.name, "passed": item.passed, "value": item.value, "requirement": item.requirement}
                for item in checks
            ],
        }
        report_hash = sha256(json.dumps(
            primitive, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
        ).encode()).hexdigest()
        primitive["report_hash"] = report_hash
        write_json(self.root / release.relative_path / "quality.json", primitive)
        self.catalog.record_quality_assessment(
            release.release_id, level, report_hash=report_hash,
            actor="quality-engine", reason=f"{profile} profile assessment",
        )
        return QualityAssessment(release.release_id, profile, level, passed, checks, report_hash)

    def _has_parquet(self, release: DatasetRelease) -> bool:
        return bool(self._parquet_paths(release))

    def _parquet_paths(self, release: DatasetRelease) -> list[Path]:
        directory = self.root / release.relative_path
        manifest = _lineage(directory / "manifest.json")
        declared = manifest.get("files", [])
        paths = sorted(
            directory / str(item["path"])
            for item in declared
            if isinstance(item, dict) and str(item.get("path", "")).endswith(".parquet")
        )
        paths = [path for path in paths if path.exists()]
        if paths:
            return paths
        nested_release = "/release=" not in f"/{release.relative_path}"
        return sorted(
            path for path in directory.glob("**/*.parquet")
            if not nested_release or "/release=" not in path.relative_to(directory).as_posix()
        )

    def _streaming_view(self, release: DatasetRelease):
        try:
            import duckdb
        except ImportError as error:
            raise RuntimeError("streaming quality profiles require DuckDB") from error
        paths = self._parquet_paths(release)
        if not paths:
            raise ValueError(f"streaming quality release has no Parquet files: {release.release_id}")
        escaped = [str(path).replace("'", "''") for path in paths]
        source = "[" + ",".join(f"'{path}'" for path in escaped) + "]"
        connection = duckdb.connect()
        connection.execute(
            f"CREATE TEMP VIEW quality_rows AS SELECT * FROM read_parquet({source}, "
            "union_by_name=true, filename=true, file_row_number=true)"
        )
        columns = {str(row[0]) for row in connection.execute("DESCRIBE quality_rows").fetchall()}
        return connection, columns

    def _streaming_integrity(self, release: DatasetRelease, row_count: int) -> list[QualityCheck]:
        base = list(self._integrity([], release))
        base[0] = QualityCheck("non_empty", row_count > 0, row_count, "> 0 rows")
        return base

    def _streaming_trade(self, release: DatasetRelease) -> tuple[QualityCheck, ...]:
        connection, fields = self._streaming_view(release)
        try:
            physical_order = _physical_order(fields)
            required = {"instrument_id", "trade_id", "event_time", "available_time"}
            price = _first_field(fields, "price", "price_btc", "trade_price")
            quantity = _first_field(fields, "quantity", "amount", "amount_btc", "size")
            required_ok = required <= fields and price is not None and quantity is not None
            count = int(connection.execute("SELECT count(*) FROM quality_rows").fetchone()[0])
            base = self._streaming_integrity(release, count)
            missing = sorted(required - fields)
            if price is None:
                missing.append("price|price_btc|trade_price")
            if quantity is None:
                missing.append("quantity|amount|amount_btc|size")
            base.append(QualityCheck("required_fields", required_ok, missing, "trade identity, price, quantity and point-in-time fields"))
            if not required_ok:
                return tuple(base)
            duplicates, invalid_values, invalid_time = connection.execute(f"""
                SELECT
                    count(*) - count(DISTINCT (instrument_id, trade_id)),
                    count(*) FILTER (WHERE {price} IS NULL OR {quantity} IS NULL OR {price} <= 0 OR {quantity} <= 0),
                    count(*) FILTER (WHERE event_time IS NULL OR available_time IS NULL OR available_time < event_time)
                FROM quality_rows
            """).fetchone()
            invalid_direction = 0
            direction = _first_field(fields, "direction", "side")
            if direction is not None:
                invalid_direction = int(connection.execute(
                    f"SELECT count(*) FROM quality_rows WHERE {direction} IS NOT NULL "
                    f"AND lower(cast({direction} AS VARCHAR)) NOT IN ('buy','sell')",
                ).fetchone()[0])
            unordered = int(connection.execute(f"""
                SELECT count(*) FROM (
                    SELECT event_time, trade_id,
                           lag(event_time) OVER physical AS previous_time,
                           lag(trade_id) OVER physical AS previous_trade
                    FROM quality_rows
                    WINDOW physical AS (ORDER BY {physical_order})
                )
                WHERE previous_time IS NOT NULL
                  AND (event_time, cast(trade_id AS VARCHAR))
                      < (previous_time, cast(previous_trade AS VARCHAR))
            """).fetchone()[0])
            base.extend((
                QualityCheck("unique_trade_id", int(duplicates) == 0, int(duplicates), "0 duplicate instrument/trade IDs"),
                QualityCheck("positive_trade_values", int(invalid_values) == 0, int(invalid_values), "price and quantity > 0"),
                QualityCheck("trade_point_in_time", int(invalid_time) == 0, int(invalid_time), "event_time <= available_time"),
                QualityCheck("valid_trade_direction", invalid_direction == 0, invalid_direction, "direction is buy or sell"),
                QualityCheck("deterministic_order", unordered == 0, unordered, "physical rows ordered by event time/trade ID"),
                QualityCheck("streaming_execution", True, count, "quality computed without materializing rows"),
            ))
            return tuple(base)
        finally:
            connection.close()

    def _streaming_market_event(self, release: DatasetRelease) -> tuple[QualityCheck, ...]:
        connection, fields = self._streaming_view(release)
        try:
            physical_order = _physical_order(fields)
            required = {"source", "source_namespace", "source_instrument_id", "record_type", "event_time", "available_time"}
            required_ok = required <= fields
            count = int(connection.execute("SELECT count(*) FROM quality_rows").fetchone()[0])
            base = self._streaming_integrity(release, count)
            base.append(_required_check(required, fields, "canonical source identity, record type and visibility time"))
            if not required_ok:
                return tuple(base)
            source_order = "source_order" if "source_order" in fields else "file_row_number"
            duplicates, missing_identity, invalid_time = connection.execute(f"""
                SELECT
                    count(*) - count(DISTINCT (
                        source, source_namespace, source_instrument_id, record_type, event_time, {source_order}
                    )),
                    count(*) FILTER (WHERE
                        source IS NULL OR trim(cast(source AS VARCHAR)) = '' OR
                        source_namespace IS NULL OR trim(cast(source_namespace AS VARCHAR)) = '' OR
                        source_instrument_id IS NULL OR trim(cast(source_instrument_id AS VARCHAR)) = '' OR
                        record_type IS NULL OR trim(cast(record_type AS VARCHAR)) = ''
                    ),
                    count(*) FILTER (WHERE event_time IS NULL OR available_time IS NULL OR available_time < event_time)
                FROM quality_rows
            """).fetchone()
            unordered = int(connection.execute(f"""
                SELECT count(*) FROM (
                    SELECT available_time, event_time, {source_order} AS source_order_value,
                           lag(available_time) OVER physical AS previous_available,
                           lag(event_time) OVER physical AS previous_event,
                           lag({source_order}) OVER physical AS previous_order
                    FROM quality_rows
                    WINDOW physical AS (ORDER BY {physical_order})
                )
                WHERE previous_available IS NOT NULL
                  AND (available_time, event_time, source_order_value)
                      < (previous_available, previous_event, previous_order)
            """).fetchone()[0])
            base.extend((
                QualityCheck("unique_source_event", int(duplicates) == 0, int(duplicates), "0 duplicate canonical source events"),
                QualityCheck("complete_source_identity", int(missing_identity) == 0, int(missing_identity), "source identity fields are non-empty"),
                QualityCheck("event_point_in_time", int(invalid_time) == 0, int(invalid_time), "event_time <= available_time"),
                QualityCheck("deterministic_order", unordered == 0, unordered, "physical rows ordered by available/event/source order"),
                QualityCheck("streaming_execution", True, count, "quality computed without materializing rows"),
            ))
            return tuple(base)
        finally:
            connection.close()

    def _profile(self, release: DatasetRelease, rows: list[dict[str, object]]) -> str:
        try:
            return self.catalog.product_spec(release.product_key).quality_profile
        except KeyError:
            pass
        fields = set(rows[0]) if rows else set()
        if "ohlcv" in release.schema_id.lower() or {"open", "high", "low", "close", "volume"} <= fields:
            return "ohlcv"
        return "integrity"

    def _integrity(self, rows: list[dict[str, object]], release: DatasetRelease) -> tuple[QualityCheck, ...]:
        manifest_path = self.root / release.relative_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        manifest_hash = manifest.get("dataset_sha256") or manifest.get("content_sha256")
        return (
            QualityCheck("non_empty", bool(rows), len(rows), "> 0 rows"),
            QualityCheck("content_hash", bool(release.content_hash), release.content_hash, "frozen content hash"),
            QualityCheck(
                "manifest_content_hash", bool(manifest_hash) and manifest_hash == release.content_hash,
                {"manifest": manifest_hash, "release": release.content_hash}, "Manifest hash equals Release hash",
            ),
        )

    def _ohlcv(self, rows: list[dict[str, object]], release: DatasetRelease) -> tuple[QualityCheck, ...]:
        base = list(self._integrity(rows, release))
        required = {"period_start", "period_end", "event_time", "available_time", "open", "high", "low", "close", "volume"}
        fields = set(rows[0]) if rows else set()
        base.append(QualityCheck("required_fields", required <= fields, sorted(required - fields), "all OHLCV/time fields present"))
        if not rows or not required <= fields:
            return tuple(base)
        identities = [(str(row.get("instrument_id", "")), str(row["period_start"])) for row in rows]
        duplicates = len(identities) - len(set(identities))
        invalid_ohlc = invalid_volume = invalid_time = unordered = 0
        previous = None
        for row in rows:
            open_, high, low, close = (Decimal(str(row[name])) for name in ("open", "high", "low", "close"))
            volume = Decimal(str(row["volume"]))
            if low > min(open_, close) or high < max(open_, close) or low > high or min(open_, high, low, close) <= 0:
                invalid_ohlc += 1
            if volume < 0:
                invalid_volume += 1
            start, end = _time(row["period_start"]), _time(row["period_end"])
            event, available = _time(row["event_time"]), _time(row["available_time"])
            if not (start < end and event >= end and available >= event):
                invalid_time += 1
            identity = (str(row.get("instrument_id", "")), start)
            if previous is not None and identity < previous:
                unordered += 1
            previous = identity
        coverage = self.client.coverage(release.release_id).get("coverage", {})
        coverage_body = coverage.get("coverage", {}) if isinstance(coverage, dict) else {}
        ratio = Decimal(str(coverage_body.get("coverage_ratio", 0)))
        missing = coverage.get("missing_ranges", []) if isinstance(coverage, dict) else []
        base.extend((
            QualityCheck("unique_primary_key", duplicates == 0, duplicates, "0 duplicates"),
            QualityCheck("valid_ohlc", invalid_ohlc == 0, invalid_ohlc, "positive prices and low <= open/close <= high"),
            QualityCheck("non_negative_volume", invalid_volume == 0, invalid_volume, "0 negative volumes"),
            QualityCheck("point_in_time_order", invalid_time == 0, invalid_time, "start < end <= event <= available"),
            QualityCheck("deterministic_order", unordered == 0, unordered, "rows ordered by instrument/time"),
            QualityCheck("coverage_ratio", ratio >= Decimal("0.99"), str(ratio), ">= 0.99"),
            QualityCheck("missing_ranges", not missing, len(missing), "0 missing ranges"),
            QualityCheck("backtest_history", len(rows) >= 365, len(rows), ">= 365 observations"),
        ))
        return tuple(base)

    def _quote(self, rows: list[dict[str, object]], release: DatasetRelease) -> tuple[QualityCheck, ...]:
        base = list(self._integrity(rows, release))
        required = {"instrument_id", "event_time", "available_time", "bid", "ask"}
        fields = set(rows[0]) if rows else set()
        base.append(_required_check(required, fields, "quote identity, time, bid and ask fields"))
        if not rows or not required <= fields:
            return tuple(base)
        duplicates, invalid_price, crossed, invalid_time, unordered = 0, 0, 0, 0, 0
        identities = []
        previous = None
        for row in rows:
            identity = (str(row["instrument_id"]), str(row["event_time"]))
            identities.append(identity)
            bid, ask = _decimal(row["bid"]), _decimal(row["ask"])
            if bid is None or ask is None or bid < 0 or ask <= 0:
                invalid_price += 1
            elif bid > ask:
                crossed += 1
            event, available = _safe_time(row["event_time"]), _safe_time(row["available_time"])
            if event is None or available is None or available < event:
                invalid_time += 1
            if previous is not None and identity < previous:
                unordered += 1
            previous = identity
        duplicates = len(identities) - len(set(identities))
        base.extend((
            QualityCheck("unique_quote_key", duplicates == 0, duplicates, "0 duplicate instrument/event pairs"),
            QualityCheck("valid_quote_prices", invalid_price == 0, invalid_price, "bid >= 0 and ask > 0"),
            QualityCheck("non_crossed_quotes", crossed == 0, crossed, "bid <= ask"),
            QualityCheck("quote_point_in_time", invalid_time == 0, invalid_time, "event_time <= available_time"),
            QualityCheck("deterministic_order", unordered == 0, unordered, "rows ordered by instrument/time"),
        ))
        return tuple(base)

    def _trade(self, rows: list[dict[str, object]], release: DatasetRelease) -> tuple[QualityCheck, ...]:
        base = list(self._integrity(rows, release))
        fields = set(rows[0]) if rows else set()
        required = {"instrument_id", "trade_id", "event_time", "available_time"}
        price_field = _first_field(fields, "price", "price_btc", "trade_price")
        quantity_field = _first_field(fields, "quantity", "amount", "amount_btc", "size")
        required_ok = required <= fields and price_field is not None and quantity_field is not None
        missing = sorted(required - fields)
        if price_field is None:
            missing.append("price|price_btc|trade_price")
        if quantity_field is None:
            missing.append("quantity|amount|amount_btc|size")
        base.append(QualityCheck("required_fields", required_ok, missing, "trade identity, price, quantity and point-in-time fields"))
        if not rows or not required_ok:
            return tuple(base)
        identities, invalid_values, invalid_time, invalid_direction, unordered = [], 0, 0, 0, 0
        previous = None
        for row in rows:
            identity = (str(row["instrument_id"]), str(row["trade_id"]))
            identities.append(identity)
            price, quantity = _decimal(row[price_field]), _decimal(row[quantity_field])
            if price is None or quantity is None or price <= 0 or quantity <= 0:
                invalid_values += 1
            event, available = _safe_time(row["event_time"]), _safe_time(row["available_time"])
            if event is None or available is None or available < event:
                invalid_time += 1
            direction = row.get("direction", row.get("side"))
            if direction is not None and str(direction).lower() not in {"buy", "sell"}:
                invalid_direction += 1
            order = (str(row["event_time"]), str(row["trade_id"]))
            if previous is not None and order < previous:
                unordered += 1
            previous = order
        duplicates = len(identities) - len(set(identities))
        base.extend((
            QualityCheck("unique_trade_id", duplicates == 0, duplicates, "0 duplicate instrument/trade IDs"),
            QualityCheck("positive_trade_values", invalid_values == 0, invalid_values, "price and quantity > 0"),
            QualityCheck("trade_point_in_time", invalid_time == 0, invalid_time, "event_time <= available_time"),
            QualityCheck("valid_trade_direction", invalid_direction == 0, invalid_direction, "direction is buy or sell"),
            QualityCheck("deterministic_order", unordered == 0, unordered, "rows ordered by event time/trade ID"),
        ))
        return tuple(base)

    def _market_event(self, rows: list[dict[str, object]], release: DatasetRelease) -> tuple[QualityCheck, ...]:
        base = list(self._integrity(rows, release))
        fields = set(rows[0]) if rows else set()
        required = {"source", "source_namespace", "source_instrument_id", "record_type", "event_time", "available_time"}
        base.append(_required_check(required, fields, "canonical source identity, record type and visibility time"))
        if not rows or not required <= fields:
            return tuple(base)
        identities, invalid_time, missing_identity, unordered = [], 0, 0, 0
        previous = None
        for index, row in enumerate(rows):
            source_order = row.get("source_order", row.get("sequence", index))
            identity = (
                str(row["source"]), str(row["source_namespace"]), str(row["source_instrument_id"]),
                str(row["record_type"]), str(row["event_time"]), str(source_order),
            )
            identities.append(identity)
            if any(not str(row[name]).strip() for name in ("source", "source_namespace", "source_instrument_id", "record_type")):
                missing_identity += 1
            event, available = _safe_time(row["event_time"]), _safe_time(row["available_time"])
            if event is None or available is None or available < event:
                invalid_time += 1
            order = (str(row["available_time"]), str(row["event_time"]), str(source_order))
            if previous is not None and order < previous:
                unordered += 1
            previous = order
        duplicates = len(identities) - len(set(identities))
        base.extend((
            QualityCheck("unique_source_event", duplicates == 0, duplicates, "0 duplicate canonical source events"),
            QualityCheck("complete_source_identity", missing_identity == 0, missing_identity, "source identity fields are non-empty"),
            QualityCheck("event_point_in_time", invalid_time == 0, invalid_time, "event_time <= available_time"),
            QualityCheck("deterministic_order", unordered == 0, unordered, "rows ordered by available/event/source order"),
        ))
        return tuple(base)

    def _option_snapshot(self, rows: list[dict[str, object]], release: DatasetRelease) -> tuple[QualityCheck, ...]:
        base = list(self._integrity(rows, release))
        fields = set(rows[0]) if rows else set()
        time_field = _first_field(fields, "available_time", "event_time", "period_start")
        bid_field = _first_field(fields, "bid", "best_bid_price", "bid_price_btc")
        ask_field = _first_field(fields, "ask", "best_ask_price", "ask_price_btc")
        required = {"instrument_id", "expiry", "strike"}
        required_ok = required <= fields and time_field is not None
        missing = sorted(required - fields)
        if time_field is None:
            missing.append("available_time|event_time|period_start")
        base.append(QualityCheck("required_fields", required_ok, missing, "option identity, expiry, strike and snapshot time"))
        if not rows or not required_ok:
            return tuple(base)
        identities, invalid_contract, crossed, invalid_iv, invalid_time = [], 0, 0, 0, 0
        for row in rows:
            timestamp = _safe_time(row[time_field])
            expiry = _safe_time(row["expiry"])
            strike = _decimal(row["strike"])
            identities.append((str(row["instrument_id"]), str(row[time_field])))
            if timestamp is None or expiry is None or expiry <= timestamp or strike is None or strike <= 0:
                invalid_contract += 1
            if "event_time" in row and "available_time" in row:
                event, available = _safe_time(row["event_time"]), _safe_time(row["available_time"])
                if event is None or available is None or available < event:
                    invalid_time += 1
            if bid_field and ask_field:
                bid, ask = _decimal(row.get(bid_field)), _decimal(row.get(ask_field))
                if bid is not None and ask is not None and bid > ask:
                    crossed += 1
            for name, value in row.items():
                if "iv" in name.lower() and value is not None:
                    parsed = _decimal(value)
                    if parsed is None or parsed < 0:
                        invalid_iv += 1
        duplicates = len(identities) - len(set(identities))
        base.extend((
            QualityCheck("unique_option_snapshot", duplicates == 0, duplicates, "0 duplicate instrument/snapshot pairs"),
            QualityCheck("valid_option_contract", invalid_contract == 0, invalid_contract, "expiry after snapshot and strike > 0"),
            QualityCheck("non_crossed_quotes", crossed == 0, crossed, "bid <= ask when both are present"),
            QualityCheck("non_negative_implied_volatility", invalid_iv == 0, invalid_iv, "all IV fields >= 0"),
            QualityCheck("snapshot_point_in_time", invalid_time == 0, invalid_time, "event_time <= available_time"),
        ))
        return tuple(base)

    def _feature(self, rows: list[dict[str, object]], release: DatasetRelease) -> tuple[QualityCheck, ...]:
        base = list(self._integrity(rows, release))
        fields = set(rows[0]) if rows else set()
        required = {"period_start", "period_end", "event_time", "available_time"}
        base.append(_required_check(required, fields, "feature window and visibility fields"))
        if not rows or not required <= fields:
            return tuple(base)
        identities, invalid_time, invalid_numeric, unordered = [], 0, 0, 0
        previous = None
        for row in rows:
            start, end = _safe_time(row["period_start"]), _safe_time(row["period_end"])
            event, available = _safe_time(row["event_time"]), _safe_time(row["available_time"])
            identity = str(row["period_start"])
            identities.append(identity)
            if None in {start, end, event, available} or not (start < end <= event <= available):
                invalid_time += 1
            for name, value in row.items():
                if name in required or value is None or isinstance(value, (str, bool, datetime)):
                    continue
                if isinstance(value, (int, float, Decimal)) and not _finite(value):
                    invalid_numeric += 1
            if previous is not None and identity < previous:
                unordered += 1
            previous = identity
        lineage = _lineage(self.root / release.relative_path / "lineage.json")
        inputs = lineage.get("inputs") or lineage.get("input_releases") or lineage.get("sources")
        frozen_inputs = isinstance(inputs, list) and bool(inputs) and all(
            isinstance(item, dict)
            and bool(item.get("release_id") or item.get("dataset_id"))
            and bool(item.get("content_hash") or item.get("dataset_sha256"))
            for item in inputs
        )
        base.extend((
            QualityCheck("unique_feature_time", len(identities) == len(set(identities)), len(identities) - len(set(identities)), "0 duplicate feature windows"),
            QualityCheck("no_future_data", invalid_time == 0, invalid_time, "period_start < period_end <= event_time <= available_time"),
            QualityCheck("finite_feature_values", invalid_numeric == 0, invalid_numeric, "no NaN or infinite numeric values"),
            QualityCheck("deterministic_order", unordered == 0, unordered, "rows ordered by feature period"),
            QualityCheck("frozen_feature_inputs", frozen_inputs, frozen_inputs, "lineage declares input IDs and content hashes"),
        ))
        return tuple(base)

    def _reference(self, rows: list[dict[str, object]], release: DatasetRelease) -> tuple[QualityCheck, ...]:
        base = list(self._integrity(rows, release))
        fields = set(rows[0]) if rows else set()
        required = {"instrument_id", "effective_from"}
        base.append(_required_check(required, fields, "reference identity and effective_from"))
        if not rows or not required <= fields:
            return tuple(base)
        identities, invalid_ranges = [], 0
        for row in rows:
            start = _safe_time(row["effective_from"])
            end = _safe_time(row["effective_to"]) if row.get("effective_to") is not None else None
            identities.append((str(row["instrument_id"]), str(row["effective_from"])))
            if start is None or end is not None and end <= start:
                invalid_ranges += 1
        duplicates = len(identities) - len(set(identities))
        base.extend((
            QualityCheck("unique_reference_version", duplicates == 0, duplicates, "0 duplicate instrument/effective_from pairs"),
            QualityCheck("valid_effective_range", invalid_ranges == 0, invalid_ranges, "effective_to is absent or after effective_from"),
        ))
        return tuple(base)

    def _market_slice(self, release: DatasetRelease) -> tuple[QualityCheck, ...]:
        raw_rows = self.client.load_rows(release.release_id)
        base = list(self._integrity(raw_rows, release))
        dataset = self.client.replay_slices(release.release_id).dataset
        manifest = dataset.manifest
        ordered = tuple(sorted(dataset.slices, key=lambda item: (item.timestamp, item.sequence)))
        duplicate_keys = len(dataset.slices) - len({(item.timestamp, item.sequence) for item in dataset.slices})
        known = {item.instrument_id for item in dataset.definitions}
        unknown = {
            instrument_id.value
            for market in dataset.slices
            for instrument_id in market.instrument_universe
            if instrument_id not in known
        }
        future_facts = 0
        crossed_quotes = 0
        critical_issues = 0
        for market in dataset.slices:
            critical_issues += sum(issue.severity == "error" for issue in market.quality_issues)
            for snapshot in market.instruments:
                for timestamp in (snapshot.quote_time, snapshot.trade_time, snapshot.greeks_time):
                    if timestamp is not None and timestamp > market.timestamp:
                        future_facts += 1
                quote = snapshot.quote
                if quote is not None and quote.bid is not None and quote.ask is not None and quote.bid > quote.ask:
                    crossed_quotes += 1
        lineage = _lineage(self.root / release.relative_path / "lineage.json")
        inputs = lineage.get("inputs")
        frozen_inputs = isinstance(inputs, list) and bool(inputs) and all(
            isinstance(item, dict)
            and bool(item.get("release_id") or item.get("dataset_id"))
            and bool(item.get("content_hash") or item.get("dataset_sha256"))
            for item in inputs
        )
        if manifest.synthetic and not frozen_inputs:
            frozen_inputs = bool(lineage.get("producer") or lineage.get("source"))
        base.extend((
            QualityCheck("manifest_identity", manifest.dataset_id == release.release_id, manifest.dataset_id, "Manifest ID equals Release ID"),
            QualityCheck("historical_manifest_hash", manifest.content_hash == release.content_hash, manifest.content_hash, "Historical Manifest hash equals Release hash"),
            QualityCheck("minimum_slices", manifest.slice_count >= 2, manifest.slice_count, ">= 2 deterministic slices"),
            QualityCheck("deterministic_slice_order", tuple(dataset.slices) == ordered and duplicate_keys == 0, duplicate_keys, "strict timestamp/sequence order"),
            QualityCheck("complete_instrument_definitions", not unknown, sorted(unknown), "all point-in-time universe members have definitions"),
            QualityCheck("contract_coverage", manifest.contract_coverage == Decimal("1"), str(manifest.contract_coverage), "= 1.0"),
            QualityCheck("quote_coverage", manifest.quote_coverage >= Decimal("0.95"), str(manifest.quote_coverage), ">= 0.95"),
            QualityCheck("stale_rate", manifest.stale_rate <= Decimal("0.01"), str(manifest.stale_rate), "<= 0.01"),
            QualityCheck("no_future_market_facts", future_facts == 0, future_facts, "all market fact timestamps <= slice timestamp"),
            QualityCheck("non_crossed_quotes", crossed_quotes == 0, crossed_quotes, "bid <= ask"),
            QualityCheck("no_critical_quality_issues", critical_issues == 0, critical_issues, "0 error-severity slice issues"),
            QualityCheck("frozen_market_slice_inputs", frozen_inputs, frozen_inputs, "lineage declares input Release IDs and hashes"),
            QualityCheck("synthetic_provenance_declared", isinstance(manifest.synthetic, bool), manifest.synthetic, "synthetic provenance is explicit"),
        ))
        return tuple(base)


def _time(value: object) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("quality assessment requires timezone-aware timestamps")
    return result


def _safe_time(value: object) -> datetime | None:
    try:
        return _time(value)
    except (TypeError, ValueError):
        return None


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    return result if result.is_finite() else None


def _finite(value: object) -> bool:
    if isinstance(value, Decimal):
        return value.is_finite()
    if isinstance(value, float):
        return math.isfinite(value)
    return True


def _first_field(fields: set[str], *candidates: str) -> str | None:
    return next((name for name in candidates if name in fields), None)


def _physical_order(fields: set[str]) -> str:
    partitions = [name for name in ("event_year", "event_month", "event_day") if name in fields]
    return ", ".join((*partitions, "filename", "file_row_number"))


def _required_check(required: set[str], fields: set[str], requirement: str) -> QualityCheck:
    return QualityCheck("required_fields", required <= fields, sorted(required - fields), requirement)


def _lineage(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}
