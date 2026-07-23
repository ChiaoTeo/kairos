from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path


class HistoricalDataService:
    """Integration-owned historical Data Product service."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def add(self, args) -> dict[str, object]:
        from kairospy.data import HistoricalDataService as DataHistoricalDataService

        return DataHistoricalDataService(self.root).add(args)

    def use_builtin(self, args) -> dict[str, object]:
        from kairospy.data import DatasetStore
        from kairospy.integrations.data_products.catalog import BuiltInDataProductRegistry

        request_args = _with_lake_root(args, self.root)
        registry = BuiltInDataProductRegistry.from_default_products()
        if getattr(request_args, "list_products", False):
            return _product_list_payload(registry)

        try:
            built_in = registry.resolve(str(request_args.key))
        except KeyError as error:
            try:
                return self._use_configured_product(request_args)
            except KeyError:
                from kairospy.integrations.data_products.bootstrap import configured_product_specs

                configured_keys = tuple(str(spec.key) for spec in configured_product_specs())
            missing = KeyError(str(request_args.key))
            missing.operation = "use"
            missing.known_keys = tuple(item.key for item in registry.list()) + configured_keys
            missing.aliases = registry.aliases()
            raise missing from error
        if built_in.capability == "live":
            raise ValueError(f"integration-provided data product {built_in.key!r} is live-only and cannot be acquired as history")
        if not getattr(request_args, "start", None) or not getattr(request_args, "end", None):
            raise ValueError("--start and --end are required when using a integration-provided historical Data Product")
        from kairospy.integrations.data_products.catalog import built_in_dataset_id

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
                "integration-provided data products use canonical Dataset IDs; create an alias after using the product instead"
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
        provider_payload = self._use_dataset_writer_product(built_in, request_args, dry_run=dry_run, target_use=target_use)
        if provider_payload is not None:
            return provider_payload
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

    def _use_dataset_writer_product(self, built_in, request_args, *, dry_run: bool, target_use: str) -> dict[str, object] | None:
        from kairospy.integrations.acquisition import AcquisitionRequest, TimeRange
        from kairospy.integrations.data_products.bootstrap import default_provider_registry

        if not built_in.provider:
            return None
        providers = default_provider_registry(self.root)
        if not providers.available(str(built_in.provider), str(built_in.key)):
            return None
        connector = providers.get(str(built_in.provider), str(built_in.key))
        ingest_dataset = getattr(connector, "ingest_dataset", None)
        if not callable(ingest_dataset):
            return None
        spec = providers.product_spec(str(built_in.key))
        source = spec.product.sources[0]
        request = AcquisitionRequest(
            str(built_in.key),
            (TimeRange(_parse_datetime(request_args.start, "start"), _parse_datetime(request_args.end, "end")),),
            source,
            tuple(str(item) for item in getattr(request_args, "instrument", ()) or ()),
        )
        selection = {
            "start": request_args.start,
            "end": request_args.end,
            "instruments": list(request.instruments),
        }
        if dry_run:
            task_plan = getattr(connector, "task_plan", None)
            return {
                "product": "data",
                "operation": "use",
                "dataset": str(built_in.key),
                "data_product": str(built_in.key),
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
                **({"provider_tasks": task_plan(request)} if callable(task_plan) else {}),
            }
        result = ingest_dataset(request)
        return {
            "product": "data",
            "operation": "use",
            "dataset": str(result.get("dataset") or built_in.key),
            "data_product": str(built_in.key),
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
                "row_count": int(result.get("row_count") or 0),
            },
            "selection": selection,
            "ingestion": {
                key: value for key, value in dict(result).items()
                if key not in {"dataset", "row_count"}
            },
        }


def _with_lake_root(args, root: Path):
    try:
        return replace(args, lake_root=root)
    except TypeError:
        setattr(args, "lake_root", root)
        return args


def _hyperliquid_historical_rows(built_in, request_args) -> list[dict[str, object]]:
    from kairospy.integrations.historical_market_data import hyperliquid_historical_rows

    return hyperliquid_historical_rows(built_in, request_args)


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


def _product_list_payload(registry) -> dict[str, object]:
    aliases = registry.aliases()
    aliases_by_target: dict[str, list[str]] = {}
    for alias, target in aliases.items():
        aliases_by_target.setdefault(target, []).append(alias)
    return {
        "product": "data",
        "operation": "product.list",
        "products": [
            _product_payload(item, aliases=aliases_by_target.get(item.key, ()))
            for item in registry.list()
        ],
    }


def _product_payload(item, *, aliases: tuple[str, ...] | list[str] = ()) -> dict[str, object]:
    payload = {
        "key": item.key,
        "title": item.title,
        "capability": item.capability,
        "requires_account": item.requires_account,
        "default_dataset_name": item.default_dataset_name,
        "primary_time": item.primary_time,
        "provider": item.provider,
        "venue": item.venue,
    }
    if aliases:
        payload["aliases"] = sorted(aliases)
    return payload
