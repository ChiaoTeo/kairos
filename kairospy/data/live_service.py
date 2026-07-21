from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path


class LiveDataService:
    """User-facing live Data service.

    Live Dataset onboarding and reconnects use this boundary so built-in and
    user-defined LiveDataProtocol connectors share the same readiness pipeline.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def connect(self, args) -> dict[str, object]:
        from kairospy import product_surface
        from kairospy.data import (
            BuiltInDataProductRegistry, LiveDataRequest, LiveViewManifest,
            default_builtin_protocol_registry, live_view_manifest_path,
            stable_artifact_hash, write_live_view_manifest,
        )

        request_args = _with_lake_root(args, self.root)
        source = Path(request_args.source)
        dataset_id = str(request_args.as_dataset)
        target_use = str(getattr(request_args, "for_use", None) or "shadow")
        built_in = None
        protocol_name = None
        provider = None
        venue = None
        source_kind = "user_defined"
        if source.exists():
            connector_hash = sha256(source.read_bytes()).hexdigest()
            module = product_surface._load_user_module(source, f"kairospy_user_live_data_{connector_hash[:12]}")
            product_surface._live_protocol_adapter(module)
            source_name = source.name
            source_path = str(source.resolve())
            primary_time = str(getattr(request_args, "time", None) or "timestamp")
            runtime_config = {}
        else:
            registry = BuiltInDataProductRegistry.from_default_products()
            try:
                built_in = registry.resolve(str(request_args.source))
            except KeyError as error:
                raise product_surface.DataProductNotFoundError(
                    str(request_args.source),
                    operation="connect",
                    known_keys=tuple(item.key for item in registry.list()),
                    aliases=registry.aliases(),
                ) from error
            if built_in.capability not in {"live", "both"}:
                raise ValueError(f"built-in data product {built_in.key!r} is not a live source")
            protocols = default_builtin_protocol_registry(self.root, registry.list())
            adapter = protocols.live(built_in.protocol_name)
            connector_hash = sha256(built_in.protocol_name.encode("utf-8")).hexdigest()
            protocol_name = built_in.protocol_name
            provider = built_in.provider
            venue = built_in.venue
            source_kind = built_in.source_kind
            source_name = built_in.key
            source_path = None
            requested_time = str(getattr(request_args, "time", None) or "")
            primary_time = built_in.primary_time if requested_time in {"", "timestamp"} else requested_time
            runtime_config = product_surface._live_runtime_config(adapter, LiveDataRequest(
                dataset_id,
                account=getattr(request_args, "account", None),
                instruments=tuple(getattr(request_args, "instrument", ()) or ()),
                channel=getattr(request_args, "channel", None),
                params=product_surface._live_source_params(request_args),
            ))
        freshness_seconds = float(getattr(request_args, "freshness_seconds", 5.0))
        if freshness_seconds <= 0:
            raise ValueError("data connect freshness-seconds must be positive")
        live_view_id = f"{dataset_id}:live:{connector_hash[:12]}"
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
        manifest = LiveViewManifest(
            dataset_id,
            live_view_id,
            stable_artifact_hash({
                "dataset_id": dataset_id,
                "primary_time": primary_time,
                "fields": list(fields),
                "source_kind": source_kind,
            }),
            connector_hash,
            primary_time,
            fields,
            live_data_plane,
            {"kind": "live_protocol", "name": source_name, "source_kind": source_kind}
            | ({"path": source_path} if source_path is not None else {})
            | (
                {"provider": provider, "venue": venue, "protocol_name": protocol_name}
                | product_surface._runtime_source_fields(runtime_config)
                if built_in is not None else {}
            ),
            "configured",
            product_surface._now(),
        )
        manifest_path = live_view_manifest_path(self.root, dataset_id, live_view_id)
        write_live_view_manifest(manifest_path, manifest)
        return {
            "product": "data",
            "operation": "connect",
            "dataset": dataset_id,
            "target_use": target_use,
            "source_kind": source_kind,
            **({"provider": provider, "venue": venue} if built_in is not None else {}),
            **({"runtime": product_surface._runtime_summary(runtime_config)} if runtime_config else {}),
            "time": primary_time,
            "historical": {
                "status": "not_configured",
                "ready_for": [],
                "blocked_for": ["study", "backtest"],
                "issues": [],
            },
            "live": {
                "status": "configured",
                "ready_for": ["shadow"],
                "blocked_for": ["paper", "live"],
                "issues": ["freshness_not_verified"],
            },
        }

    def reconnect(self, args) -> dict[str, object]:
        from kairospy import product_surface

        request_args = _with_lake_root(args, self.root)
        manifest = product_surface._latest_live_view_manifest(self.root, str(request_args.dataset))
        if manifest is None:
            raise product_surface.DataLiveDatasetNotConfiguredError(str(request_args.dataset))
        source = dict(manifest.source)
        plane = dict(manifest.live_data_plane)
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
        reconnect_args = product_surface._args(
            self.root,
            source=Path(source_value),
            as_dataset=str(request_args.dataset),
            time=manifest.primary_time,
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
