from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable

from kairospy.backtest.calendar import TradingCalendar
from kairospy.configuration import DEFAULT_LAKE_ROOT
from kairospy.data.bootstrap import register_default_products
from kairospy.data.catalog import DataCatalog
from kairospy.data.contracts import DatasetRelease, DatasetStatus, DatasetStorageKind, QualityLevel
from kairospy.storage.data_lake import write_json


@dataclass(frozen=True, slots=True)
class UsEquityMomentumPolicy:
    policy_id: str = "us-equity-momentum-v1"
    version: str = "1"
    minimum_price: Decimal = Decimal("5")
    minimum_adv20: Decimal = Decimal("10000000")
    minimum_history: int = 252

    def __post_init__(self) -> None:
        if self.minimum_price <= 0 or self.minimum_adv20 < 0 or self.minimum_history < 1:
            raise ValueError("US equity momentum policy thresholds must be positive")


class UsEquityMomentumDatasetBuilder:
    """Build first-pass US equity returns, universe, liquidity and momentum datasets.

    This builder intentionally starts from governed OHLCV parquet instead of calling
    a provider.  It is suitable for bounded smoke datasets and keeps full-market
    point-in-time reference requirements visible as quality limitations until the
    reference pipeline is complete.
    """

    def __init__(self, lake_root: str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.root = Path(lake_root)

    def build_from_ohlcv_directory(
        self,
        source_directory: str | Path,
        *,
        dataset_id: str,
        policy: UsEquityMomentumPolicy = UsEquityMomentumPolicy(),
        corporate_actions_directory: str | Path | None = None,
        reference_directory: str | Path | None = None,
    ) -> dict[str, object]:
        register_default_products(self.root)
        source = Path(source_directory)
        if not source.is_absolute():
            source = self.root / source
        corporate_actions = _read_corporate_actions(
            self.root, corporate_actions_directory,
        ) if corporate_actions_directory is not None else _CorporateActions()
        reference = _read_reference(self.root, reference_directory) if reference_directory is not None else _ReferenceEvidence()
        rows = _read_ohlcv(source)
        if not rows:
            raise ValueError("US equity momentum builder requires non-empty OHLCV input")
        rows.sort(key=lambda item: (item["instrument_id"], item["event_date"]))
        rows = _materialize_missing_sessions(rows, reference)
        source_manifest = _read_json(source / "manifest.json")
        source_hash = str(source_manifest.get("content_sha256") or _hash(rows))

        returns = _returns(rows, corporate_actions)
        liquidity = _liquidity(rows, policy)
        universe = _universe(rows, liquidity, policy)
        momentum = _momentum(returns)

        outputs = {
            "returns": _Output(
                logical_key="market.returns.equity.us.1d",
                layer_path="curated/market/returns/asset_class=equity/region=us/interval=1d",
                schema_id="market.returns.equity.us.1d.v1",
                rows=returns,
                primary_key=("instrument_id", "event_date"),
            ),
            "universe": _Output(
                logical_key="market.universe.equity.us.1d",
                layer_path="curated/market/universe/asset_class=equity/region=us/frequency=1d",
                schema_id="market.universe.equity.us.1d.v1",
                rows=universe,
                primary_key=("instrument_id", "event_date"),
            ),
            "liquidity": _Output(
                logical_key="features.liquidity.equity.us.1d",
                layer_path="features/equity/region=us/feature_set=liquidity-v1/frequency=1d",
                schema_id="features.liquidity.equity.us.1d.v1",
                rows=liquidity,
                primary_key=("instrument_id", "event_date"),
            ),
            "momentum": _Output(
                logical_key="features.momentum.equity.us.1d",
                layer_path="features/equity/region=us/feature_set=momentum-v1/frequency=1d",
                schema_id="features.momentum.equity.us.1d.v1",
                rows=momentum,
                primary_key=("instrument_id", "event_date"),
            ),
        }

        written = {}
        for name, output in outputs.items():
            written[name] = self._write_output(
                output, dataset_id=dataset_id, source_hash=source_hash,
                policy=policy, corporate_actions=corporate_actions, reference=reference,
            )
        manifest = {
            "manifest_version": 1,
            "dataset_id": dataset_id,
            "source_directory": str(source.relative_to(self.root)) if source.is_relative_to(self.root) else str(source),
            "source_content_sha256": source_hash,
            "corporate_actions": corporate_actions.manifest(),
            "reference": reference.manifest(),
            "policy": _policy_payload(policy),
            "outputs": written,
        }
        target = self.root / "features/equity/region=us/feature_set=momentum-v1/frequency=1d" / f"dataset={dataset_id}"
        target.mkdir(parents=True, exist_ok=True)
        write_json(target / "build_manifest.json", manifest)
        return manifest

    def _write_output(
        self,
        output: "_Output",
        *,
        dataset_id: str,
        source_hash: str,
        policy: UsEquityMomentumPolicy,
        corporate_actions: "_CorporateActions",
        reference: "_ReferenceEvidence",
    ) -> dict[str, object]:
        content_hash = _hash(output.rows)
        release_id = f"ds_{content_hash[:24]}"
        target = self.root / output.layer_path / f"dataset={release_id}"
        target.mkdir(parents=True, exist_ok=True)
        parquet = target / f"part-{content_hash[:24]}.parquet"
        if not parquet.exists():
            _write_parquet(parquet, output.rows)
        manifest = {
            "manifest_version": 1,
            "dataset_id": dataset_id,
            "release_id": release_id,
            "logical_key": output.logical_key,
            "rows": len(output.rows),
            "file": parquet.name,
            "content_sha256": content_hash,
            "file_sha256": _file_hash(parquet),
        }
        dates = [item["event_date"] for item in output.rows]
        instruments = sorted({str(item["instrument_id"]) for item in output.rows})
        write_json(target / "manifest.json", manifest)
        write_json(target / "schema.json", {
            "schema_id": output.schema_id,
            "schema_version": 1,
            "primary_key": list(output.primary_key),
            "primary_time": "available_time",
            "fields": sorted(output.rows[0]) if output.rows else [],
        })
        write_json(target / "coverage.json", {
            "start": min(dates).isoformat() if dates else None,
            "end": max(dates).isoformat() if dates else None,
            "boundary": "inclusive-dates",
            "rows": len(output.rows),
            "observed_rows": sum(1 for item in output.rows if item.get("price_observation_status") == "observed"),
            "missing_bar_rows": sum(1 for item in output.rows if item.get("price_observation_status") == "missing_bar"),
            "instrument_count": len(instruments),
            "instruments": instruments,
        })
        write_json(target / "lineage.json", {
            "lineage_version": 1,
            "producer": {"name": type(self).__name__, "version": 1},
            "transform": {"id": policy.policy_id, "version": policy.version},
            "source": {
                "content_sha256": source_hash,
                "corporate_actions_sha256": corporate_actions.content_sha256,
                "corporate_action_event_count": corporate_actions.event_count,
                "reference_sha256": reference.content_sha256,
                "reference_record_count": reference.record_count,
            },
            "point_in_time_safe": True,
        })
        limitations = [
            "bounded OHLCV-derived dataset; full-market point-in-time reference is not proven",
            "delisting returns are not independently rebuilt in this builder",
        ]
        if corporate_actions.event_count:
            limitations.append("corporate actions are limited to supplied split and cash dividend events")
        else:
            limitations.append("corporate actions are not supplied; total_return falls back to raw close returns")
        limitations.append("missing bars are classified from the internal US securities calendar; halt/delist/download failure requires reference evidence")
        write_json(target / "quality.json", {
            "publishable": True,
            "quality_level": "Q2" if output.logical_key.startswith("market.") else "Q3",
            "known_limitations": limitations,
        })
        self._register_release(output, release_id=release_id, target=target, content_hash=content_hash)
        return manifest

    def _register_release(self, output: "_Output", *, release_id: str, target: Path, content_hash: str) -> None:
        catalog = DataCatalog(self.root)
        release = DatasetRelease(
            release_id,
            catalog.product(output.logical_key).key,
            f"content.{content_hash[:16]}",
            output.schema_id,
            "1",
            "us_equity_momentum_builder",
            "1",
            str(target.relative_to(self.root)),
            "parquet",
            content_hash,
            "internal",
            "us-equity",
            (f"{output.logical_key}@latest-study",),
            DatasetStatus.APPROVED_FOR_STUDY,
            QualityLevel.STUDY,
            datetime.now(timezone.utc).isoformat(),
            DatasetStorageKind.TABULAR,
            "1",
        )
        catalog.register_release(release)
        catalog.save()


@dataclass(frozen=True, slots=True)
class _Output:
    logical_key: str
    layer_path: str
    schema_id: str
    rows: list[dict[str, object]]
    primary_key: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _CorporateActions:
    splits: dict[tuple[str, date], tuple[Decimal, ...]] | None = None
    dividends: dict[tuple[str, date], tuple[Decimal, ...]] | None = None
    content_sha256: str | None = None
    event_count: int = 0
    directory: str | None = None

    def split_ratio(self, instrument_id: str, event_date: date) -> Decimal:
        total = Decimal("1")
        for value in (self.splits or {}).get((instrument_id, event_date), ()):
            total *= value
        return total

    def cash_dividend(self, instrument_id: str, event_date: date) -> Decimal:
        return sum((self.dividends or {}).get((instrument_id, event_date), ()), Decimal("0"))

    def manifest(self) -> dict[str, object]:
        return {
            "directory": self.directory,
            "content_sha256": self.content_sha256,
            "event_count": self.event_count,
        }


@dataclass(frozen=True, slots=True)
class _ReferenceRecord:
    instrument_id: str
    listing_date: date | None
    delisting_date: date | None
    security_type: str | None
    active: bool | None


@dataclass(frozen=True, slots=True)
class _ReferenceEvidence:
    records: dict[str, _ReferenceRecord] | None = None
    content_sha256: str | None = None
    record_count: int = 0
    directory: str | None = None

    def get(self, instrument_id: str) -> _ReferenceRecord | None:
        return (self.records or {}).get(instrument_id)

    def classify_missing(self, instrument_id: str, event_date: date) -> str:
        record = self.get(instrument_id)
        if record is None:
            return "expected_trading_session_without_bar"
        if record.listing_date is not None and event_date < record.listing_date:
            return "not_yet_listed"
        if record.delisting_date is not None and event_date >= record.delisting_date:
            return "delisted_after_reference_end"
        return "expected_trading_session_without_bar"

    def manifest(self) -> dict[str, object]:
        return {
            "directory": self.directory,
            "content_sha256": self.content_sha256,
            "record_count": self.record_count,
        }


def _read_ohlcv(source: Path) -> list[dict[str, object]]:
    files = _parquet_inputs(source)
    if not files:
        raise FileNotFoundError(f"no parquet files found in {source}")
    rows: list[dict[str, object]] = []
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("US equity momentum builder requires the 'data' optional dependency") from error
    for path in files:
        for row in pq.read_table(path).to_pylist():
            if row.get("instrument_id") is None or row.get("event_date") is None:
                continue
            rows.append(row)
    return rows


def _materialize_missing_sessions(rows: list[dict[str, object]], reference: _ReferenceEvidence) -> list[dict[str, object]]:
    calendar = TradingCalendar()
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        enriched = dict(row)
        enriched.setdefault("price_observation_status", "observed")
        enriched.setdefault("missing_reason", None)
        grouped[str(row["instrument_id"])].append(enriched)
    result: list[dict[str, object]] = []
    for instrument, instrument_rows in grouped.items():
        instrument_rows.sort(key=lambda item: item["event_date"])
        by_date = {item["event_date"]: item for item in instrument_rows}
        first, last = instrument_rows[0]["event_date"], instrument_rows[-1]["event_date"]
        last_seen = instrument_rows[0]
        for trading_day in calendar.trading_days_between(first, last):
            observed = by_date.get(trading_day)
            if observed is not None:
                last_seen = observed
                result.append(observed)
                continue
            session = calendar.session(trading_day)
            missing_reason = reference.classify_missing(instrument, trading_day)
            result.append({
                "ticker": last_seen.get("ticker"),
                "instrument_id": instrument,
                "price_view": last_seen.get("price_view"),
                "event_date": trading_day,
                "window_start": session.opens_at.astimezone(timezone.utc),
                "available_time": session.closes_at.astimezone(timezone.utc),
                "open": None,
                "high": None,
                "low": None,
                "close": None,
                "volume": None,
                "transactions": None,
                "vwap": None,
                "price_observation_status": "missing_bar",
                "missing_reason": missing_reason,
            })
    return sorted(result, key=lambda item: (item["instrument_id"], item["event_date"]))


def _parquet_inputs(source: Path) -> list[Path]:
    manifest = _read_json(source / "manifest.json")
    declared = []
    for key in ("file", "files"):
        value = manifest.get(key)
        if isinstance(value, str):
            declared.append(source / value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    declared.append(source / item)
                elif isinstance(item, dict) and item.get("path"):
                    declared.append(source / str(item["path"]))
    existing = sorted({path for path in declared if path.suffix == ".parquet" and path.exists()})
    if existing:
        return existing
    return sorted(source.glob("**/part-*.parquet")) or sorted(source.glob("*.parquet"))


def _read_corporate_actions(root: Path, directory: str | Path) -> _CorporateActions:
    source = Path(directory)
    if not source.is_absolute():
        source = root / source
    files = [source / "events.json"] if (source / "events.json").exists() else sorted(source.glob("**/events.json"))
    if not files:
        raise FileNotFoundError(f"no corporate action events.json files found in {source}")
    splits: dict[tuple[str, date], list[Decimal]] = defaultdict(list)
    dividends: dict[tuple[str, date], list[Decimal]] = defaultdict(list)
    events: list[object] = []
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"corporate action file must contain a JSON list: {path}")
        events.extend(payload)
        for event in payload:
            if not isinstance(event, dict):
                continue
            instrument = _instrument_value(event.get("instrument_id"))
            if not instrument:
                continue
            if "ratio" in event:
                ratio = _decimal_value(event["ratio"])
                if ratio <= 0:
                    raise ValueError("split ratio must be positive")
                splits[(instrument, _event_date(event.get("effective_at")))].append(ratio)
            elif "amount_per_share" in event:
                amount = _decimal_value(event["amount_per_share"])
                if amount < 0:
                    raise ValueError("cash dividend amount cannot be negative")
                dividends[(instrument, _event_date(event.get("ex_date")))].append(amount)
    digest = _hash(events)
    return _CorporateActions(
        {key: tuple(value) for key, value in splits.items()},
        {key: tuple(value) for key, value in dividends.items()},
        digest,
        len(events),
        str(source.relative_to(root)) if source.is_relative_to(root) else str(source),
    )


def _read_reference(root: Path, directory: str | Path) -> _ReferenceEvidence:
    source = Path(directory)
    if not source.is_absolute():
        source = root / source
    files = [source / "instruments.json"] if (source / "instruments.json").exists() else sorted(source.glob("**/instruments.json"))
    if not files:
        raise FileNotFoundError(f"no reference instruments.json files found in {source}")
    records: dict[str, _ReferenceRecord] = {}
    payloads: list[object] = []
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"reference instruments file must contain a JSON list: {path}")
        payloads.extend(payload)
        for item in payload:
            if not isinstance(item, dict):
                continue
            instrument = _instrument_value(item.get("instrument_id"))
            if not instrument:
                continue
            records[instrument] = _ReferenceRecord(
                instrument,
                _optional_date(item.get("listing_date")),
                _optional_date(item.get("delisting_date")),
                str(item.get("security_type")) if item.get("security_type") is not None else None,
                bool(item["active"]) if "active" in item else None,
            )
    manifest = _read_json(source / "manifest.json")
    return _ReferenceEvidence(
        records,
        str(manifest.get("sha256") or _hash(payloads)),
        len(records),
        str(source.relative_to(root)) if source.is_relative_to(root) else str(source),
    )


