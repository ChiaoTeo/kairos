from __future__ import annotations

from dataclasses import asdict, replace
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
        from kairospy.data.metadata import DatasetMetadataInference

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
        contract = metadata.to_contract()
        product_surface._write_historical_file(self.root, dataset_id, contract, source)
        return {
            "product": "data",
            "operation": "add",
            "dataset": dataset_id,
            "time": metadata.primary_time,
            "fields": list(metadata.field_names),
            "source_kind": metadata.source_kind,
            "historical": {
                "status": "ready_for_workspace",
                "ready_for": ["workspace"],
                "blocked_for": ["backtest"],
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
        from kairospy.data import (
            BuiltInDataProductRegistry, HistoricalDataRequest, default_builtin_protocol_registry,
        )

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
                from kairospy.data.bootstrap import configured_product_specs

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
        dataset_id = str(getattr(request_args, "as_dataset", None) or built_in.default_dataset_name)
        target_use = str(getattr(request_args, "for_use", None) or "workspace")

        protocols = default_builtin_protocol_registry(self.root, registry.list())
        adapter = protocols.historical(built_in.protocol_name)
        historical_request = HistoricalDataRequest(
            dataset_id,
            start=datetime.fromisoformat(request_args.start),
            end=datetime.fromisoformat(request_args.end),
            instruments=tuple(getattr(request_args, "instrument", ()) or ()),
            params={
                "provider": getattr(request_args, "provider", None),
                "venue": getattr(request_args, "venue", None),
                "refresh": bool(getattr(request_args, "refresh", False)),
            },
        )
        if not hasattr(adapter, "prepare"):
            raise TypeError(f"built-in protocol {built_in.protocol_name!r} does not support preparation")
        plan, release = adapter.prepare(historical_request, dry_run=bool(getattr(request_args, "dry_run", False)))
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
                "status": "ready" if release is not None or plan.complete else "needs_data",
                "ready_for": ["workspace"] if release is not None or plan.complete else [],
                "blocked_for": [] if release is not None or plan.complete else ["workspace", "backtest"],
                "issues": [] if plan.executable else ["no_available_provider_access"],
            },
            "plan": {
                "complete": plan.complete,
                "executable": plan.executable,
                "missing": [_time_range_payload(item) for item in plan.missing],
                "covered": [_time_range_payload(item) for item in plan.covered],
                "selected_source": asdict(plan.selected) if plan.selected is not None else None,
            },
        }

    def _use_configured_product(self, request_args) -> dict[str, object]:
        from kairospy.data.bootstrap import default_provider_registry, register_configured_products
        from kairospy.data.client import DatasetClient

        if not getattr(request_args, "start", None) or not getattr(request_args, "end", None):
            raise ValueError("--start and --end are required when using a configured historical Data Product")
        register_configured_products(self.root)
        providers = default_provider_registry(self.root)
        client = DatasetClient(self.root, providers=providers)
        product_key = str(request_args.key)
        product = client.catalog.product(product_key)
        start, end = datetime.fromisoformat(request_args.start), datetime.fromisoformat(request_args.end)
        provider = getattr(request_args, "provider", None)
        venue = getattr(request_args, "venue", None)
        plan = client.plan(product_key, start=start, end=end, provider=provider, venue=venue)
        instruments = tuple(getattr(request_args, "instrument", ()) or ())
        if instruments:
            connector = providers.get(plan.selected.provider, plan.logical_key) if plan.selected is not None else None
            if connector is not None:
                from dataclasses import replace
                from kairospy.data.acquisition_primitives import AcquisitionRequest

                request = AcquisitionRequest(plan.logical_key, plan.missing, plan.selected, instruments, base_release_id=plan.local_release_id)
                estimate = connector.estimate(request) if hasattr(connector, "estimate") else plan.estimate
                plan = replace(plan, estimate=estimate)
        release = None
        dataset_id = str(getattr(request_args, "as_dataset", None) or product_key)
        if not bool(getattr(request_args, "dry_run", False)):
            release = client.acquire(
                plan,
                instruments=instruments,
                refresh=bool(getattr(request_args, "refresh", False)),
                aliases=_dataset_aliases(dataset_id, product_key),
            )
        target_use = str(getattr(request_args, "for_use", None) or "workspace")
        selected = plan.selected
        return {
            "product": "data",
            "operation": "use",
            "dataset": dataset_id,
            "data_product": product_key,
            "title": product.title,
            "capability": "historical",
            "target_use": target_use,
            "time": product.primary_time,
            "provider": selected.provider if selected is not None else provider,
            "venue": selected.venue if selected is not None else venue,
            "historical": {
                "status": "ready" if release is not None or plan.complete else "needs_data",
                "ready_for": ["workspace"] if release is not None or plan.complete else [],
                "blocked_for": [] if release is not None or plan.complete else ["workspace", "backtest"],
                "issues": [] if plan.executable else ["no_available_provider_access"],
            },
            "plan": {
                "complete": plan.complete,
                "executable": plan.executable,
                "missing": [_time_range_payload(item) for item in plan.missing],
                "covered": [_time_range_payload(item) for item in plan.covered],
                "selected_source": asdict(plan.selected) if plan.selected is not None else None,
            },
        }


def _with_lake_root(args, root: Path):
    try:
        return replace(args, lake_root=root)
    except TypeError:
        setattr(args, "lake_root", root)
        return args


def _dataset_aliases(dataset_id: str, default_dataset_name: str) -> tuple[str, ...]:
    dataset = str(dataset_id).strip()
    if not dataset or dataset == default_dataset_name:
        return ()
    return (dataset,)


def _time_range_payload(value) -> dict[str, str]:
    return {"start": value.start.isoformat(), "end": value.end.isoformat()}
