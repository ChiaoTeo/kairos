from __future__ import annotations

import tomllib

import pytest

from kairospy.investment.apps.market.application import (
    MarketProviderBindingApplication,
)
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.system.apps.integration.application import (
    ProviderConnectionConfigurationApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication


def test_market_binding_references_connection_without_copying_access_details(
    tmp_path,
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace")
    CredentialConfigurationApplication(workspace).configure(
        "okx-readonly",
        provider="okx",
        values={"api_key": "key", "api_secret": "secret", "passphrase": "pass"},
    )
    ProviderConnectionConfigurationApplication(workspace).configure(
        "okx-swap",
        provider="okx",
        credential_id="okx-readonly",
        products=("swap",),
        purposes=("market-query", "market-stream"),
    )

    binding = MarketProviderBindingApplication(workspace).bind_connection(
        "okx-swap", product="swap"
    )

    assert binding == {
        "type": "okx",
        "instrument_type": "swap",
        "enabled": True,
        "connection_id": "okx-swap",
    }
    manifest = tomllib.loads(workspace.paths.manifest.read_text())
    assert manifest["market"]["providers"] == [binding]
    assert "credential_id" not in workspace.paths.manifest.read_text()
    assert MarketProviderBindingApplication(workspace).references("okx-swap") == (
        "Market:swap",
    )
    with pytest.raises(ValueError, match="market.providers"):
        ProviderConnectionConfigurationApplication(workspace).delete("okx-swap")

    MarketProviderBindingApplication(workspace).unbind_connection("okx-swap")
    assert ProviderConnectionConfigurationApplication(workspace).delete("okx-swap") == {
        "connection_id": "okx-swap",
        "status": "deleted",
    }


def test_rebinding_one_product_preserves_other_market_providers(tmp_path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace")
    credentials = CredentialConfigurationApplication(workspace)
    connections = ProviderConnectionConfigurationApplication(workspace)
    credentials.configure(
        "binance-readonly",
        provider="binance",
        values={"api_key": "key", "api_secret": "secret"},
    )
    for product in ("spot", "usd-m-futures"):
        connections.configure(
            f"binance-{product}",
            provider="binance",
            credential_id="binance-readonly",
            products=(product,),
            purposes=("market-query", "market-stream"),
        )
    bindings = MarketProviderBindingApplication(workspace)
    bindings.bind_connection("binance-spot", product="spot")
    bindings.bind_connection("binance-usd-m-futures", product="usd-m-futures")
    bindings.bind_connection("binance-spot", product="spot", enabled=False)

    providers = tomllib.loads(workspace.paths.manifest.read_text())["market"][
        "providers"
    ]
    assert len(providers) == 2
    assert {value["connection_id"] for value in providers} == {
        "binance-spot",
        "binance-usd-m-futures",
    }
    spot = next(
        value for value in providers if value["connection_id"] == "binance-spot"
    )
    assert spot["enabled"] is False