def _returns(rows: list[dict[str, object]], corporate_actions: _CorporateActions) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    previous_close: dict[str, Decimal] = {}
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        instrument = str(row["instrument_id"])
        close = Decimal(str(row["close"])) if row.get("close") is not None else None
        previous = previous_close.get(instrument)
        event_date = row["event_date"]
        split_ratio = corporate_actions.split_ratio(instrument, event_date)
        cash_dividend = corporate_actions.cash_dividend(instrument, event_date)
        adjusted_previous = previous / split_ratio if previous and split_ratio != 0 else previous
        simple_return = close / previous - Decimal("1") if close is not None and previous and previous != 0 else None
        split_adjusted_return = close / adjusted_previous - Decimal("1") if close is not None and adjusted_previous and adjusted_previous != 0 else None
        total_return = (close + cash_dividend) / adjusted_previous - Decimal("1") if close is not None and adjusted_previous and adjusted_previous != 0 else None
        observed = row.get("price_observation_status") == "observed"
        if observed:
            counts[instrument] += 1
        result.append({
            "instrument_id": instrument,
            "ticker": row.get("ticker"),
            "event_date": event_date,
            "available_time": row["available_time"],
            "close_raw": close,
            "simple_return": simple_return,
            "split_ratio": split_ratio,
            "cash_dividend": cash_dividend,
            "split_adjusted_return": split_adjusted_return,
            "total_return": total_return,
            "history_observations": counts[instrument],
            "price_observation_status": row.get("price_observation_status", "observed"),
            "missing_reason": row.get("missing_reason"),
            "source_adjustment_status": "split-dividend-events" if corporate_actions.event_count else "raw-close-only",
        })
        if close is not None:
            previous_close[instrument] = close
    return result


