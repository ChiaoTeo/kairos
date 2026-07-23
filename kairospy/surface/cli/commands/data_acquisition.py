from __future__ import annotations

import argparse
from dataclasses import replace

from kairospy.data import DatasetClient
from kairospy.data.acquisition import AcquisitionLimits, AcquisitionRequest
from kairospy.infrastructure.storage.codec import to_primitive
from kairospy.surface.cli.prompts import prompt_text as _prompt_text

def prompt_acquire_args(args: argparse.Namespace, client: DatasetClient, providers) -> None:
    if args.dataset and args.start and args.end:
        return
    products = acquirable_product_rows(client, providers)
    if not products:
        raise SystemExit("no acquirable data products are registered")
    if args.dataset is None:
        print("Acquirable Data Products")
        for index, product in enumerate(products, start=1):
            print(f"  {index}. {product['logical_key']}  {product['title']}")
        selected = _prompt_text("Dataset number or logical key", "1").strip()
        if selected.isdigit() and 1 <= int(selected) <= len(products):
            args.dataset = str(products[int(selected) - 1]["logical_key"])
        else:
            args.dataset = selected
    if args.start is None:
        args.start = _prompt_text("Start [inclusive ISO-8601]", "")
    if args.end is None:
        args.end = _prompt_text("End [exclusive ISO-8601]", "")
    if not args.instrument:
        universe = _prompt_text("Universe [full-market or comma-separated instruments]", "full-market").strip()
        if universe and universe != "full-market":
            args.instrument = tuple(item.strip() for item in universe.split(",") if item.strip())

def acquirable_product_rows(client: DatasetClient, providers) -> list[dict[str, object]]:
    rows = []
    specs = getattr(providers, "_specs", {})
    for key, spec in sorted(specs.items()):
        product = spec.product
        rows.append({
            "logical_key": str(key),
            "title": product.title,
            "layer": product.layer.value,
            "dimensions": dict(product.dimensions),
            "primary_time": product.primary_time,
            "sources": to_primitive(product.sources),
            "releases": [to_primitive(release) for release in client.catalog.releases(product)],
        })
    return rows

def plan_with_cli_instruments(plan, providers, instruments: tuple[str, ...]):
    if not instruments or plan.selected is None or not plan.connector_available:
        return plan
    connector = providers.get(plan.selected.provider, plan.logical_key)
    request = AcquisitionRequest(
        plan.logical_key, plan.missing, plan.selected, instruments,
        base_release_id=plan.local_release_id,
    )
    estimate = connector.estimate(request) if hasattr(connector, "estimate") else plan.estimate
    return replace(plan, estimate=estimate)

def acquisition_plan_payload(plan, providers, instruments: tuple[str, ...]) -> dict[str, object]:
    payload = to_primitive(plan)
    if plan.selected is None or not plan.connector_available:
        return payload
    connector = providers.get(plan.selected.provider, plan.logical_key)
    task_plan = getattr(connector, "task_plan", None)
    if task_plan is None:
        return payload
    request = AcquisitionRequest(
        plan.logical_key, plan.missing, plan.selected, instruments,
        base_release_id=plan.local_release_id,
    )
    try:
        payload["provider_tasks"] = task_plan(request)
    except Exception as error:
        payload["provider_tasks"] = {"status": "unavailable", "error": f"{type(error).__name__}: {error}"}
    return payload

def acquisition_limits(args: argparse.Namespace) -> AcquisitionLimits:
    max_requests = int(getattr(args, "max_requests", 10_000))
    max_instruments = int(getattr(args, "max_instruments", 10_000))
    max_bytes = getattr(args, "max_bytes", None)
    if max_requests <= 0 or max_instruments <= 0 or max_bytes is not None and int(max_bytes) <= 0:
        raise SystemExit("acquisition limits must be positive")
    return AcquisitionLimits(maximum_requests=max_requests, maximum_instruments=max_instruments, maximum_bytes=max_bytes)

# Backward-compatible private aliases for tests and legacy imports.
_prompt_acquire_args = prompt_acquire_args
_acquirable_product_rows = acquirable_product_rows
_plan_with_cli_instruments = plan_with_cli_instruments
_acquisition_plan_payload = acquisition_plan_payload
_acquisition_limits = acquisition_limits
