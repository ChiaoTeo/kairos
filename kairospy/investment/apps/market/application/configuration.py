"""Market-owned bindings from products to Integration provider connections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import re
import tomllib
from typing import Any

from kairospy.system.apps.configuration.services.transactions import (
    WorkspaceConfigurationTransaction,
)
from kairospy.system.apps.integration.application import (
    ProviderConnectionConfigurationApplication,
)
from kairospy.system.domain.workspace import Workspace


@dataclass(frozen=True, slots=True)
class MarketProviderBindingApplication:
    workspace: Workspace

    def bind_connection(
        self,
        connection_id: str,
        *,
        product: str,
        enabled: bool = True,
    ) -> dict[str, Any]:
        connection = ProviderConnectionConfigurationApplication(self.workspace).show(
            connection_id
        )
        provider = str(connection["provider"])
        product = product.strip().lower()
        if product not in connection.get("products", []):
            raise ValueError(
                f"provider connection {connection_id} does not enable product {product}"
            )
        binding = _binding(
            provider,
            product,
            connection_id=connection_id,
            enabled=enabled,
        )
        document = self.workspace.paths.manifest.read_text(encoding="utf-8")
        document = _replace_binding(document, provider, product, binding)
        transaction = WorkspaceConfigurationTransaction(
            self.workspace, f"market-binding:{connection_id}:{product}"
        )
        transaction.stage_text(self.workspace.paths.manifest, document)
        transaction.commit()
        return binding

    def references(self, connection_id: str) -> tuple[str, ...]:
        value = tomllib.loads(self.workspace.paths.manifest.read_text(encoding="utf-8"))
        market = value.get("market")
        providers = market.get("providers") if isinstance(market, Mapping) else None
        return tuple(
            f"Market:{_product(item)}"
            for item in (providers if isinstance(providers, list) else ())
            if isinstance(item, Mapping) and item.get("connection_id") == connection_id
        )

    def unbind_connection(self, connection_id: str) -> dict[str, object]:
        document = self.workspace.paths.manifest.read_text(encoding="utf-8")
        pattern = re.compile(r"(?ms)^\[\[market\.providers\]\]\s*\n.*?(?=^\[|\Z)")
        retained: list[str] = []
        position = 0
        removed = 0
        for match in pattern.finditer(document):
            retained.append(document[position : match.start()])
            block = match.group(0)
            try:
                item = tomllib.loads(block).get("market", {}).get("providers", [{}])[0]
            except (tomllib.TOMLDecodeError, AttributeError, IndexError):
                item = {}
            if isinstance(item, Mapping) and item.get("connection_id") == connection_id:
                removed += 1
            else:
                retained.append(block)
            position = match.end()
        retained.append(document[position:])
        if removed:
            transaction = WorkspaceConfigurationTransaction(
                self.workspace, f"market-unbind:{connection_id}"
            )
            transaction.stage_text(
                self.workspace.paths.manifest, "".join(retained).rstrip() + "\n"
            )
            transaction.commit()
        return {"connection_id": connection_id, "bindings_removed": removed}


def _binding(
    provider: str, product: str, *, connection_id: str, enabled: bool
) -> dict[str, Any]:
    common: dict[str, Any] = {
        "enabled": enabled,
        "connection_id": connection_id,
    }
    if provider == "massive":
        return {"type": "massive", "product": product, **common}
    if provider == "binance":
        if product == "spot":
            return {"type": "binance-spot", **common}
        if product == "equity":
            return {"type": "binance-equity", **common}
        return {
            "type": "binance-derivatives",
            "product": product,
            **common,
        }
    if provider == "okx":
        return {"type": "okx", "instrument_type": product, **common}
    raise ValueError(f"unsupported Market provider: {provider}")


def _replace_binding(
    document: str,
    provider: str,
    product: str,
    replacement: Mapping[str, Any],
) -> str:
    pattern = re.compile(r"(?ms)^\[\[market\.providers\]\]\s*\n.*?(?=^\[|\Z)")
    retained: list[str] = []
    position = 0
    for match in pattern.finditer(document):
        retained.append(document[position : match.start()])
        block = match.group(0)
        try:
            item = tomllib.loads(block).get("market", {}).get("providers", [{}])[0]
        except (tomllib.TOMLDecodeError, AttributeError, IndexError):
            item = {}
        if not (
            isinstance(item, Mapping)
            and _provider(item) == provider
            and _product(item) == product
        ):
            retained.append(block)
        position = match.end()
    retained.append(document[position:])
    result = "".join(retained).rstrip() + "\n\n[[market.providers]]\n"
    result += "\n".join(
        f"{key} = {_toml_value(value)}" for key, value in replacement.items()
    )
    return result + "\n"


def _provider(item: Mapping[str, object]) -> str:
    value = str(item.get("type") or "")
    return "binance" if value.startswith("binance-") else value


def _product(item: Mapping[str, object]) -> str:
    value = str(item.get("type") or "")
    if value == "binance-spot":
        return "spot"
    if value == "binance-equity":
        return "equity"
    return str(item.get("product") or item.get("instrument_type") or "")


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(str(value), ensure_ascii=False)


__all__ = ["MarketProviderBindingApplication"]
