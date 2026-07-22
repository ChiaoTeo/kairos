from __future__ import annotations

from pathlib import Path

from kairospy.data import BuiltInDataProductRegistry
from kairospy.data.bootstrap import configured_product_specs, default_provider_registry


def providers_list(root: str | Path) -> dict[str, object]:
    rows = _provider_rows(root)
    return {
        "product": "providers",
        "operation": "list",
        "providers": [
            {
                "provider": row["provider"],
                "status": row["status"],
                "data_products": row["data_products"],
                "available_data_products": row["available_data_products"],
                "venues": row["venues"],
            }
            for row in rows
        ],
    }


def provider_doctor(
    root: str | Path,
    provider: str,
) -> dict[str, object]:
    provider_id = str(provider).strip().lower()
    rows = [row for row in _provider_rows(root) if row["provider"] == provider_id]
    if not rows:
        return {
            "product": "providers",
            "operation": "doctor",
            "provider": provider_id,
            "status": "unknown_provider",
            "issues": [{
                "code": "unknown_provider",
                "message": f"Provider {provider_id!r} is not known to built-in or configured Data Products.",
            }],
            "data_products": [],
        }
    row = rows[0]
    issues = []
    if row["status"] == "needs_configuration":
        issues.append({
            "code": "no_available_provider_access",
            "message": "Known Data Products exist, but provider access is not currently configured.",
        })
    elif row["status"] == "partial":
        issues.append({
            "code": "some_data_products_unavailable",
            "message": "Some known Data Products are not currently available from this Provider.",
        })
    return {
        "product": "providers",
        "operation": "doctor",
        "provider": provider_id,
        "status": row["status"],
        "venues": row["venues"],
        "data_products": row["products"],
        "issues": issues,
    }


def data_product_doctor(
    root: str | Path,
    product_key: str,
) -> dict[str, object]:
    requested_key = str(product_key).strip()
    rows = _provider_rows(root)
    products = [product for row in rows for product in row["products"]]
    product = _find_data_product(products, requested_key)
    if product is None:
        return {
            "product": "data",
            "operation": "products.doctor",
            "requested_key": requested_key,
            "status": "unknown_data_product",
            "issues": [{
                "code": "unknown_data_product",
                "message": f"Data Product {requested_key!r} is not known.",
            }],
            "next_commands": ["kairospy data products list"],
        }
    issues = _data_product_issues(product)
    payload = {
        "product": "data",
        "operation": "products.doctor",
        "requested_key": requested_key,
        "key": product["key"],
        "title": product["title"],
        "capability": product["capability"],
        "provider": product["provider"],
        "venue": product["venue"],
        "dataset": product["dataset"],
        "available": product["available"],
        "status": product["status"],
        "requires_account": product["requires_account"],
        "aliases": product.get("aliases", []),
        "issues": issues,
        "next_commands": _data_product_next_commands(product),
    }
    if requested_key != product["key"]:
        payload["resolved_key"] = product["key"]
    return payload


def _provider_rows(root: str | Path) -> list[dict[str, object]]:
    products = _provider_data_products()
    providers = default_provider_registry(root)
    grouped: dict[str, list[dict[str, object]]] = {}
    for product in products:
        provider = str(product.get("provider") or "").strip().lower()
        if not provider:
            continue
        row = dict(product)
        row["available"] = _provider_product_available(providers, row)
        row["status"] = "available" if row["available"] else "needs_configuration"
        grouped.setdefault(provider, []).append(row)
    result = []
    for provider, values in sorted(grouped.items()):
        venues = sorted({str(item.get("venue")) for item in values if item.get("venue")})
        available = sum(1 for item in values if item["available"])
        if available == len(values):
            status = "available"
        elif available:
            status = "partial"
        else:
            status = "needs_configuration"
        for item in values:
            if item["available"]:
                item["status"] = "available"
            elif status == "needs_configuration" and item.get("requires_account"):
                item["status"] = "needs_configuration"
            else:
                item["status"] = "not_available"
        result.append({
            "provider": provider,
            "status": status,
            "venues": venues,
            "data_products": len(values),
            "available_data_products": available,
            "products": sorted(values, key=lambda item: str(item.get("key") or "")),
        })
    return result


def _find_data_product(products: list[dict[str, object]], requested_key: str) -> dict[str, object] | None:
    for product in products:
        if str(product.get("key")) == requested_key:
            return product
    for product in products:
        aliases = product.get("aliases")
        if isinstance(aliases, list) and requested_key in {str(alias) for alias in aliases}:
            return product
    return None


def _data_product_issues(product: dict[str, object]) -> list[dict[str, object]]:
    status = str(product.get("status") or "")
    if status == "available":
        return []
    if status == "needs_configuration":
        return [{
            "code": "provider_access_not_configured",
            "message": "Configure provider access before using this Data Product.",
        }]
    return [{
        "code": "data_product_not_available",
        "message": "This Data Product is known, but no implementation is currently available.",
    }]


def _data_product_next_commands(product: dict[str, object]) -> list[str]:
    commands = [f"kairospy providers doctor {product['provider']}"]
    if product.get("available") and product.get("capability") == "historical":
        commands.append(f"kairospy data use {product['key']} --as {product['dataset']} --start <start> --end <end>")
    return commands


def _provider_data_products() -> list[dict[str, object]]:
    rows = []
    aliases_by_target: dict[str, list[str]] = {}
    registry = BuiltInDataProductRegistry.from_default_products()
    for alias, target in registry.aliases().items():
        aliases_by_target.setdefault(target, []).append(alias)
    for item in registry.list():
        rows.append({
            "key": item.key,
            "title": item.title,
            "capability": item.capability,
            "dataset": item.default_dataset_name,
            "provider": item.provider,
            "venue": item.venue,
            "requires_account": item.requires_account,
            "aliases": sorted(aliases_by_target.get(item.key, ())),
        })
    known = {str(item["key"]) for item in rows}
    for spec in configured_product_specs():
        source = spec.product.sources[0] if spec.product.sources else None
        key = str(spec.key)
        if key in known:
            continue
        rows.append({
            "key": key,
            "title": spec.product.title,
            "capability": "historical",
            "dataset": key,
            "provider": source.provider if source is not None else None,
            "venue": source.venue if source is not None else None,
            "requires_account": bool(source and source.provider in {"massive", "ibkr"}),
            "aliases": [],
        })
    return rows


def _provider_product_available(providers, product: dict[str, object]) -> bool:
    provider = str(product.get("provider") or "")
    key = str(product.get("key") or "")
    capability = str(product.get("capability") or "")
    if capability == "live":
        return provider in {"binance"} and not bool(product.get("requires_account"))
    return bool(provider and key and providers.available(provider, key))
