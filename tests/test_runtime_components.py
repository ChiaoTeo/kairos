from __future__ import annotations

from kairospy.application.launch.host.resources import TradingRuntimeResources
from kairospy.application.runtime.components import RuntimeComponents


def test_runtime_resources_expose_composed_components() -> None:
    components = RuntimeComponents()
    resources = TradingRuntimeResources(components=components)

    assert resources.runtime_components() is components


def test_legacy_resource_fields_are_normalized_at_the_composition_boundary() -> None:
    data = object()
    account = object()
    execution = object()
    reference = object()
    resources = TradingRuntimeResources(
        data=data,  # type: ignore[arg-type]
        account=account,  # type: ignore[arg-type]
        trading_execution=execution,  # type: ignore[arg-type]
        reference=reference,  # type: ignore[arg-type]
    )

    components = resources.runtime_components()

    assert components.market is data
    assert components.account is account
    assert components.execution is execution
    assert components.reference is reference
