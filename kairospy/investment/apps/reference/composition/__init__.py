"""Private concrete construction for Reference application access."""

from __future__ import annotations

from kairospy.system.apps.components.application.clients import ReferenceSystemClient

from ..application.application import ReferenceApplication


def build_strategy_access(client: ReferenceSystemClient | None) -> ReferenceApplication:
    """Build Reference read access for one Strategy process."""

    return ReferenceApplication(None if client is None else client.application_client())
