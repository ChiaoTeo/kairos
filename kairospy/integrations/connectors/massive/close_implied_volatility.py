from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from kairospy.reference.contracts import OptionRight
from kairospy.analytics.pricing import PricingInput, PricingModel, implied_volatility
from kairospy.infrastructure.storage.data_lake import write_json


SECONDS_PER_YEAR = Decimal("31557600")


class OptionCloseImpliedVolatilityPipeline:
    def __init__(self, lake_root: str | Path) -> None:
        self.root = Path(lake_root)

    def prepare(
        self,
        dataset_id: str,
        option_dataset_id: str,
        equity_dataset_id: str,
        *,
        risk_free_rate: Decimal = Decimal("0.04"),
        dividend_yield: Decimal = Decimal("0.0003"),
    ) -> dict[str, object]:
        option_root = self.root / "curated/provider=massive" / f"dataset={option_dataset_id}"
        equity_root = _equity_dataset_root(self.root, equity_dataset_id)
        option_manifest = _read(option_root / "manifest.json")
        equity_manifest = _read(equity_root / "manifest.json")
        fingerprint = sha256(json.dumps({
            "option_hash": option_manifest["dataset_sha256"], "equity_hash": equity_manifest["content_sha256"],
            "risk_free_rate": str(risk_free_rate), "dividend_yield": str(dividend_yield), "version": 1,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        target = self.root / "features/provider=massive" / f"dataset={dataset_id}"
        manifest_path = target / "manifest.json"
        if manifest_path.exists():
            existing = _read(manifest_path)
            if existing.get("input_fingerprint") == fingerprint:
                return existing
            raise ValueError(f"dataset ID {dataset_id!r} already refers to different IV inputs")

        pa, pq = _pyarrow()
        equity_table = pq.read_table(equity_root / equity_manifest["file"])
        underlying = {row["event_date"]: Decimal(str(row["close"])) for row in equity_table.to_pylist()}
        statuses: dict[str, int] = {}
        files = []
        total = converged = 0
        for source_file in (item for item in option_manifest["files"] if item.get("month")):
            source_path = option_root / source_file["path"]
            rows = []
            for row in pq.read_table(source_path).to_pylist():
                total += 1
                enriched = _iv_row(row, underlying.get(row["event_date"]), risk_free_rate, dividend_yield)
                status = str(enriched["solver_status"])
                statuses[status] = statuses.get(status, 0) + 1
                converged += status == "converged"
                rows.append(enriched)
            table = pa.Table.from_pylist(rows)
            month = str(source_file["month"])
            directory = target / f"year={month[:4]}" / f"month={month[5:]}"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"part-{fingerprint[:16]}-{month}.parquet"
            pq.write_table(table, path, compression="zstd", use_dictionary=True)
            files.append({"path": str(path.relative_to(target)), "month": month, "rows": len(rows), "bytes": path.stat().st_size, "sha256": _file_hash(path)})
        if total != int(option_manifest["rows"]):
            raise ValueError("option IV row reconciliation failed")
        quality = {
            "publishable": converged > 0, "input_rows": total, "output_rows": total,
            "converged_rows": converged, "iv_coverage": str(Decimal(converged) / Decimal(total or 1)),
            "status_counts": statuses,
        }
        if not quality["publishable"]:
            raise ValueError("option IV dataset has no converged observations")
        dataset_hash = sha256(json.dumps({"input_fingerprint": fingerprint, "files": files}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        manifest = {
            "manifest_version": 1, "dataset_id": dataset_id, "source": "internal.close_iv.black_scholes",
            "option_dataset_id": option_dataset_id, "equity_dataset_id": equity_dataset_id,
            "risk_free_rate": str(risk_free_rate), "dividend_yield": str(dividend_yield),
            "input_fingerprint": fingerprint, "rows": total, "converged_rows": converged,
            "files": files, "dataset_sha256": dataset_hash, "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        write_json(target / "lineage.json", {
            "option_dataset_id": option_dataset_id, "option_dataset_hash": option_manifest["dataset_sha256"],
            "equity_dataset_id": equity_dataset_id, "equity_dataset_hash": equity_manifest["content_sha256"],
            "model": "Black-Scholes European approximation for American NVDA options",
            "market_price": "adjusted OPRA Day Aggregates close", "underlying": "adjusted NVDA daily close",
            "risk_free_rate": str(risk_free_rate), "dividend_yield": str(dividend_yield),
            "visibility": "max(option and underlying available_time)",
        })
        write_json(target / "quality.json", quality)
        write_json(manifest_path, manifest)
        return manifest


def _iv_row(row: dict[str, object], underlying: Decimal | None, risk_free_rate: Decimal, dividend_yield: Decimal) -> dict[str, object]:
    output = dict(row)
    output.update({
        "underlying_close": float(underlying) if underlying is not None else None,
        "risk_free_rate": float(risk_free_rate), "dividend_yield": float(dividend_yield),
        "implied_volatility": None, "solver_status": None, "moneyness": None,
    })
    if underlying is None or underlying <= 0:
        output["solver_status"] = "missing_underlying"
        return output
    strike, market_price = Decimal(str(row["strike"])), Decimal(str(row["close"]))
    output["moneyness"] = float(strike / underlying)
    trading_day, expiry_day = row["event_date"], row["expiry"]
    valuation_at = datetime.combine(trading_day, time(16), ZoneInfo("America/New_York")).astimezone(timezone.utc)
    expires_at = datetime.combine(expiry_day, time(16), ZoneInfo("America/New_York")).astimezone(timezone.utc)
    maturity = Decimal(str((expires_at - valuation_at).total_seconds())) / SECONDS_PER_YEAR
    output["time_to_expiry_years"] = float(maturity)
    output["available_time"] = max(row["available_time"], valuation_at)
    if maturity <= 0:
        output["solver_status"] = "expired_at_close"
        return output
    if market_price <= 0:
        output["solver_status"] = "non_positive_close"
        return output
    right = OptionRight.CALL if row["right"] == "call" else OptionRight.PUT
    inputs = PricingInput(underlying, strike, maturity, risk_free_rate, Decimal("0.2"), right, dividend_yield)
    solved = implied_volatility(market_price, inputs, PricingModel.BLACK_SCHOLES)
    output["solver_status"] = solved.status.value
    output["implied_volatility"] = float(solved.volatility) if solved.volatility is not None else None
    return output


def _read(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _equity_dataset_root(root: Path, dataset_id: str) -> Path:
    legacy = root / "curated/provider=massive" / f"dataset={dataset_id}"
    if (legacy / "manifest.json").exists():
        return legacy
    matches = sorted(
        root.glob(
            "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/"
            f"interval=1d/view=*/dataset={dataset_id}"
        )
    )
    if matches:
        return matches[0]
    return legacy


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("option IV materialization requires the 'data' optional dependency") from error
    return pa, pq


__all__ = [
    "OptionCloseImpliedVolatilityPipeline",
]
