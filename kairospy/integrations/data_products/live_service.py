from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


class LiveDataService:
    """User-facing live Data service.

    Live Dataset onboarding and reconnects use this boundary so built-in and
    user-defined LiveDataProtocol connectors share the same readiness pipeline.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def connect(self, args) -> dict[str, object]:
        from kairospy.data import (
            DatasetStore, LiveDataRequest,
        )
        from kairospy.integrations.data_products.catalog import (
            BuiltInDataProductRegistry,
            built_in_dataset_id,
            default_builtin_protocol_registry,
        )

        request_args = _with_lake_root(args, self.root)
        source = Path(request_args.source)
        target_use = str(getattr(request_args, "for_use", None) or "shadow")
        built_in = None
        protocol_name = None
        provider = None
        venue = None
        source_kind = "user_defined"
        if source.exists():
            dataset_id = str(getattr(request_args, "as_dataset", None) or "").strip()
            if not dataset_id:
                raise ValueError("user-defined live data sources require --as")
            connector_hash = sha256(source.read_bytes()).hexdigest()
            module = _load_user_module(source, f"kairospy_user_live_data_{connector_hash[:12]}")
            _live_protocol_object(module)
            source_name = source.name
            source_path = str(source.resolve())
            primary_time = str(getattr(request_args, "time", None) or "timestamp")
            runtime_config = {}
        else:
            registry = BuiltInDataProductRegistry.from_default_products()
            try:
                built_in = registry.resolve(str(request_args.source))
            except KeyError as error:
                missing = KeyError(str(request_args.source))
                missing.operation = "connect"
                missing.known_keys = tuple(item.key for item in registry.list())
                missing.aliases = registry.aliases()
                raise missing from error
            if built_in.capability not in {"live", "both"}:
                raise ValueError(f"integration-provided data product {built_in.key!r} is not a live source")
            protocols = default_builtin_protocol_registry(self.root, registry.list())
            protocol = protocols.live(built_in.protocol_name)
            dataset_id = built_in_dataset_id(
                built_in,
                instruments=tuple(getattr(request_args, "instrument", ()) or ()),
                params=_live_source_params(request_args),
            )
            requested_dataset = str(getattr(request_args, "as_dataset", None) or "").strip()
            if requested_dataset and requested_dataset != dataset_id:
                raise ValueError(
                    "integration-provided data products use canonical Dataset IDs; create an alias after connecting instead"
                )
            connector_hash = sha256(built_in.protocol_name.encode("utf-8")).hexdigest()
            protocol_name = built_in.protocol_name
            provider = built_in.provider
            venue = built_in.venue
            source_kind = built_in.source_kind
            source_name = built_in.key
            source_path = None
            requested_time = str(getattr(request_args, "time", None) or "")
            primary_time = built_in.primary_time if requested_time in {"", "timestamp"} else requested_time
            runtime_config = _live_runtime_config(protocol, LiveDataRequest(
                dataset_id,
                account=getattr(request_args, "account", None),
                instruments=tuple(getattr(request_args, "instrument", ()) or ()),
                channel=getattr(request_args, "channel", None),
                params=_live_source_params(request_args),
            ))
        freshness_seconds = float(getattr(request_args, "freshness_seconds", 5.0))
        if freshness_seconds <= 0:
            raise ValueError("data connect freshness-seconds must be positive")
        fields = (primary_time,)
        live_data_plane = {
            "transport": "connector",
            "protocol": "LiveDataProtocol",
            "protocol_name": protocol_name,
            "account": getattr(request_args, "account", None),
            "channel": getattr(request_args, "channel", None),
            "instruments": list(getattr(request_args, "instrument", ()) or ()),
            "freshness": {"max_age_seconds": freshness_seconds},
            "target_use": target_use,
            "provider": provider,
            "venue": venue,
        } | dict(runtime_config)
        store = DatasetStore(self.root)
        contract = {
            "dataset_id": dataset_id,
            "primary_time": primary_time,
            "fields": list(fields),
            "source": source_name,
            "live_data_plane": live_data_plane,
        }
        store.ensure_dataset(dataset_id, metadata={
            "primary_time": primary_time,
            "fields": list(fields),
            "data_product": built_in.key if built_in is not None else None,
            "provider": provider,
            "venue": venue,
        })
        live_path = store.live_path(dataset_id) / "default"
        live_path.mkdir(parents=True, exist_ok=True)
        state = {
            "dataset": dataset_id,
            "status": "configured",
            "primary_time": primary_time,
            "fields": list(fields),
            "source": {"kind": "live_protocol", "name": source_name, "source_kind": source_kind}
            | ({"path": source_path} if source_path is not None else {})
            | (
                {"provider": provider, "venue": venue, "protocol_name": protocol_name}
                | _runtime_source_fields(runtime_config)
                if built_in is not None else {}
            ),
            "live_data_plane": live_data_plane,
            "configured_at": _now(),
        }
        (live_path / "state.json").write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        from kairospy.data.contracts import LiveViewManifest, stable_artifact_hash
        from kairospy.data.quality.freshness import live_view_manifest_path, write_live_view_manifest

        manifest = LiveViewManifest(
            dataset_id,
            "default",
            stable_artifact_hash(contract),
            connector_hash,
            primary_time,
            tuple(fields),
            live_data_plane,
            state["source"],
            "configured",
            state["configured_at"],
        )
        manifest_path = live_view_manifest_path(self.root, dataset_id, "default")
        write_live_view_manifest(manifest_path, manifest)
        return {
            "product": "data",
            "operation": "connect",
            "dataset": dataset_id,
            "target_use": target_use,
            "source_kind": source_kind,
            **({"provider": provider, "venue": venue} if built_in is not None else {}),
            **({"runtime": _runtime_summary(runtime_config)} if runtime_config else {}),
            "time": primary_time,
            "historical": {
                "status": "not_configured",
                "ready_for": [],
                "blocked_for": [],
                "issues": [],
            },
            "live": {
                "status": "configured",
                "ready_for": ["live"],
                "blocked_for": [],
                "issues": [],
                "view": "default",
                "manifest": str(manifest_path),
            },
        }

    def reconnect(self, args) -> dict[str, object]:
        request_args = _with_lake_root(args, self.root)
        from kairospy.data import DatasetStore

        state_path = DatasetStore(self.root).live_path(str(request_args.dataset)) / "default" / "state.json"
        if not state_path.exists():
            missing = KeyError(str(request_args.dataset))
            missing.dataset_not_configured = True
            raise missing
        state = json.loads(state_path.read_text(encoding="utf-8"))
        source = dict(state.get("source") or {})
        plane = dict(state.get("live_data_plane") or {})
        source_kind = str(source.get("source_kind") or "user_defined")
        if source_kind == "built_in":
            source_value = str(source.get("name") or "")
        else:
            source_value = str(source.get("path") or "")
            if not source_value:
                raise ValueError(
                    f"Dataset {request_args.dataset!r} was configured before connector path tracking; run data connect again"
                )
        instruments = (
            tuple(getattr(request_args, "instrument", ()) or ())
            or tuple(str(item) for item in plane.get("instruments", ()) or ())
        )
        freshness = plane.get("freshness") if isinstance(plane.get("freshness"), dict) else {}
        reconnect_args = _args(
            self.root,
            source=Path(source_value),
            as_dataset=str(request_args.dataset),
            time=str(state.get("primary_time") or "timestamp"),
            account=(
                getattr(request_args, "account", None)
                if getattr(request_args, "account", None) is not None
                else plane.get("account")
            ),
            channel=(
                getattr(request_args, "channel", None)
                if getattr(request_args, "channel", None) is not None
                else plane.get("channel")
            ),
            instrument=list(instruments),
            freshness_seconds=(
                getattr(request_args, "freshness_seconds", None)
                if getattr(request_args, "freshness_seconds", None) is not None
                else float(freshness.get("max_age_seconds", 5.0))
            ),
            for_use=str(plane.get("target_use") or "shadow"),
            market=(
                getattr(request_args, "market", None)
                if getattr(request_args, "market", None) is not None
                else str(plane.get("market") or "spot")
            ),
            levels=(
                getattr(request_args, "levels", None)
                if getattr(request_args, "levels", None) is not None
                else plane.get("levels")
            ),
            interval=(
                getattr(request_args, "interval", None)
                if getattr(request_args, "interval", None) is not None
                else plane.get("interval")
            ),
        )
        payload = self.connect(reconnect_args)
        reused_configuration = {
            "source_kind": source_kind,
            "account": reconnect_args.account,
            "channel": reconnect_args.channel,
            "instruments": list(instruments),
        }
        if source_kind == "built_in":
            reused_configuration["source"] = source_value
        return {
            **payload,
            "operation": "reconnect",
            "reused_configuration": reused_configuration,
        }


def _with_lake_root(args, root: Path):
    try:
        return replace(args, lake_root=root)
    except TypeError:
        setattr(args, "lake_root", root)
        return args


def _args(root: Path, **values: object) -> SimpleNamespace:
    values.setdefault("lake_root", root)
    return SimpleNamespace(**values)


def _load_user_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Python module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _live_protocol_object(module):
    for name in ("PROTOCOL", "protocol", "SOURCE", "source", "ADAPTER", "adapter"):
        protocol = getattr(module, name, None)
        if protocol is not None and hasattr(protocol, "stream") and callable(protocol.stream):
            return protocol
    factory = getattr(module, "get_protocol", None)
    if callable(factory):
        protocol = factory()
        if hasattr(protocol, "stream") and callable(protocol.stream):
            return protocol
    legacy_factory = getattr(module, "get_adapter", None)
    if callable(legacy_factory):
        protocol = legacy_factory()
        if hasattr(protocol, "stream") and callable(protocol.stream):
            return protocol
    stream = getattr(module, "stream", None)
    if callable(stream):
        class _FunctionProtocol:
            async def stream(self, request):
                async for item in stream(request):
                    yield item
        return _FunctionProtocol()
    raise ValueError("live protocol module must define stream(request), PROTOCOL.stream(request), or get_protocol().stream(request)")


def _live_runtime_config(protocol, request) -> dict[str, object]:
    configured = _configured_live_runtime_config(protocol, request)
    if configured is not None:
        return configured
    integration_runtime = _integration_live_runtime_config(protocol, request)
    if integration_runtime is not None:
        return integration_runtime
    runtime_config = getattr(protocol, "runtime_config", None)
    if not callable(runtime_config):
        return {}
    value = runtime_config(request)
    if value is None:
        return {}
    if not isinstance(value, dict):
        return dict(value)
    return value


def _integration_live_runtime_config(protocol, request) -> dict[str, object] | None:
    product = getattr(protocol, "product", None)
    if product is None:
        return None
    from kairospy.integrations.data_products.live_runtime import provider_live_runtime_config

    value = provider_live_runtime_config(product, request)
    if value is None:
        return None
    return dict(value)


def _configured_live_runtime_config(protocol, request) -> dict[str, object] | None:
    product = getattr(protocol, "product", None)
    provider = getattr(product, "provider", None)
    root = getattr(protocol, "root", None)
    if root is None or not provider:
        return None
    try:
        from kairospy.infrastructure.configuration import ConfigError, KairosProjectConfig
        from kairospy.integrations.live_market_data import (
            LiveMarketDataRequest,
            resolve_live_market_data_source_config,
        )

        config = KairosProjectConfig.discover(root)
        source_config = resolve_live_market_data_source_config(
            config,
            LiveMarketDataRequest(
                request.dataset_id,
                str(getattr(product, "key", "")),
                str(provider),
                tuple(request.instruments),
                request.channel,
                request.params,
            ),
        )
    except ConfigError:
        return None
    if source_config is None:
        return None
    return source_config.runtime_config


def _live_source_params(args) -> dict[str, object]:
    return {
        key: value
        for key, value in {
            "market": getattr(args, "market", None),
            "levels": getattr(args, "levels", None),
            "interval": getattr(args, "interval", None),
        }.items()
        if value is not None
    }


def _runtime_source_fields(runtime_config: dict[str, object]) -> dict[str, object]:
    return {
        key: runtime_config[key]
        for key in (
            "symbol",
            "channel",
            "stream",
            "instrument_id",
            "market",
            "levels",
            "interval",
            "public_only",
            "futures",
            "source_instance",
            "maximum_reconnects",
            "channel_capacity",
        )
        if key in runtime_config
    }


def _runtime_summary(runtime_config: dict[str, object]) -> dict[str, object]:
    return {
        key: runtime_config[key]
        for key in (
            "provider", "venue", "market", "symbol", "channel", "levels", "interval",
            "stream", "instrument_id", "public_only", "futures",
        )
        if key in runtime_config
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