def _liquidity(rows: list[dict[str, object]], policy: UsEquityMomentumPolicy) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    notionals: dict[str, deque[Decimal]] = defaultdict(lambda: deque(maxlen=20))
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        instrument = str(row["instrument_id"])
        close = Decimal(str(row["close"])) if row.get("close") is not None else None
        volume = Decimal(str(row.get("volume") or 0))
        notional = close * volume if close is not None else None
        history_before = counts[instrument]
        trailing = notionals[instrument]
        adv20 = sum(trailing, Decimal("0")) / Decimal(len(trailing)) if trailing else None
        result.append({
            "instrument_id": instrument,
            "ticker": row.get("ticker"),
            "event_date": row["event_date"],
            "available_time": row["available_time"],
            "dollar_volume": notional,
            "adv20": adv20,
            "adv20_observations": len(trailing),
            "liquidity_eligible": row.get("price_observation_status") == "observed" and adv20 is not None and adv20 >= policy.minimum_adv20,
            "history_observations_before": history_before,
            "price_observation_status": row.get("price_observation_status", "observed"),
            "missing_reason": row.get("missing_reason"),
        })
        if notional is not None:
            trailing.append(notional)
            counts[instrument] += 1
    return result


def _universe(
    rows: list[dict[str, object]], liquidity: list[dict[str, object]], policy: UsEquityMomentumPolicy,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    by_key = {(item["instrument_id"], item["event_date"]): item for item in liquidity}
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        instrument = str(row["instrument_id"])
        close = Decimal(str(row["close"])) if row.get("close") is not None else None
        observed = row.get("price_observation_status") == "observed"
        observations = counts[instrument] + (1 if observed else 0)
        liquid = by_key[(instrument, row["event_date"])]
        reasons = []
        if not observed:
            reasons.append(str(row.get("missing_reason") or "missing_bar"))
        if close is not None and close < policy.minimum_price:
            reasons.append("price_below_minimum")
        if observations < policy.minimum_history:
            reasons.append("insufficient_history")
        if not liquid["liquidity_eligible"]:
            reasons.append("insufficient_adv20")
        result.append({
            "instrument_id": instrument,
            "ticker": row.get("ticker"),
            "event_date": row["event_date"],
            "available_time": row["available_time"],
            "exists": True,
            "security_type": "common_stock_unverified",
            "base_universe": observed,
            "eligible": not reasons,
            "exclusion_reasons": reasons,
            "raw_close": close,
            "history_observations": observations,
            "adv20": liquid["adv20"],
            "price_observation_status": row.get("price_observation_status", "observed"),
            "missing_reason": row.get("missing_reason"),
            "critical_gap": not observed,
        })
        if observed:
            counts[instrument] = observations
    return result


def _momentum(returns: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    windows: dict[str, deque[Decimal | None]] = defaultdict(lambda: deque(maxlen=252))
    for row in returns:
        instrument = str(row["instrument_id"])
        history = tuple(windows[instrument])
        result.append({
            "instrument_id": instrument,
            "ticker": row.get("ticker"),
            "event_date": row["event_date"],
            "available_time": row["available_time"],
            "price_observation_status": row.get("price_observation_status", "observed"),
            "missing_reason": row.get("missing_reason"),
            "momentum_12_1": _compound(history[-252:-21]),
            "momentum_6_1": _compound(history[-126:-21]),
            "momentum_3_1": _compound(history[-63:-21]),
            "momentum_12m_including_recent": _compound(history[-252:]),
            "short_term_reversal_1m": _compound(history[-21:]),
            "lookback_observations": len([item for item in history if item is not None]),
        })
        windows[instrument].append(row["total_return"])
    return result


def _compound(values: Iterable[Decimal | None]) -> Decimal | None:
    material = [item for item in values if item is not None]
    if not material:
        return None
    total = Decimal("1")
    for item in material:
        total *= Decimal("1") + item
    return total - Decimal("1")


def _write_parquet(path: Path, rows: list[dict[str, object]]) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("US equity momentum builder requires the 'data' optional dependency") from error
    pq.write_table(pa.Table.from_pylist(rows), path, compression="zstd", use_dictionary=True)


def _hash(value: object) -> str:
    return sha256(json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _policy_payload(policy: UsEquityMomentumPolicy) -> dict[str, object]:
    return {
        "policy_id": policy.policy_id,
        "version": policy.version,
        "minimum_price": str(policy.minimum_price),
        "minimum_adv20": str(policy.minimum_adv20),
        "minimum_history": policy.minimum_history,
    }


def _instrument_value(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        inner = value.get("value") or value.get("instrument_id")
        return str(inner) if inner else None
    return None


def _event_date(value: object) -> date:
    if isinstance(value, dict):
        value = value.get("$datetime") or value.get("$date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        raise ValueError("corporate action event is missing date")
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def _optional_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    return _event_date(value)


def _decimal_value(value: object) -> Decimal:
    if isinstance(value, dict):
        value = value.get("$decimal")
    return Decimal(str(value))


def _jsonable(value: object):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (date, datetime, Decimal)):
        return value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    return value
