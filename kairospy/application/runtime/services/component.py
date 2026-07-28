from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from kairospy.application.runtime.protocol.events import RuntimeEnvelope
from kairospy.core.views import ViewSchema, ViewStore


class RuntimeComponent(Protocol):
    @property
    def key(self) -> str:
        ...

    @property
    def schema(self) -> ViewSchema:
        ...

    def on_event(self, event: RuntimeEnvelope) -> None:
        ...

    def view(self) -> object:
        ...


class RuntimeProjection(Protocol):
    @property
    def schemas(self) -> tuple[ViewSchema, ...]:
        ...

    def on_event(self, event: RuntimeEnvelope) -> None:
        ...

    def publish(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        ...


RuntimeViewPublisher = RuntimeComponent | RuntimeProjection


class RuntimeComponentProvider(Protocol):
    def runtime_components(self) -> tuple[RuntimeViewPublisher, ...]:
        ...


class RuntimeIntentProcessor(Protocol):
    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        ...


def provided_components(providers: Iterable[RuntimeComponentProvider]) -> tuple[RuntimeViewPublisher, ...]:
    components: list[RuntimeViewPublisher] = []
    for provider in providers:
        components.extend(provider.runtime_components())
    return tuple(components)


def register_components(views: ViewStore, components: Iterable[RuntimeViewPublisher]) -> None:
    for component in components:
        schemas = getattr(component, "schemas", None)
        if schemas is not None:
            for schema in schemas:
                _register_if_missing(views, schema)
        else:
            _register_if_missing(views, component.schema)


def _register_if_missing(views: ViewStore, schema: ViewSchema) -> None:
    if views.registry.get(schema.key) is not None:
        return
    views.register(schema)


def publish_components(
    views: ViewStore,
    components: Iterable[RuntimeViewPublisher],
    *,
    event: RuntimeEnvelope | None = None,
    as_of: datetime | None = None,
) -> None:
    if event is not None:
        as_of = event.time
    for component in components:
        publish = getattr(component, "publish", None)
        if publish is not None:
            publish(views, as_of=as_of)
        else:
            views.put_runtime(component.key, component.view(), as_of=as_of, available_time=as_of)


__all__ = [
    "RuntimeComponent",
    "RuntimeComponentProvider",
    "RuntimeIntentProcessor",
    "RuntimeProjection",
    "RuntimeViewPublisher",
    "publish_components",
    "provided_components",
    "register_components",
]
