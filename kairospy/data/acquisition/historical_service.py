from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path


class HistoricalDataService:
    """User-facing historical Data service.

    This service is the stable boundary for historical Dataset onboarding.
    The implementation currently delegates to the product-surface pipeline so
    CLI, notebook API, and DatasetClient share identical behavior.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def add(self, args) -> dict[str, object]:
        from kairospy.surface import product as product_surface
        from kairospy.data.storage.metadata import DatasetMetadataInference
        from kairospy.data import DatasetStore, DatasetWriter

        request = _with_lake_root(args, self.root)
        source = (
            product_surface._materialize_historical_protocol(request)
            if product_surface._is_historical_protocol_add(request)
            else Path(request.source)
        )
        dataset_id = str(request.name)
        if not product_surface._is_historical_protocol_add(request):
            product_surface._validate_data_add_file_source(source, dataset_id)
        metadata = DatasetMetadataInference().infer_file(
            source,
            dataset_id=dataset_id,
            time_field=getattr(request, "time", None),
            source_kind="user_defined",
        )
        store = DatasetStore(self.root)
        store.ensure_dataset(dataset_id, metadata={
            "primary_time": metadata.primary_time,
            "fields": list(metadata.field_names),
            "source": metadata.source_summary or {},
        })
        DatasetWriter(store).append(dataset_id, source)
        return {
            "product": "data",
            "operation": "add",
            "dataset": dataset_id,
            "time": metadata.primary_time,
            "fields": list(metadata.field_names),
            "source_kind": metadata.source_kind,
            "historical": {
                "status": "ready",
                "ready_for": ["read"],
                "blocked_for": [],
                "issues": [],
            },
            "live": {
                "status": "not_configured",
                "ready_for": [],
                "blocked_for": ["shadow", "paper", "live"],
                "issues": [],
            },
        }

    def use_builtin(self, args) -> dict[str, object]:
        from kairospy.surface import product as product_surface
        from kairospy.data import BuiltInDataProductRegistry, DatasetStore

        request_args = _with_lake_root(args, self.root)
        registry = BuiltInDataProductRegistry.from_default_products()
        if getattr(request_args, "list_products", False):
            return product_surface.data_product_list(request_args)

        try:
            built_in = registry.resolve(str(request_args.key))
        except KeyError as error:
            try:
                return self._use_configured_product(request_args)
            except KeyError:
                from kairospy.data.extensions.bootstrap import configured_product_specs

                configured_keys = tuple(str(spec.key) for spec in configured_product_specs())
            raise product_surface.DataProductNotFoundError(
                str(request_args.key),
                known_keys=tuple(item.key for item in registry.list()) + configured_keys,
                aliases=registry.aliases(),
            ) from error
        if built_in.capability == "live":
            raise ValueError(f"built-in data product {built_in.key!r} is live-only and cannot be acquired as history")
        if not getattr(request_args, "start", None) or not getattr(request_args, "end", None):
            raise ValueError("--start and --end are required when using a built-in historical data product")
        from kairospy.data import built_in_dataset_id

        dataset_id = built_in_dataset_id(
            built_in,
            instruments=tuple(getattr(request_args, "instrument", ()) or ()),
            params={
                "provider": getattr(request_args, "provider", None),
                "venue": getattr(request_args, "venue", None),
                "refresh": bool(getattr(request_args, "refresh", False)),
            },
        )
        requested_dataset = str(getattr(request_args, "as_dataset", None) or "").strip()
        if requested_dataset and requested_dataset != dataset_id:
            raise ValueError(
                "built-in data products use canonical Dataset IDs; create an alias after using the product instead"
            )
        target_use = str(getattr(request_args, "for_use", None) or "workspace")

        DatasetStore(self.root).ensure_dataset(dataset_id, metadata={
            "primary_time": built_in.primary_time,
            "data_product": built_in.key,
            "provider": built_in.provider,
            "venue": built_in.venue,
        })
        dry_run = bool(getattr(request_args, "dry_run", False))
        if built_in.provider == "hyperliquid":
            selection = {
                "start": request_args.start,
                "end": request_args.end,
                "instruments": list(getattr(request_args, "instrument", ()) or ()),
            }
            if dry_run:
                return {
                    "product": "data",
                    "operation": "use",
                    "dataset": dataset_id,
                    "data_product": built_in.key,
                    "default_dataset": built_in.default_dataset_name,
                    "title": built_in.title,
                    "source_kind": built_in.source_kind,
                    "capability": built_in.capability,
                    "target_use": target_use,
                    "time": built_in.primary_time,
                    "requires_account": built_in.requires_account,
                    "provider": built_in.provider,
                    "venue": built_in.venue,
                    "historical": {
                        "status": "planned",
                        "ready_for": [],
                        "blocked_for": [],
                        "issues": [],
                    },
                    "selection": selection,
                }
            rows = _hyperliquid_historical_rows(built_in, request_args)
            if rows:
                from kairospy.data import DatasetWriter

                DatasetWriter(self.root).append(
                    dataset_id,
                    rows,
                    partition_by=("event_day",),
                    time_field=built_in.primary_time,
                )
            fields = list(rows[0].keys()) if rows else []
            DatasetStore(self.root).ensure_dataset(dataset_id, metadata={
                "primary_time": built_in.primary_time,
                "fields": fields,
                "data_product": built_in.key,
                "provider": built_in.provider,
                "venue": built_in.venue,
                "source": {"source_kind": built_in.source_kind, "provider": built_in.provider},
            })
            return {
                "product": "data",
                "operation": "use",
                "dataset": dataset_id,
                "data_product": built_in.key,
                "default_dataset": built_in.default_dataset_name,
                "title": built_in.title,
                "source_kind": built_in.source_kind,
                "capability": built_in.capability,
                "target_use": target_use,
                "time": built_in.primary_time,
                "requires_account": built_in.requires_account,
                "provider": built_in.provider,
                "venue": built_in.venue,
                "historical": {
                    "status": "ready",
                    "ready_for": ["read"],
                    "blocked_for": [],
                    "issues": [],
                    "row_count": len(rows),
                },
                "selection": selection,
            }
        return {
            "product": "data",
            "operation": "use",
            "dataset": dataset_id,
            "data_product": built_in.key,
            "default_dataset": built_in.default_dataset_name,
            "title": built_in.title,
            "source_kind": built_in.source_kind,
            "capability": built_in.capability,
            "target_use": target_use,
            "time": built_in.primary_time,
            "requires_account": built_in.requires_account,
            "provider": built_in.provider,
            "venue": built_in.venue,
            "historical": {
                "status": "planned" if dry_run else "needs_connector",
                "ready_for": [],
                "blocked_for": [],
                "issues": ["provider_writer_not_migrated"] if not dry_run else [],
            },
            "selection": {
                "start": request_args.start,
                "end": request_args.end,
                "instruments": list(getattr(request_args, "instrument", ()) or ()),
            }
        }

    def _use_configured_product(self, request_args) -> dict[str, object]:
        raise KeyError(str(request_args.key))


def _with_lake_root(args, root: Path):
    try:
        return replace(args, lake_root=root)
    except TypeError:
        setattr(args, "lake_root", root)
        return args


def _hyperliquid_historical_rows(built_in, request_args) -> list[dict[str, object]]:
    from kairospy.integrations.connectors.hyperliquid import (
        HyperliquidInfoClient, hyperliquid_funding_rows, hyperliquid_ohlcv_rows,
    )

    start = _parse_datetime(request_args.start, "start")
    end = _parse_datetime(request_args.end, "end")
    instruments = tuple(str(item) for item in getattr(request_args, "instrument", ()) or ())
    if not instruments:
        raise ValueError("Hyperliquid historical products require --instrument <coin>")
    client = getattr(request_args, "client", None) or HyperliquidInfoClient()
    if "ohlcv" in built_in.protocol_name:
        interval = "1m" if built_in.key.endswith(".1m") else "1h"
        return hyperliquid_ohlcv_rows(client, coins=instruments, interval=interval, start=start, end=end)
    if "funding" in built_in.protocol_name:
        return hyperliquid_funding_rows(client, coins=instruments, start=start, end=end)
    raise ValueError(f"unsupported Hyperliquid historical product: {built_in.key}")


def _parse_datetime(value: object, label: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return result


def _dataset_aliases(dataset_id: str, default_dataset_name: str) -> tuple[str, ...]:
    dataset = str(dataset_id).strip()
    if not dataset or dataset == default_dataset_name:
        return ()
    return (dataset,)


def _time_range_payload(value) -> dict[str, str]:
    return {"start": value.start.isoformat(), "end": value.end.isoformat()}
