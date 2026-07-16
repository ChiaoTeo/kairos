from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path
from decimal import Decimal
from typing import Callable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from trading import __version__
from trading.backtest.calendar import TradingCalendar
from trading.storage.data_lake import write_json

from .client import MassiveClient, MassiveError


class OutsideDownloadWindow(RuntimeError):
    pass


def request_fingerprint(resource: str, params: Mapping[str, object]) -> str:
    safe = {key: value for key, value in params.items() if key.lower() not in {"apikey", "api_key", "token"}}
    encoded = json.dumps({"resource": resource, "params": safe}, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ArchivedRequest:
    fingerprint: str
    directory: Path
    receipt: dict[str, object]


class MassiveSourceArchive:
    def __init__(self, root: str | Path, client: MassiveClient, *, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.root, self.client, self.now = Path(root), client, now

    def fetch_pages(self, resource: str, params: Mapping[str, object], *, max_pages: int = 100_000) -> ArchivedRequest:
        fingerprint = request_fingerprint(resource, params)
        directory = self.root / "source" / "provider=massive" / f"resource={_safe(resource)}" / f"request_id={fingerprint}"
        receipt_path = directory / "receipt.json"
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("status") == "complete" and receipt.get("transport_scheme") == "https":
                return ArchivedRequest(fingerprint, directory, receipt)

        directory.mkdir(parents=True, exist_ok=True)
        for stale in directory.glob("page-*.json.gz"):
            stale.unlink()
        started = self.now()
        pages, request_ids, rows, total_bytes, retry_attempts, rate_limits = [], [], 0, 0, [], []
        try:
            for index, (response, payload) in enumerate(self.client.pages(resource, params, max_pages=max_pages)):
                target = directory / f"page-{index:05d}.json.gz"
                content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                temporary = target.with_suffix(target.suffix + ".tmp")
                with gzip.open(temporary, "wb") as handle:
                    handle.write(content)
                temporary.replace(target)
                pages.append({"path": target.name, "sha256": sha256(target.read_bytes()).hexdigest(), "bytes": target.stat().st_size})
                total_bytes += target.stat().st_size
                retry_attempts.append(response.attempts)
                rate_limits.append({key: value for key, value in response.headers.items() if "rate" in key.lower() or "limit" in key.lower()})
                results = payload.get("results")
                rows += len(results) if isinstance(results, list) else int(isinstance(results, dict))
                if payload.get("request_id"):
                    request_ids.append(str(payload["request_id"]))
        except Exception:
            # A fail-fast 4xx has no Source payload to preserve. Avoid leaving an
            # empty request directory that could be mistaken for an archived run.
            if directory.exists() and not any(directory.iterdir()):
                directory.rmdir()
                try:
                    directory.parent.rmdir()
                except OSError:
                    pass
            raise
        completed = self.now()
        safe_params = {key: value for key, value in params.items() if key.lower() not in {"apikey", "api_key", "token"}}
        receipt: dict[str, object] = {
            "receipt_version": 1, "provider": "massive", "api_host": "api.massiveprivateserver.site", "transport_scheme": "https",
            "producer": {"name": "trader-massive", "version": __version__},
            "resource": resource, "parameters": safe_params, "request_fingerprint": fingerprint,
            "requested_at": started.isoformat(), "completed_at": completed.isoformat(), "boundary": "[start,end)",
            "request_ids": request_ids, "page_count": len(pages), "record_count": rows,
            "response_bytes": total_bytes, "files": pages, "retry_attempts_by_page": retry_attempts,
            "rate_limit_headers_by_page": rate_limits, "status": "complete",
        }
        write_json(receipt_path, receipt)
        return ArchivedRequest(fingerprint, directory, receipt)

    @staticmethod
    def iter_results(archived: ArchivedRequest):
        for page in sorted(archived.directory.glob("page-*.json.gz")):
            with gzip.open(page, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            results = payload.get("results", [])
            if isinstance(results, list):
                yield from results
            elif isinstance(results, dict):
                yield results
            else:
                raise MassiveError(f"archived Massive page has invalid results: {page}")

    @staticmethod
    def quarantine_non_https(root: str | Path) -> tuple[Path, ...]:
        base = Path(root)
        source = base / "source" / "provider=massive"
        quarantine = base / "quarantine" / "provider=massive" / "invalid-source-transport"
        moved = []
        for directory in sorted(source.glob("resource=*/request_id=*")):
            receipt_path = directory / "receipt.json"
            valid = False
            if receipt_path.exists():
                try:
                    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                    valid = receipt.get("status") == "complete" and receipt.get("transport_scheme") == "https"
                except (OSError, json.JSONDecodeError):
                    valid = False
            if valid:
                continue
            relative = directory.relative_to(source)
            target = quarantine / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target = target.with_name(target.name + "-duplicate")
            directory.replace(target); moved.append(target)
        return tuple(moved)


class MassiveFlatFileClient:
    def __init__(self, root: str | Path, client: MassiveClient, *, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 stream_download: Callable[[str, Path], tuple[int, dict[str, str], int, str]] | None = None) -> None:
        self.root, self.client, self.now = Path(root), client, now
        self.stream_download = stream_download or self._stream_download

    def usage(self) -> dict[str, object]:
        value = self.client.get("/usage").json()
        if not isinstance(value, dict):
            raise MassiveError("Massive /usage must return an object")
        return value

    def cache_status(self, file_key: str) -> dict[str, object]:
        value = self.client.get("/cache/status", {"key": file_key}).json()
        if not isinstance(value, dict):
            raise MassiveError("Massive cache status must return an object")
        return value

    def download(self, file_key: str) -> Path:
        current = self.now()
        if current.tzinfo is None:
            raise ValueError("download clock must be timezone-aware")
        local = self.local_file(file_key)
        if local is not None:
            return local
        new_york = current.astimezone(ZoneInfo("America/New_York"))
        if new_york.weekday() < 5 and time(9, 30) <= new_york.time().replace(tzinfo=None) < time(16):
            raise OutsideDownloadWindow("Flat Files may only be downloaded outside 09:30-16:00 America/New_York")
        usage = self.usage()
        used = _usage_bytes(usage)
        server_limit = _usage_limit_bytes(usage)
        effective_limit = min(self.client.config.monthly_flat_file_limit_bytes, server_limit) if server_limit is not None else self.client.config.monthly_flat_file_limit_bytes
        if used >= effective_limit:
            raise MassiveError("monthly Massive Flat File quota is exhausted")
        directory = self.root / "source" / "provider=massive" / "resource=flat-files" / f"request_id={request_fingerprint(file_key, {})}"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / Path(file_key).name
        temporary = target.with_suffix(target.suffix + ".tmp")
        status, headers, downloaded_bytes, content_hash = self.stream_download(file_key, temporary)
        if status == 202:
            temporary.unlink(missing_ok=True)
            raise MassiveError("Flat File is being cached; retry later")
        if not 200 <= status < 300:
            temporary.unlink(missing_ok=True)
            raise MassiveError(f"Flat File download failed status={status}")
        temporary.replace(target)
        write_json(directory / "receipt.json", {
            "receipt_version": 1, "provider": "massive", "api_host": "api.massiveprivateserver.site", "transport_scheme": "https",
            "producer": {"name": "trader-massive", "version": __version__},
            "resource": "flat-files", "file_key": file_key, "downloaded_at": current.isoformat(),
            "bytes": downloaded_bytes, "sha256": content_hash,
            "monthly_usage_bytes_before_download": used, "status": "complete",
            "server_monthly_limit_bytes": server_limit, "effective_monthly_limit_bytes": effective_limit,
        })
        return target

    def local_file(self, file_key: str) -> Path | None:
        directory = self.root / "source" / "provider=massive" / "resource=flat-files" / f"request_id={request_fingerprint(file_key, {})}"
        target, receipt_path = directory / Path(file_key).name, directory / "receipt.json"
        if not target.exists() or not receipt_path.exists():
            return None
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if (receipt.get("status") != "complete" or receipt.get("file_key") != file_key
                or int(receipt.get("bytes", -1)) != target.stat().st_size):
            return None
        return target

    def _stream_download(self, file_key: str, target: Path) -> tuple[int, dict[str, str], int, str]:
        url = self.client._url("/download", {"key": file_key})
        request = Request(url, headers={"Authorization": f"Bearer {self.client.config.api_key}", "User-Agent": "trader-massive/1.0"})
        digest, total = sha256(), 0
        try:
            response = urlopen(request, timeout=self.client.config.timeout_seconds)
        except Exception as error:
            if getattr(error, "code", None) == 202:
                return 202, {}, 0, digest.hexdigest()
            raise
        with response, target.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk); digest.update(chunk); total += len(chunk)
            return response.status, dict(response.headers.items()), total, digest.hexdigest()


class MassiveFlatFileBatchDownloader:
    """Resumable, bounded downloader for daily OPRA day-aggregate files."""

    PREFIX = "us_options_opra/day_aggs_v1"

    def __init__(self, flat_files: MassiveFlatFileClient, *, calendar: TradingCalendar | None = None) -> None:
        self.flat_files = flat_files
        self.calendar = calendar or TradingCalendar()

    @classmethod
    def file_key(cls, trading_day: date) -> str:
        return f"{cls.PREFIX}/{trading_day:%Y/%m}/{trading_day:%Y-%m-%d}.csv.gz"

    def download_range(self, start: date, end: date, *, max_files: int = 5, dry_run: bool = False) -> dict[str, object]:
        if not start < end:
            raise ValueError("Flat File batch requires [start,end) with start < end")
        if max_files <= 0:
            raise ValueError("max_files must be positive")
        trading_days = self.calendar.trading_days_between(start, end - timedelta(days=1))
        items: list[dict[str, object]] = []
        attempted = 0
        for trading_day in trading_days:
            key = self.file_key(trading_day)
            local = self.flat_files.local_file(key)
            if local is not None:
                items.append({"date": trading_day.isoformat(), "key": key, "status": "already_downloaded", "path": str(local)})
                continue
            if attempted >= max_files:
                items.append({"date": trading_day.isoformat(), "key": key, "status": "deferred_by_batch_limit"})
                continue
            attempted += 1
            try:
                cache = self.flat_files.cache_status(key)
                if dry_run:
                    items.append({"date": trading_day.isoformat(), "key": key, "status": "planned", "cached": bool(cache.get("cached")), "downloading": bool(cache.get("downloading"))})
                elif cache.get("downloading") and not cache.get("cached"):
                    items.append({"date": trading_day.isoformat(), "key": key, "status": "caching"})
                else:
                    path = self.flat_files.download(key)
                    items.append({"date": trading_day.isoformat(), "key": key, "status": "downloaded", "path": str(path), "bytes": path.stat().st_size})
            except OutsideDownloadWindow:
                raise
            except MassiveError as error:
                status = "caching" if "being cached" in str(error) else "error"
                items.append({"date": trading_day.isoformat(), "key": key, "status": status, "error_type": type(error).__name__, "error": str(error)})
                if "quota is exhausted" in str(error):
                    break
        counts: dict[str, int] = {}
        for item in items:
            counts[str(item["status"])] = counts.get(str(item["status"]), 0) + 1
        report: dict[str, object] = {
            "provider": "massive", "resource": self.PREFIX, "boundary": "[start,end)",
            "start": start.isoformat(), "end": end.isoformat(), "trading_days": len(trading_days),
            "max_files": max_files, "dry_run": dry_run, "counts": counts, "items": items,
            "created_at": self.flat_files.now().isoformat(),
        }
        fingerprint = request_fingerprint(self.PREFIX, {"start": start.isoformat(), "end": end.isoformat(), "max_files": max_files, "dry_run": dry_run})
        content_hash = sha256(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        report_path = self.flat_files.root / "source" / "provider=massive" / "resource=flat-files" / "batches" / f"batch-{fingerprint}-{content_hash[:16]}.json"
        write_json(report_path, report)
        report["report_path"] = str(report_path)
        return report


def _usage_bytes(payload: Mapping[str, object]) -> int:
    for key in ("bytes_used", "used_bytes", "usage_bytes", "downloaded_bytes"):
        value = payload.get(key)
        if value is not None:
            return int(value)
    if payload.get("usedGB") is not None:
        return int(Decimal(str(payload["usedGB"])) * Decimal("1000000000"))
    raise MassiveError("Massive /usage response does not contain a recognized byte counter")


def _usage_limit_bytes(payload: Mapping[str, object]) -> int | None:
    for key in ("limit_bytes", "monthly_limit_bytes"):
        if payload.get(key) is not None:
            return int(payload[key])
    if payload.get("limitGB") is not None:
        return int(Decimal(str(payload["limitGB"])) * Decimal("1000000000"))
    return None


def _safe(value: str) -> str:
    return value.strip("/").replace("/", "_").replace("=", "_") or "root"
