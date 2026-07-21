from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from kairospy.application import RuntimeFeedPlan, RuntimeFeedServiceBundle, RuntimeFeedServicePlan
from kairospy.contracts import CanonicalEventEnvelope
from kairospy.data import (
    LiveViewFreshnessMonitor,
    live_view_freshness_evidence,
    live_view_manifest_path,
    load_live_view_manifest,
)
from kairospy.trading.identity import InstrumentId
from kairospy.market_data import BoundedEventChannel, RotatingCanonicalCaptureWriter
from kairospy.ports import Environment

from .market_stream import BinanceStreamSession, WebSocketClientConnector, WebSocketConnector, websocket_url
from .stream import BinanceCanonicalStreamService


@dataclass(frozen=True, slots=True)
class BinanceRuntimeFeed:
    runtime_bundle: RuntimeFeedServiceBundle
    connector_services: Mapping[str, BinanceCanonicalStreamService]
    channels: Mapping[str, BoundedEventChannel[CanonicalEventEnvelope]]
    manifest_paths: Mapping[str, Path]

    @property
    def managed_services(self):
        return self.runtime_bundle.services


class BinanceRuntimeFeedFactory:
    """Instantiate Binance feed runners from Live View manifests.

    The factory interprets provider-specific Live View source metadata, while
    the application layer stays limited to the provider-neutral feed plan.
    """

    def __init__(
        self,
        lake_root: str | Path,
        *,
        connector: WebSocketConnector | None = None,
        environment: Environment = Environment.LIVE,
        monitor_interval_seconds: float = 1.0,
        channel_capacity: int = 4096,
        journal_root: str | Path | None = None,
    ) -> None:
        if monitor_interval_seconds <= 0:
            raise ValueError("Binance runtime feed monitor interval must be positive")
        if channel_capacity < 1:
            raise ValueError("Binance runtime feed channel capacity must be positive")
        self.lake_root = Path(lake_root)
        self.connector = connector or WebSocketClientConnector()
        self.environment = environment
        self.monitor_interval_seconds = monitor_interval_seconds
        self.channel_capacity = channel_capacity
        self.journal_root = Path(journal_root) if journal_root is not None else (
            self.lake_root / "source" / "live" / "binance" / "runtime"
        )

    def build(self, plan: RuntimeFeedPlan) -> BinanceRuntimeFeed:
        connector_services: dict[str, BinanceCanonicalStreamService] = {}
        channels: dict[str, BoundedEventChannel[CanonicalEventEnvelope]] = {}
        manifest_paths: dict[str, Path] = {}
        monitors: dict[str, LiveViewFreshnessMonitor] = {}

        for service in plan.services:
            manifest_path = live_view_manifest_path(self.lake_root, service.dataset, service.live_view_id)
            manifest = load_live_view_manifest(manifest_path)
            config = _binance_stream_config(service, manifest.source, manifest.live_data_plane)
            capacity = int(config.get("channel_capacity") or self.channel_capacity)
            output: BoundedEventChannel[CanonicalEventEnvelope] = BoundedEventChannel(capacity)
            journal = self._journal_path(service, str(config["stream"]))
            canonical_path = journal.with_suffix(".canonical.jsonl")
            connector_service = BinanceCanonicalStreamService(
                BinanceStreamSession(
                    self.connector,
                    websocket_url(
                        self.environment,
                        str(config["stream"]),
                        futures=bool(config.get("futures")),
                        public_only=bool(config.get("public_only")),
                    ),
                    maximum_reconnects=int(config.get("maximum_reconnects") or 5),
                    journal=journal,
                ),
                {str(config["symbol"]): InstrumentId(str(config["instrument_id"]))},
                output,
                source_instance=str(config["source_instance"]),
                stream_id=str(config["stream"]),
                canonical_capture=RotatingCanonicalCaptureWriter(
                    canonical_path,
                    session_id=journal.stem,
                    source="binance",
                ),
            )
            connector_services[service.service_id] = connector_service
            channels[service.service_id] = output
            manifest_paths[service.service_id] = manifest_path
            monitors[service.service_id] = LiveViewFreshnessMonitor(
                manifest_path,
                lambda service=connector_service, output=output, stream=str(config["stream"]): live_view_freshness_evidence(
                    service,
                    output,
                    source="binance",
                    stream_id=stream,
                ),
                interval_seconds=self.monitor_interval_seconds,
            )

        runtime_bundle = plan.managed_service_bundle(
            feed_runner_factory=lambda service: connector_services[service.service_id].run,
            monitor_runner_factory=lambda service: monitors[service.service_id].run,
        )
        return BinanceRuntimeFeed(runtime_bundle, connector_services, channels, manifest_paths)

    def _journal_path(self, service: RuntimeFeedServicePlan, stream: str) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        name = f"{_safe_name(service.service_id)}-{_safe_name(stream)}-{stamp}.jsonl"
        return self.journal_root / name


def _binance_stream_config(
    service: RuntimeFeedServicePlan,
    source: Mapping[str, object],
    live_data_plane: Mapping[str, object],
) -> dict[str, object]:
    kind = str(source.get("kind") or "")
    provider = str(source.get("provider") or live_data_plane.get("provider") or "")
    if provider != "binance" and kind not in {"binance_market_stream", "binance_public_stream"}:
        raise ValueError(f"Live View {service.live_view_id!r} is not a Binance runtime stream")
    symbol = str(source.get("symbol") or live_data_plane.get("symbol") or "").upper()
    channel = str(source.get("channel") or live_data_plane.get("channel") or "")
    stream = str(source.get("stream") or live_data_plane.get("stream") or "")
    if not stream:
        if not symbol or not channel:
            raise ValueError(f"Binance Live View {service.live_view_id!r} requires symbol and channel or stream")
        stream = f"{symbol.lower()}@{channel}"
    if not symbol:
        symbol = stream.split("@", 1)[0].upper()
    instrument_id = str(
        source.get("instrument_id")
        or live_data_plane.get("instrument_id")
        or f"crypto:binance:{'futures' if _truthy(source.get('futures') or live_data_plane.get('futures')) else 'spot'}:{symbol}"
    )
    futures = _truthy(source.get("futures") or live_data_plane.get("futures"))
    return {
        "symbol": symbol,
        "channel": channel,
        "stream": stream,
        "instrument_id": instrument_id,
        "futures": futures,
        "public_only": _truthy(
            source.get("public_only")
            if "public_only" in source
            else live_data_plane.get("public_only", not futures)
        ),
        "source_instance": str(source.get("source_instance") or f"kairospy-runtime:{service.name}"),
        "maximum_reconnects": int(source.get("maximum_reconnects") or live_data_plane.get("maximum_reconnects") or 5),
        "channel_capacity": int(source.get("channel_capacity") or live_data_plane.get("channel_capacity") or 0),
    }


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in {"-", "_"} else "-" for character in value)


__all__ = [
    "BinanceRuntimeFeed",
    "BinanceRuntimeFeedFactory",
]
