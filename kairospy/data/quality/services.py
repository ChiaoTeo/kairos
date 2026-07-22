from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import math
from pathlib import Path

from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT
from kairospy.infrastructure.storage.data_lake import write_json

from ..catalog import DataCatalog
from ..contracts import DatasetRelease, QualityLevel


@dataclass(frozen=True, slots=True)
class QualityCheck:
    name: str
    passed: bool
    value: object
    requirement: str
    severity: str = "diagnostic"


@dataclass(frozen=True, slots=True)
class QualityAssessment:
    release_id: str
    profile: str
    level: QualityLevel
    passed: bool
    checks: tuple[QualityCheck, ...]
    report_hash: str


class DatasetQualityService:
    def __init__(self, root: str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.root = Path(root)
        self.catalog = DataCatalog(self.root)

    def assess(self, dataset: str) -> QualityAssessment:
        release = self.catalog.release(dataset)
        profile = self._profile(release, [])
        streaming = profile in {"ohlcv", "trade", "market_event"} and self._has_parquet(release)
        rows = [] if profile in {"market_snapshot", "market_slice", "corporate_action", "equity_identity"} or streaming else self._load_rows_for_assessment(release)
        profiles = {
            "ohlcv": self._ohlcv,
            "quote": self._quote,
            "trade": self._trade,
            "market_event": self._market_event,
            "option_snapshot": self._option_snapshot,
            "feature": self._feature,
            "equity_returns": self._equity_returns,
            "equity_universe": self._equity_universe,
            "equity_feature": self._equity_feature,
            "equity_ohlcv": self._equity_ohlcv,
            "corporate_action": self._corporate_action,
            "equity_identity": self._equity_identity,
            "reference": self._reference,
            "generic": self._integrity,
            "integrity": self._integrity,
        }
        try:
            if profile in {"market_snapshot", "market_slice"}:
                checks = self._market_snapshot(release)
            elif streaming:
                if profile == "ohlcv":
                    checks = self._streaming_ohlcv(release)
                elif profile == "trade":
                    checks = self._streaming_trade(release)
                else:
                    checks = self._streaming_market_event(release)
            else:
                checks = profiles[profile](rows, release)
        except KeyError as error:
            raise ValueError(f"unsupported dataset quality profile: {profile}") from error
        passed = all(item.passed for item in checks)
        if profile in {"market_snapshot", "market_slice"}:
            level = QualityLevel.BACKTEST
        elif profile == "ohlcv" and (len(rows) >= 365 or _passed(checks, "backtest_history")):
            level = QualityLevel.BACKTEST
        elif profile in {"equity_returns", "equity_universe", "equity_feature"} and passed:
            level = QualityLevel.BACKTEST
        elif profile == "corporate_action" and passed:
            level = QualityLevel.WORKSPACE
        elif profile == "equity_identity" and passed:
            level = QualityLevel.WORKSPACE
        else:
            level = QualityLevel.WORKSPACE
        primitive = {
            "quality_schema_version": 2,
            "release_id": release.release_id,
            "logical_key": str(release.product_key),
            "profile": profile,
            "level": level.value,
            "passed": passed,
            "checks": [
                {"name": item.name, "passed": item.passed, "value": item.value, "requirement": item.requirement}
                | ({"severity": item.severity} if item.severity != "gate" else {})
                for item in checks
            ],
        }
        existing_quality = _lineage(self.root / release.relative_path / "quality.json")
        if isinstance(existing_quality.get("known_limitations"), list):
            primitive["known_limitations"] = existing_quality["known_limitations"]
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

    def _load_rows_for_assessment(self, release: DatasetRelease) -> list[dict[str, object]]:
        directory = self.root / release.relative_path
        rows: list[dict[str, object]] = []
        for path in sorted(directory.glob("**/*.parquet")):
            import pyarrow.parquet as pq

            rows.extend(pq.read_table(path).to_pylist())
        for path in sorted(directory.glob("**/*.csv")):
            import csv

            with path.open(newline="", encoding="utf-8") as handle:
                rows.extend(dict(row) for row in csv.DictReader(handle))
        return rows

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

    def _coverage_payload(self, release: DatasetRelease) -> dict[str, object]:
        payload = _lineage(self.root / release.relative_path / "coverage.json")
        return payload if isinstance(payload, dict) else {}

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

    def _streaming_ohlcv(self, release: DatasetRelease) -> tuple[QualityCheck, ...]:
        connection, fields = self._streaming_view(release)
        try:
            physical_order = _physical_order(fields)
            required = {
                "instrument_id", "period_start", "period_end", "event_time", "available_time",
                "open", "high", "low", "close", "volume",
            }
            required_ok = required <= fields
            count = int(connection.execute("SELECT count(*) FROM quality_rows").fetchone()[0])
            base = self._streaming_integrity(release, count)
            base.append(QualityCheck("required_fields", required_ok, sorted(required - fields), "all OHLCV/time fields present"))
            base.append(self._schema_timezones_check(release))
            if not required_ok:
                return tuple(base)
            duplicates, invalid_ohlc, invalid_volume, invalid_time = connection.execute("""
                SELECT
                    count(*) - count(DISTINCT (instrument_id, period_start)),
                    count(*) FILTER (
                        WHERE low > least(open, close)
                           OR high < greatest(open, close)
                           OR low > high
                           OR least(open, high, low, close) <= 0
                    ),
                    count(*) FILTER (WHERE volume < 0),
                    count(*) FILTER (
                        WHERE period_start >= period_end
                           OR event_time < period_end
                           OR available_time < event_time
                    )
                FROM quality_rows
            """).fetchone()
            unordered = int(connection.execute(f"""
                SELECT count(*) FROM (
                    SELECT period_start, instrument_id,
                           lag(period_start) OVER physical AS previous_start,
                           lag(instrument_id) OVER physical AS previous_instrument
                    FROM quality_rows
                    WINDOW physical AS (ORDER BY {physical_order})
                )
                WHERE previous_start IS NOT NULL
                  AND (period_start, cast(instrument_id AS VARCHAR))
                      < (previous_start, cast(previous_instrument AS VARCHAR))
            """).fetchone()[0])
            coverage = self._coverage_payload(release)
            coverage_body = coverage.get("coverage", {}) if isinstance(coverage, dict) else {}
            ratio = Decimal(str(coverage_body.get("coverage_ratio", 0)))
            missing = coverage.get("missing_ranges", []) if isinstance(coverage, dict) else []
            base.extend((
                QualityCheck("unique_primary_key", int(duplicates) == 0, int(duplicates), "0 duplicates"),
                QualityCheck("valid_ohlc", int(invalid_ohlc) == 0, int(invalid_ohlc), "positive prices and low <= open/close <= high"),
                QualityCheck("non_negative_volume", int(invalid_volume) == 0, int(invalid_volume), "0 negative volumes"),
                QualityCheck("point_in_time_order", int(invalid_time) == 0, int(invalid_time), "start < end <= event <= available"),
                QualityCheck("deterministic_order", unordered == 0, unordered, "physical rows ordered by time/instrument"),
                _diagnostic_check("coverage_ratio", ratio >= Decimal("0.99"), str(ratio), ">= 0.99"),
                _diagnostic_check("missing_ranges", not missing, len(missing), "0 missing ranges"),
                _diagnostic_check("backtest_history", count >= 365, count, ">= 365 observations"),
                _diagnostic_check("streaming_execution", True, count, "quality computed without materializing rows"),
            ))
            return tuple(base)
        finally:
            connection.close()

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
                _diagnostic_check("streaming_execution", True, count, "quality computed without materializing rows"),
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
                _diagnostic_check("streaming_execution", True, count, "quality computed without materializing rows"),
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
        manifest_hash = manifest.get("dataset_sha256") or manifest.get("content_sha256") or manifest.get("content_hash")
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
        base.append(self._schema_timezones_check(release))
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
            identity = (start, str(row.get("instrument_id", "")))
            if previous is not None and identity < previous:
                unordered += 1
            previous = identity
        coverage = self._coverage_payload(release)
        coverage_body = coverage.get("coverage", {}) if isinstance(coverage, dict) else {}
        ratio = Decimal(str(coverage_body.get("coverage_ratio", 0)))
        missing = coverage.get("missing_ranges", []) if isinstance(coverage, dict) else []
        base.extend((
            QualityCheck("unique_primary_key", duplicates == 0, duplicates, "0 duplicates"),
            QualityCheck("valid_ohlc", invalid_ohlc == 0, invalid_ohlc, "positive prices and low <= open/close <= high"),
            QualityCheck("non_negative_volume", invalid_volume == 0, invalid_volume, "0 negative volumes"),
            QualityCheck("point_in_time_order", invalid_time == 0, invalid_time, "start < end <= event <= available"),
            QualityCheck("deterministic_order", unordered == 0, unordered, "rows ordered by time/instrument"),
            _diagnostic_check("coverage_ratio", ratio >= Decimal("0.99"), str(ratio), ">= 0.99"),
            _diagnostic_check("missing_ranges", not missing, len(missing), "0 missing ranges"),
            _diagnostic_check("backtest_history", len(rows) >= 365, len(rows), ">= 365 observations"),
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

    def _corporate_action(self, rows: list[dict[str, object]], release: DatasetRelease) -> tuple[QualityCheck, ...]:
        directory = self.root / release.relative_path
        files = [directory / "events.json"] if (directory / "events.json").exists() else sorted(directory.glob("**/events.json"))
        manifest = _lineage(directory / "manifest.json")
        events: list[dict[str, object]] = []
        invalid_files = 0
        for path in files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                invalid_files += 1
                continue
            if not isinstance(payload, list):
                invalid_files += 1
                continue
            events.extend(item for item in payload if isinstance(item, dict))

        invalid_identity = invalid_date = invalid_ratio = invalid_amount = unsupported = 0
        identities = []
        for event in events:
            instrument = _instrument_value(event.get("instrument_id"))
            ticker = str(event.get("ticker") or "").strip().upper()
            if not instrument:
                invalid_identity += 1
            if "ratio" in event:
                event_date = _event_date(event.get("effective_at"))
                ratio = _decimal_value(event.get("ratio"))
                if event_date is None:
                    invalid_date += 1
                if ratio is None or ratio <= 0:
                    invalid_ratio += 1
                identities.append(("split", instrument, event_date, str(ratio), ticker))
            elif "amount_per_share" in event:
                event_date = _event_date(event.get("ex_date"))
                amount = _decimal_value(event.get("amount_per_share"))
                if event_date is None:
                    invalid_date += 1
                if amount is None or amount < 0:
                    invalid_amount += 1
                identities.append(("dividend", instrument, event_date, str(amount), ticker))
            else:
                unsupported += 1

        duplicate_keys = len(identities) - len(set(identities))
        receipt_count = len(manifest.get("source_receipts", [])) if isinstance(manifest.get("source_receipts"), list) else 0
        return (
            QualityCheck("events_file_present", bool(files), len(files), "events.json exists"),
            QualityCheck("valid_event_files", invalid_files == 0, invalid_files, "corporate action files are JSON lists"),
            QualityCheck("event_count_declared", int(manifest.get("event_count", len(events))) == len(events), len(events), "event count matches manifest"),
            QualityCheck("content_hash", bool(release.content_hash), release.content_hash, "frozen content hash"),
            QualityCheck(
                "manifest_content_hash",
                bool(manifest.get("sha256")) and manifest.get("sha256") == release.content_hash,
                {"manifest": manifest.get("sha256"), "release": release.content_hash},
                "Manifest hash equals Release hash",
            ),
            _diagnostic_check("source_receipts", receipt_count > 0, receipt_count, "archived Massive source receipts are declared"),
            QualityCheck("supported_event_types", unsupported == 0, unsupported, "only split and cash dividend events are present"),
            QualityCheck("event_identity", invalid_identity == 0, invalid_identity, "events have instrument_id"),
            QualityCheck("event_dates", invalid_date == 0, invalid_date, "events have effective/ex dates"),
            QualityCheck("positive_split_ratios", invalid_ratio == 0, invalid_ratio, "split ratios are > 0"),
            QualityCheck("non_negative_dividends", invalid_amount == 0, invalid_amount, "cash dividends are >= 0"),
            QualityCheck("unique_corporate_action_key", duplicate_keys == 0, duplicate_keys, "0 duplicate action keys"),
        )

    def _equity_identity(self, rows: list[dict[str, object]], release: DatasetRelease) -> tuple[QualityCheck, ...]:
        directory = self.root / release.relative_path
        manifest = _lineage(directory / "manifest.json")
        mappings = _json_list(directory / "mappings.json")
        instruments = _json_list(directory / "instruments.json")
        quarantined = _json_list(directory / "quarantine.json")
        mapping_keys = []
        invalid_mapping = invalid_range = 0
        for item in mappings:
            external = str(item.get("external_id") or "").strip()
            target = str(item.get("target_id") or "").strip()
            start = _event_date(item.get("effective_from"))
            end = _event_date(item.get("effective_to")) if item.get("effective_to") else None
            if not external or not target or start is None:
                invalid_mapping += 1
            if end is not None and start is not None and end <= start:
                invalid_range += 1
            mapping_keys.append((external, target, start, end))
        instrument_ids = [str(item.get("instrument_id") or "").strip() for item in instruments if isinstance(item, dict)]
        duplicate_mappings = len(mapping_keys) - len(set(mapping_keys))
        duplicate_instruments = len(instrument_ids) - len(set(instrument_ids))
        return (
            QualityCheck("mappings_file_present", (directory / "mappings.json").exists(), len(mappings), "mappings.json exists"),
            QualityCheck("instruments_file_present", (directory / "instruments.json").exists(), len(instruments), "instruments.json exists"),
            QualityCheck("manifest_content_hash", bool(manifest.get("sha256")) and manifest.get("sha256") == release.content_hash,
                         {"manifest": manifest.get("sha256"), "release": release.content_hash}, "Manifest hash equals Release hash"),
            QualityCheck("identity_mapping_count", len(mappings) > 0, len(mappings), "> 0 provider symbol mappings"),
            QualityCheck("identity_instrument_count", len(instruments) > 0, len(instruments), "> 0 stable instruments"),
            QualityCheck("identity_quarantine_clear", len(quarantined) == 0, len(quarantined), "0 unresolved identity quarantine records"),
            QualityCheck("valid_identity_mappings", invalid_mapping == 0, invalid_mapping, "mappings have external_id, target_id and effective_from"),
            QualityCheck("valid_identity_ranges", invalid_range == 0, invalid_range, "effective_to is absent or after effective_from"),
            QualityCheck("unique_identity_mappings", duplicate_mappings == 0, duplicate_mappings, "0 duplicate mapping rows"),
            QualityCheck("unique_identity_instruments", duplicate_instruments == 0, duplicate_instruments, "0 duplicate instrument rows"),
        )

    def _equity_ohlcv(self, rows: list[dict[str, object]], release: DatasetRelease) -> tuple[QualityCheck, ...]:
        if rows and "period_start" in rows[0]:
            return self._ohlcv(rows, release)
        base = list(self._equity_panel_base(rows, release, required={"open", "high", "low", "close", "volume"}))
        if not rows:
            return tuple(base)
        invalid_ohlc = invalid_volume = 0
        for row in rows:
            open_, high, low, close = (Decimal(str(row[name])) for name in ("open", "high", "low", "close"))
            volume = Decimal(str(row["volume"]))
            if low > min(open_, close) or high < max(open_, close) or low > high or min(open_, high, low, close) <= 0:
                invalid_ohlc += 1
            if volume < 0:
                invalid_volume += 1
        base.extend((
            QualityCheck("valid_ohlc", invalid_ohlc == 0, invalid_ohlc, "positive prices and low <= open/close <= high"),
            QualityCheck("non_negative_volume", invalid_volume == 0, invalid_volume, "0 negative volumes"),
        ))
        return tuple(base)

    def _schema_timezones_check(self, release: DatasetRelease) -> QualityCheck:
        schema = _lineage(self.root / release.relative_path / "schema.json")
        columns = schema.get("columns") if isinstance(schema, dict) else None
        fields = ("period_start", "period_end", "event_time", "available_time")
        declared = isinstance(columns, dict) and all(
            isinstance(columns.get(field), dict) and columns[field].get("timezone") == "UTC"
            for field in fields
        )
        return QualityCheck("schema_timezones", declared, list(fields), "time fields declare UTC timezone")

    def _equity_returns(self, rows: list[dict[str, object]], release: DatasetRelease) -> tuple[QualityCheck, ...]:
        base = list(self._equity_panel_base(
            rows, release,
            required={"close_raw", "simple_return", "split_ratio", "cash_dividend", "split_adjusted_return", "total_return"},
        ))
        invalid = 0
        for row in rows:
            for name in ("close_raw", "simple_return", "split_ratio", "cash_dividend", "split_adjusted_return", "total_return"):
                value = row.get(name)
                if value is not None and _decimal(value) is None:
                    invalid += 1
        lineage = _lineage(self.root / release.relative_path / "lineage.json")
        source = lineage.get("source") if isinstance(lineage, dict) else None
        corporate_actions_declared = isinstance(source, dict) and "corporate_actions_sha256" in source
        base.append(QualityCheck("finite_return_values", invalid == 0, invalid, "return fields are finite when present"))
        base.append(QualityCheck(
            "corporate_action_source_declared",
            corporate_actions_declared,
            source if isinstance(source, dict) else None,
            "lineage declares whether split/dividend corporate action inputs were supplied",
        ))
        return tuple(base)

    def _equity_universe(self, rows: list[dict[str, object]], release: DatasetRelease) -> tuple[QualityCheck, ...]:
        base = list(self._equity_panel_base(
            rows, release,
            required={"exists", "eligible", "exclusion_reasons", "price_observation_status", "missing_reason", "critical_gap"},
        ))
        invalid_reasons = sum(1 for row in rows if not isinstance(row.get("exclusion_reasons"), list))
        valid_statuses = {"observed", "missing_bar"}
        valid_missing_reasons = {
            "expected_trading_session_without_bar",
            "not_yet_listed",
            "delisted_after_reference_end",
        }
        invalid_status = sum(1 for row in rows if row.get("price_observation_status") not in valid_statuses)
        missing_without_reason = sum(
            1 for row in rows
            if row.get("price_observation_status") == "missing_bar" and not row.get("missing_reason")
        )
        invalid_missing_reason = sum(
            1 for row in rows
            if row.get("price_observation_status") == "missing_bar" and row.get("missing_reason") not in valid_missing_reasons
        )
        base.extend((
            QualityCheck("structured_exclusion_reasons", invalid_reasons == 0, invalid_reasons, "exclusion reasons are lists"),
            QualityCheck("valid_price_observation_status", invalid_status == 0, invalid_status, "status is observed or missing_bar"),
            QualityCheck("missing_bars_have_reason", missing_without_reason == 0, missing_without_reason, "missing bars declare a reason"),
            QualityCheck("valid_missing_bar_reason", invalid_missing_reason == 0, invalid_missing_reason, "missing reason is a supported conservative classification"),
        ))
        return tuple(base)

    def _equity_feature(self, rows: list[dict[str, object]], release: DatasetRelease) -> tuple[QualityCheck, ...]:
        base = list(self._equity_panel_base(rows, release, required=set()))
        invalid_numeric = 0
        for row in rows:
            for name, value in row.items():
                if name in {"instrument_id", "ticker", "event_date", "available_time"} or value is None or isinstance(value, (str, bool, datetime)):
                    continue
                if isinstance(value, (int, float, Decimal)) and not _finite(value):
                    invalid_numeric += 1
        base.append(QualityCheck("finite_feature_values", invalid_numeric == 0, invalid_numeric, "no NaN or infinite numeric values"))
        return tuple(base)

    def _equity_panel_base(
        self, rows: list[dict[str, object]], release: DatasetRelease, *, required: set[str],
    ) -> tuple[QualityCheck, ...]:
        base = list(self._integrity(rows, release))
        fields = set(rows[0]) if rows else set()
        required_fields = {"instrument_id", "event_date", "available_time", *required}
        base.append(_required_check(required_fields, fields, "US equity panel identity, date, visibility and required fields"))
        if not rows or not required_fields <= fields:
            return tuple(base)
        identities = [(str(row["instrument_id"]), str(row["event_date"])) for row in rows]
        duplicates = len(identities) - len(set(identities))
        invalid_time = 0
        previous = None
        unordered = 0
        for row in rows:
            available = _safe_time(row["available_time"])
            event_date = row.get("event_date")
            if available is None or event_date is None:
                invalid_time += 1
            identity = (str(row["instrument_id"]), str(row["event_date"]))
            if previous is not None and identity < previous:
                unordered += 1
            previous = identity
        lineage = _lineage(self.root / release.relative_path / "lineage.json")
        source = lineage.get("source")
        frozen_input = isinstance(source, dict) and bool(source.get("content_sha256"))
        base.extend((
            QualityCheck("unique_equity_panel_key", duplicates == 0, duplicates, "0 duplicate instrument/date rows"),
            QualityCheck("valid_available_time", invalid_time == 0, invalid_time, "available_time is present and parseable"),
            QualityCheck("deterministic_order", unordered == 0, unordered, "rows ordered by instrument/date"),
            QualityCheck("frozen_source_hash", frozen_input, frozen_input, "lineage declares source content hash"),
        ))
        return tuple(base)

    def _market_snapshot(self, release: DatasetRelease) -> tuple[QualityCheck, ...]:
        raw_rows = self.client.load_rows(release.release_id)
        base = list(self._integrity(raw_rows, release))
        dataset = self.client.replay_snapshots(release.release_id).dataset
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
            QualityCheck("frozen_market_snapshot_inputs", frozen_inputs, frozen_inputs, "lineage declares input Release IDs and hashes"),
            QualityCheck("synthetic_provenance_declared", isinstance(manifest.synthetic, bool), manifest.synthetic, "synthetic provenance is explicit"),
        ))
        return tuple(base)

    def _market_slice(self, release: DatasetRelease) -> tuple[QualityCheck, ...]:
        return self._market_snapshot(release)


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


def _diagnostic_check(name: str, passed: bool, value: object, requirement: str) -> QualityCheck:
    return QualityCheck(name, passed, value, requirement, severity="diagnostic")


def _instrument_value(value: object) -> str | None:
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, dict):
        inner = value.get("value") or value.get("instrument_id")
        return str(inner) if inner else None
    return None


def _event_date(value: object) -> datetime | None:
    if isinstance(value, dict):
        value = value.get("$datetime") or value.get("$date")
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if value is None:
        return None
    try:
        return _time(str(value))
    except ValueError:
        try:
            return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _decimal_value(value: object) -> Decimal | None:
    if isinstance(value, dict):
        value = value.get("$decimal")
    return _decimal(value)


def _passed(checks: tuple[QualityCheck, ...], name: str) -> bool:
    return any(item.name == name and item.passed for item in checks)


def _lineage(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _json_list(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
