from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_global_connector_capability_model_is_not_reintroduced() -> None:
    forbidden_tokens = (
        "ConnectorCapability",
        "ConnectorCapabilities",
        "ConnectorCapabilityModel",
        "PortCapability",
        "PortCapabilities",
        "CapabilityGraph",
    )
    violations: list[str] = []

    for path in sorted((ROOT / "kairospy").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            if token in text:
                violations.append(f"{path.relative_to(ROOT)}: {token}")

    assert violations == [], "connector capability model must not be reintroduced:\n" + "\n".join(violations)


def test_capability_like_support_models_stay_with_their_product_owners() -> None:
    from kairospy.data.products import capabilities_payload
    from kairospy.execution.orders import ExecutionCapabilities
    from kairospy.market.subscriptions import MarketDataCapabilities
    from kairospy.reference.contracts import ReferenceCapabilities

    assert capabilities_payload.__module__ == "kairospy.data.products"
    assert ExecutionCapabilities.__module__ == "kairospy.execution.orders"
    assert MarketDataCapabilities.__module__ == "kairospy.market.subscriptions"
    assert ReferenceCapabilities.__module__ == "kairospy.reference.contracts"


def test_strategy_context_does_not_expose_connector_or_capability_views() -> None:
    from dataclasses import fields

    from kairospy.strategy.protocols import Context

    assert tuple(field.name for field in fields(Context)) == (
        "market",
        "portfolio",
        "features",
        "reference",
        "orders",
        "intents",
        "budget",
    )
